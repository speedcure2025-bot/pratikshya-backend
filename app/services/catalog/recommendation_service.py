"""Bounded, deterministic ranking over real catalogue and customer signals.

No result cache: each request rechecks publication/taxonomy/stock. ProductService
owns visibility and canonical media/collection projection. An ML scorer can
replace affinity() without changing persistence or the public items contract.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import distinct, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import aliased

from app.core.exceptions import NotFoundException
from app.core.logging import get_logger
from app.models.auth.user import UserModel
from app.models.catalog.product import ProductModel
from app.models.commerce.cart import CartModel
from app.models.commerce.cart_item import CartItemModel
from app.models.commerce.wishlist import WishlistModel
from app.models.commerce.wishlist_item import WishlistItemModel
from app.models.customer.product_interaction import UserProductInteractionModel as Interaction
from app.models.orders.order import OrderModel
from app.models.orders.order_item import OrderItemModel

logger = get_logger(__name__)
RETENTION_DAYS = 180
HISTORY_LIMIT = 200
ANCHOR_LIMIT = 30
CANDIDATE_LIMIT = 1000
RESULT_LIMIT = 12
HALF_LIFE_DAYS = 30
MIN_PERSONAL_VIEWS = 2
MIN_COMPLEMENT_CUSTOMERS = 3
WEIGHTS = {"category": 12, "subcategory": 8, "fabric": 5, "material": 3,
           "occasion": 4, "colors": 2, "patterns": 2,
           "VIEW": 1, "CLICK": 2, "WISHLIST": 5, "CART_ADD": 7, "PURCHASE": 9}


def now_utc():
    return datetime.now(timezone.utc)


def tokens(value):
    return {str(v).strip().casefold() for v in (value or []) if str(v).strip()}


def affinity(source, candidate):
    """Missing attributes never match each other; different audiences never mix."""
    if not source.gender or source.gender.casefold() != (candidate.gender or "").casefold():
        return 0
    score = 0
    for field in ("category", "subcategory", "fabric", "material"):
        left, right = getattr(source, field, None), getattr(candidate, field, None)
        if left and right and left.strip().casefold() == right.strip().casefold():
            score += WEIGHTS[field]
    for field in ("occasion", "colors", "patterns"):
        score += min(len(tokens(getattr(source, field)) & tokens(getattr(candidate, field))), 3) * WEIGHTS[field]
    return score


def decay(at, now):
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return 0.5 ** (max(0, (now - at).total_seconds()) / (86400 * HALF_LIFE_DAYS))


class RecommendationService:
    def __init__(self, db, visibility=None):
        from app.services.catalog.product_service import ProductService
        self.db = db
        self.products = ProductService(db)
        self.visibility = visibility or self.products._taxonomy_visible

    async def visible(self, product_id):
        # Deliberately bypass detail cache: stale cached publication cannot authorize tracking.
        product = (await self.db.execute(select(ProductModel).where(ProductModel.id == product_id))).scalars().first()
        maps = await self.products._visibility_maps()
        if not product or product.status != "PUBLISHED" or not product.published or not self.visibility(product, *maps):
            raise NotFoundException("Product is not available.")
        return product

    async def record(self, customer_id, product_id, event_type, idempotency_key=None):
        await self.visible(product_id)
        # Also protect callers outside the customer-only HTTP dependency.
        owner = (await self.db.execute(select(UserModel.id).where(
            UserModel.id == customer_id, UserModel.user_type == "customer"))).scalar_one_or_none()
        if owner is None:
            return False
        now = now_utc()
        bucket = int(now.timestamp()) // (900 if event_type in ("VIEW", "CLICK") else 60)
        key = f"{event_type}:{product_id}:{idempotency_key or bucket}"
        try:
            async with self.db.begin_nested():
                self.db.add(Interaction(customer_id=customer_id, product_id=product_id,
                    event_type=event_type, dedup_key=key, event_bucket=bucket,
                    created_at=now, updated_at=now))
                await self.db.flush()
        except IntegrityError as exc:
            # Suppress only duplicate keys; FK/check failures must not pretend success.
            code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
            if code == "23505" or "UNIQUE constraint failed" in str(exc.orig):
                return False
            raise
        return True

    async def _project(self, products):
        media = await self.products._registered_media_map([p.id for p in products])
        collections = await self.products._collection_membership_names(products)
        return [self.products._to_storefront(p, media.get(p.id), collections.get(str(p.id), [])) for p in products]

    async def _candidates(self, anchors, *, ids=None):
        if not anchors:
            return []
        stmt = select(ProductModel).where(
            ProductModel.status == "PUBLISHED", ProductModel.published.is_(True),
            ProductModel.stock > 0, ProductModel.availability == "in-stock",
            ProductModel.id.notin_([p.id for p, _ in anchors]),
        )
        if ids is not None:
            if not ids:
                return []
            stmt = stmt.where(ProductModel.id.in_(ids))
        else:
            # Category affinity is the candidate retrieval layer. Bound the working set
            # before Python scoring, with stable recency/ID ordering.
            stmt = stmt.where(ProductModel.category.in_({p.category for p, _ in anchors}))
        rows = (await self.db.execute(stmt.order_by(ProductModel.created_at.desc(), ProductModel.id).limit(CANDIDATE_LIMIT))).scalars().all()
        maps = await self.products._visibility_maps()
        return [p for p in rows if self.visibility(p, *maps)]

    async def _complements(self, source):
        # Evidence, not "different category" guesses: bought together by >=3 distinct
        # customers in delivered, unreturned orders within the retention window.
        anchor_item = aliased(OrderItemModel)
        stmt = (select(OrderItemModel.product_id, func.count(distinct(OrderModel.customer_id)).label("buyers"))
            .join(OrderModel, OrderModel.id == OrderItemModel.order_id)
            .join(anchor_item, anchor_item.order_id == OrderModel.id)
            .where(anchor_item.product_id == source.id, OrderItemModel.product_id != source.id,
                   OrderModel.status == "DELIVERED", OrderModel.customer_id.is_not(None),
                   OrderModel.created_at >= now_utc() - timedelta(days=RETENTION_DAYS),
                   OrderItemModel.returned_quantity == 0, anchor_item.returned_quantity == 0)
            .group_by(OrderItemModel.product_id)
            .having(func.count(distinct(OrderModel.customer_id)) >= MIN_COMPLEMENT_CUSTOMERS)
            .order_by(func.count(distinct(OrderModel.customer_id)).desc(), OrderItemModel.product_id)
            .limit(CANDIDATE_LIMIT))
        return dict((await self.db.execute(stmt)).all())

    async def contextual(self, product_id, rec_type="related", limit=RESULT_LIMIT):
        source = await self.visible(product_id)
        if rec_type in ("complete-the-look", "cart"):
            evidence = await self._complements(source)
            candidates = await self._candidates([(source, 1)], ids=list(evidence))
            candidates = [p for p in candidates if p.category != source.category and p.gender == source.gender]
            candidates.sort(key=lambda p: (-evidence[p.id], -affinity(source, p), str(p.id)))
        else:
            candidates = await self._candidates([(source, 1)])
            candidates = [p for p in candidates if affinity(source, p) > 0]
            # "recommended" remains contextual, not personal: metadata affinity plus
            # a small authored new-arrival tiebreak, never fake popularity.
            candidates.sort(key=lambda p: (-(affinity(source, p) + (int(bool(p.is_new)) if rec_type == "recommended" else 0)), str(p.id)))
        return await self._project(candidates[:min(limit, RESULT_LIMIT)])

    async def personal(self, customer_id, rec_type="personalized", limit=RESULT_LIMIT):
        now = now_utc()
        cutoff = now - timedelta(days=RETENTION_DAYS)
        events = (await self.db.execute(select(Interaction).where(
            Interaction.customer_id == customer_id, Interaction.created_at >= cutoff,
            Interaction.event_type.in_(["VIEW", "CLICK"])
        ).order_by(Interaction.created_at.desc(), Interaction.id).limit(HISTORY_LIMIT))).scalars().all()
        # Max, not sum, prevents repeated clicking from overwhelming affinity.
        signals = {}
        for event in events:
            if rec_type == "because-viewed" and event.event_type != "VIEW":
                continue
            signals[event.product_id] = max(signals.get(event.product_id, 0), WEIGHTS[event.event_type] * decay(event.created_at, now))
        if rec_type == "personalized":
            for item, parent, fk, kind in (
                (WishlistItemModel, WishlistModel, WishlistItemModel.wishlist_id, "WISHLIST"),
                (CartItemModel, CartModel, CartItemModel.cart_id, "CART_ADD"),
            ):
                rows = (await self.db.execute(select(item.product_id, item.created_at).join(parent, fk == parent.id)
                    .where(parent.customer_id == customer_id, item.created_at >= cutoff)
                    .order_by(item.created_at.desc(), item.product_id).limit(ANCHOR_LIMIT))).all()
                for pid, at in rows:
                    signals[pid] = max(signals.get(pid, 0), WEIGHTS[kind] * decay(at, now))
            purchases = (await self.db.execute(select(OrderItemModel.product_id, OrderModel.created_at)
                .join(OrderModel, OrderModel.id == OrderItemModel.order_id)
                .where(OrderModel.customer_id == customer_id, OrderModel.status == "DELIVERED",
                       OrderItemModel.returned_quantity == 0, OrderModel.created_at >= cutoff)
                .order_by(OrderModel.created_at.desc(), OrderItemModel.product_id).limit(ANCHOR_LIMIT))).all()
            for pid, at in purchases:
                signals[pid] = max(signals.get(pid, 0), WEIGHTS["PURCHASE"] * decay(at, now))
        if not signals:
            return []
        rows = (await self.db.execute(select(ProductModel).where(ProductModel.id.in_(signals)))).scalars().all()
        maps = await self.products._visibility_maps()
        anchors = [(p, signals[p.id]) for p in rows if p.status == "PUBLISHED" and p.published and self.visibility(p, *maps)]
        anchors.sort(key=lambda pair: (-pair[1], str(pair[0].id)))
        if rec_type == "because-viewed":
            # Latest visible real VIEW, not highest-weight CLICK or local history.
            by_id = {p.id: p for p, _ in anchors}
            latest = next((e for e in events if e.event_type == "VIEW" and e.product_id in by_id), None)
            anchors = [(by_id[latest.product_id], 1)] if latest else []
        elif len(anchors) < MIN_PERSONAL_VIEWS and not any(w >= WEIGHTS["WISHLIST"] * 0.5 for _, w in anchors):
            return []
        anchors = anchors[:ANCHOR_LIMIT]
        candidates = await self._candidates(anchors)
        ranked = []
        for p in candidates:
            if p.id in signals:
                continue
            score = sum(affinity(anchor, p) * weight for anchor, weight in anchors)
            if score > 0:
                ranked.append((p, score))
        ranked.sort(key=lambda pair: (-pair[1], str(pair[0].id)))
        return await self._project([p for p, _ in ranked[:min(limit, RESULT_LIMIT)]])


async def record_behavior_safely(db, customer_id, product_id, event_type):
    """Optional telemetry must never abort cart/wishlist/recent-view operations.

    A nested transaction prevents a missing behavioral migration from poisoning
    the business transaction. Existing audit mutations remain untouched.
    """
    try:
        async with db.begin_nested():
            await RecommendationService(db).record(customer_id, product_id, event_type)
    except (SQLAlchemyError, NotFoundException):
        logger.warning("Behavioral interaction unavailable; business operation preserved")
