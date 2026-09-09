"""
ProductService — all business logic for the product catalogue.

Covers:
  - Storefront catalogue query with filtering, sorting, facet counts
  - Admin CRUD: create, draft, patch, change-id, duplicate, bulk
  - Workflow: submit-review, approve, reject, publish, unpublish, archive, restore
  - Assign employee
  - Availability checks (sku, slug)
  - Next stable product id
  - Publish-issue gate (getPublishIssues)
  - Pricing engine (computePricing, exactly matching frontend pricing.js)
  - Recently viewed
  - Recommendations
  - Catalogue metrics
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger("app.services.catalog.products")

from app.core.cache import (
    TTL_PRODUCT_DETAIL,
    TTL_RECENTLY_VIEWED,
    cache,
    invalidate_response_cache,
)
from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.models.catalog.category import CategoryModel, SubcategoryModel
from app.models.catalog.collection import CollectionModel
from app.models.catalog.product import ProductModel
from app.models.employee.employee import EmployeeProfileModel
from app.services.media.product_media_records import (
    gallery_urls,
    primary_item,
    registered_media_for_product,
    registered_media_for_products,
)
from app.services.media.product_media_resolver import (
    resolve_product_image_list,
    resolve_product_image_reference,
)
from app.schemas.catalog.product import (
    ADMIN_SORTS,
    EMPLOYEE_EDITABLE_FIELDS,
    LIFECYCLE_TRANSITIONS,
    PRODUCT_ID_RE,
    REVIEW_FLAG_BLOCKING,
    SORT_ALIASES,
    AdminProduct,
    AdminProductListQuery,
    AssignEmployeeRequest,
    BulkUpdateRequest,
    CatalogMetricsResponse,
    ChangeProductIdRequest,
    ClearReviewFlagsRequest,
    EmployeeProduct,
    EmployeeProductUpdateRequest,
    FacetCounts,
    FacetValue,
    ProductCreateRequest,
    ProductDraftRequest,
    ProductListQuery,
    ProductUpdateRequest,
    RejectProductRequest,
    StorefrontProduct,
)

# ── Constants ────────────────────────────────────────────────────────────────

PRICE_BANDS = [
    {"id": "under-500", "label": "Under ₹500", "min": 0, "max": 499},
    {"id": "500-1000", "label": "₹500 – ₹1,000", "min": 500, "max": 1000},
    {"id": "1000-2500", "label": "₹1,000 – ₹2,500", "min": 1001, "max": 2500},
    {"id": "2500-5000", "label": "₹2,500 – ₹5,000", "min": 2501, "max": 5000},
    {"id": "above-5000", "label": "Above ₹5,000", "min": 5001, "max": 99_999_999},
]

ALLOW_SELLING_ABOVE_MRP = False

PLACEHOLDER_NAME_PATTERNS = re.compile(
    r"^(product\s*\d*|untitled|draft|new product|placeholder|tbd|to be|temp\w*)$",
    re.IGNORECASE,
)

# Category prefix mapping (from productIdPrefixes.js)
CATEGORY_ID_PREFIXES: Dict[str, str] = {
    "sarees": "SAR",
    "lehengas": "LEH",
    "kurtis-and-suits": "KUR",
    "bridal-couture": "BRI",
    "menswear": "MEN",
    "kidswear": "KID",
    "jewellery": "JWL",
    "innerwear": "INN",
    "bangles": "BNG",
    "dupattas": "DUP",
}

RECENTLY_VIEWED_LIMIT = 20
HISTORY_CAP = 60
PRICE_HISTORY_CAP = 24


# ── Utility helpers ───────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(text: str) -> str:
    """Convert text to a URL-safe lowercase slug."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_]+", "-", text)


def _normalise_search(text: str) -> str:
    """Normalise for case/diacritic-insensitive substring search."""
    return (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode()
        .lower()
    )


def _is_placeholder_name(name: str) -> bool:
    return bool(PLACEHOLDER_NAME_PATTERNS.match(name.strip()))


def _get_price_band(price: int) -> str:
    for band in PRICE_BANDS:
        if band["min"] <= price <= band["max"]:
            return band["id"]
    return "above-5000"


# ── Pricing engine ────────────────────────────────────────────────────────────

def compute_pricing(pricing: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reimplementation of frontend computePricing() / src/utils/pricing.js.
    Returns dict with finalPrice, savings, effectiveDiscountPercent, errors[].
    """
    errors: List[str] = []

    mrp = int(pricing.get("mrp") or pricing.get("sellingPrice") or 0)
    selling = int(pricing.get("sellingPrice") or pricing.get("selling_price") or 0)
    discount_type = pricing.get("discountType") or pricing.get("discount_type") or "none"
    discount_value = float(pricing.get("discountValue") or pricing.get("discount_value") or 0)

    if mrp <= 0:
        errors.append("MRP must be greater than zero.")
    if selling <= 0:
        errors.append("Selling price must be greater than zero.")
    if mrp > 0 and selling > mrp and not ALLOW_SELLING_ABOVE_MRP:
        errors.append("Selling price cannot be above MRP.")

    if discount_type == "percentage":
        if not (0 <= discount_value <= 100):
            errors.append("Percentage discount must be between 0 and 100.")
        discount_amount = selling * discount_value / 100
    elif discount_type == "fixed":
        if discount_value < 0:
            errors.append("Fixed discount cannot be negative.")
        if discount_value > selling:
            errors.append("Fixed discount cannot exceed the selling price.")
        discount_amount = discount_value
    else:
        discount_amount = 0.0

    final_price = max(0, round(selling - discount_amount))
    if final_price < 0:
        errors.append("Final price must never be negative.")

    savings = max(0, mrp - final_price) if mrp > 0 else 0
    effective_discount = (
        round((savings / mrp) * 100, 2) if mrp > 0 and savings > 0 else 0.0
    )

    return {
        "finalPrice": final_price,
        "savings": savings,
        "effectiveDiscountPercent": effective_discount,
        "errors": errors,
    }


# ── Publish issue gate ────────────────────────────────────────────────────────

def get_publish_issues(
    product: ProductModel,
    registered_media: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """
    Publish blocker list. Empty ⇒ safe to publish.

    Originally an exact reimplementation of the frontend's getPublishIssues();
    the media branch now additionally consults the authoritative registered
    association (`media_product_media`, plan §11.1). Callers pass the
    product's registered associations (already fetched through the
    SAVEPOINT-guarded `_registered_media_items`, so a database whose Phase 7
    media migration is pending still answers `[]` and the legacy branch
    keeps working). The legacy `image` / `primary_media_id` branch is
    retained as the transitional fallback (plan §11.4 item 3).
    """
    issues: List[str] = []

    if not product.id or not product.product_id:
        issues.append("Product ID is required.")
    if not product.name or not product.name.strip():
        issues.append("Product name is required.")
    elif _is_placeholder_name(product.name):
        issues.append("Product name must be real product information, not a placeholder.")
    if not product.sku or not product.sku.strip():
        issues.append("SKU is required.")
    if not product.category or not product.category.strip():
        issues.append("Category is required.")

    pricing = product.pricing or {}
    computed = compute_pricing(pricing) if pricing else {"finalPrice": product.price, "errors": []}
    final_price = computed.get("finalPrice", product.price)
    if (product.price or 0) <= 0 and final_price <= 0:
        issues.append("Selling price must be greater than zero.")
    for err in computed.get("errors", []):
        if err not in issues:
            issues.append(err)

    has_description = (product.description or "").strip() or (product.short_description or "").strip()
    if not has_description:
        issues.append("A description is required.")

    # Media check — authored image, OR legacy primary_media_id, OR a
    # registered association with is_primary=True (the authoritative primary
    # signal; `role` text is descriptive and is deliberately not consulted,
    # and the media-set "first item" fallback does NOT count).
    has_authored_image = bool((product.image or "").strip())
    has_legacy_primary = bool(product.primary_media_id)
    has_registered_primary = any(
        item.get("isPrimary") is True for item in (registered_media or [])
    )
    if not has_authored_image and not has_legacy_primary and not has_registered_primary:
        issues.append("At least one cover image is required before publishing.")

    # Review flags — blocking subset (declared vocabulary, plan §24 step 8)
    product_flags = set(product.review_flags or [])
    active_blocking = product_flags & set(REVIEW_FLAG_BLOCKING)
    if active_blocking:
        issues.append(f"Review flags must be resolved before publishing: {', '.join(sorted(active_blocking))}.")

    return issues


# ── Service class ─────────────────────────────────────────────────────────────

class ProductService:
    """Business logic for the product catalogue."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_or_404(self, product_id: str) -> ProductModel:
        """
        Fetch by permanent id, stable display label (`product_id`, kept in
        sync by the change-id action) or slug; raises NotFoundException if
        missing.
        """
        stmt = select(ProductModel).where(
            or_(
                ProductModel.id == product_id,
                ProductModel.slug == product_id,
                ProductModel.product_id == product_id,
            )
        )
        result = await self.db.execute(stmt)
        product = result.scalars().first()
        if not product:
            raise NotFoundException(f"Product '{product_id}' not found.")
        return product

    def _append_history(
        self, product: ProductModel, field: str, from_val: Any, to_val: Any, actor: str
    ) -> None:
        history = list(product.history or [])
        if len(history) >= HISTORY_CAP:
            history = history[-(HISTORY_CAP - 1):]
        history.append(
            {"field": field, "from": from_val, "to": to_val, "actor": actor, "at": _now_utc().isoformat()}
        )
        product.history = history

    def _append_price_history(self, product: ProductModel, from_price: int, to_price: int, actor: str) -> None:
        ph = list(product.price_history or [])
        if len(ph) >= PRICE_HISTORY_CAP:
            ph = ph[-(PRICE_HISTORY_CAP - 1):]
        ph.append({"from": from_price, "to": to_price, "actor": actor, "at": _now_utc().isoformat()})
        product.price_history = ph

    # ── Registered product-media read model (Phase 7) ────────────────────────

    async def _registered_media_map(
        self, product_ids: List[str]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Registered product ↔ media associations for the given products.

        Migration-safe: when a database has not yet run the Phase 7 media
        migration, the media tables do not exist and the join would raise.
        That state keeps dual-read OFF (empty map → legacy columns serve
        exactly as before) instead of breaking every product read on the
        pre-migration database. The failed SELECT runs inside a SAVEPOINT so
        the surrounding request transaction stays healthy. (Session doubles
        without SAVEPOINT support run the read directly; a mis-shaped answer
        is treated the same way — unavailable, never fatal to the product
        read itself.)
        """
        try:
            begin_nested = getattr(self.db, "begin_nested", None)
            if begin_nested is not None:
                async with begin_nested():
                    return await registered_media_for_products(self.db, product_ids)
            return await registered_media_for_products(self.db, product_ids)
        except (SQLAlchemyError, TypeError, ValueError, AttributeError):
            logger.warning(
                "Registered media read unavailable (Phase 7 migration pending?) — "
                "falling back to legacy product image columns.",
                exc_info=True,
            )
            return {}

    async def _registered_media_items(self, product_id: str) -> List[Dict[str, Any]]:
        try:
            begin_nested = getattr(self.db, "begin_nested", None)
            if begin_nested is not None:
                async with begin_nested():
                    return await registered_media_for_product(self.db, product_id)
            return await registered_media_for_product(self.db, product_id)
        except (SQLAlchemyError, TypeError, ValueError, AttributeError):
            logger.warning(
                "Registered media read unavailable (Phase 7 migration pending?) — "
                "falling back to legacy product image columns.",
                exc_info=True,
            )
            return []

    @staticmethod
    def _registered_media_view(registered: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Collapse an ordered registered-media list into the fields the product
        projections expose. The caller decides between this view and the
        product's legacy columns (dual-read): an empty `registered` list
        means the product has no Phase 7 associations and is left untouched.
        """
        if not registered:
            return {}
        primary = primary_item(registered)
        return {
            "image": primary["url"] if primary else "",
            "additionalImages": gallery_urls(registered),
            "primaryMediaId": primary["mediaId"] if primary else None,
            "mediaIds": [item["mediaId"] for item in registered],
            "galleryMediaIds": [
                item["mediaId"] for item in registered if not item.get("isPrimary")
            ],
        }

    async def _collection_membership_names(
        self, products: List[ProductModel]
    ) -> Dict[str, List[str]]:
        """Resolve collection-owned membership into product response labels.

        The legacy product labels remain available as a read fallback for the
        collection resolver, but they are no longer write authority. This
        batch helper keeps product projections portable across the SQLite test
        harness and PostgreSQL by evaluating the existing JSONB arrays in
        Python rather than adding an association table or migration.
        """
        names: Dict[str, List[str]] = {str(product.id): [] for product in products}
        if not products:
            return names

        try:
            result = await self.db.execute(select(CollectionModel))
            collections = result.scalars().all()
        except (SQLAlchemyError, AttributeError, TypeError):
            logger.warning("Collection membership read unavailable; using legacy labels.", exc_info=True)
            return names

        for collection in collections:
            collection_id = getattr(collection, "id", None)
            collection_name = getattr(collection, "name", None)
            if not collection_id or not collection_name:
                continue
            collection_id = str(collection_id)
            collection_name = str(collection_name)
            collection_type = getattr(collection, "type", "MANUAL")
            rule = getattr(collection, "rule", None) or {}
            explicit_ids = {
                str(value) for value in (getattr(collection, "explicit_product_ids", None) or [])
            }

            for product in products:
                member = str(product.id) in explicit_ids
                if not member and collection_type == "RULE_BASED":
                    if product.status != "PUBLISHED" or not product.published:
                        continue
                    flag = rule.get("flag")
                    occasion = rule.get("occasion")
                    fabric_includes = rule.get("fabricIncludes")
                    if flag and not (product.flags or {}).get(flag):
                        continue
                    if occasion and occasion not in (product.occasion or []):
                        continue
                    if fabric_includes and fabric_includes.lower() not in (product.fabric or "").lower():
                        continue
                    member = True
                if not member and collection_type != "RULE_BASED":
                    legacy_labels = [
                        str(value).lower()
                        for value in (product.collections or [])
                    ]
                    legacy_scalar = str(product.collection or "").lower()
                    needle_name = collection_name.lower()
                    needle_id = collection_id.lower()
                    member = (
                        needle_name in legacy_scalar
                        or needle_id in legacy_scalar
                        or needle_name in legacy_labels
                        or needle_id in legacy_labels
                    )
                if member:
                    product_names = names[str(product.id)]
                    if collection_name not in product_names:
                        product_names.append(collection_name)
        return names

    def _to_storefront(
        self, p: ProductModel, registered_media: Optional[List[Dict[str, Any]]] = None,
        collection_names: Optional[List[str]] = None,
    ) -> StorefrontProduct:
        """Project a ProductModel onto a StorefrontProduct DTO."""
        pricing = p.pricing or {}
        computed = compute_pricing(pricing) if pricing else {}
        final_price = computed.get("finalPrice", p.price)
        effective_discount = computed.get("effectiveDiscountPercent", 0.0)
        original_price = p.original_price if (p.original_price and p.original_price > final_price) else None
        media_view = self._registered_media_view(registered_media or [])
        return StorefrontProduct(
            id=p.id,
            productId=p.product_id or p.id,
            name=p.name or "",
            slug=p.slug or "",
            sku=p.sku or "",
            brand=p.brand or "Pratikshya Fashon",
            productType=p.product_type or "fashion",
            category=p.category or "",
            subcategory=p.subcategory or "",
            gender=p.gender or "Women",
            shortDescription=p.short_description or "",
            description=p.description or "",
            highlights=p.highlights or [],
            careInstructions=p.care_instructions or [],
            deliveryInfo=p.delivery_info or "",
            returnInfo=p.return_info or "",
            returnPolicy=getattr(p, "return_policy", None) or None,
            fabric=p.fabric or "",
            material=p.material or "",
            primaryColor=p.primary_color or "",
            secondaryColor=p.secondary_color or "",
            colors=p.colors or [],
            patterns=p.patterns or [],
            occasion=p.occasion or [],
            sizes=p.sizes or [],
            unavailableColors=p.unavailable_colors or [],
            unavailableSizes=p.unavailable_sizes or [],
            season=p.season or "",
            fit=p.fit or "",
            length=p.length or "",
            # `collection` is the retained legacy scalar. The plural field is
            # resolved from collection-owned membership when available.
            collection=p.collection or "",
            collections=collection_names if collection_names is not None else (p.collections or []),
            tags=p.tags or [],
            badges=p.badges or [],
            isFeatured=bool(p.is_featured),
            isBestseller=bool(p.is_bestseller),
            isNew=bool(p.is_new),
            isLimitedEdition=bool(p.is_limited_edition),
            isTrending=bool(p.is_trending),
            price=final_price,
            originalPrice=original_price,
            currency=p.currency or "INR",
            discountPercent=effective_discount,
            isOnSale=effective_discount > 0,
            stock=p.stock or 0,
            availability=p.availability or "in-stock",
            rating=float(p.rating) if p.rating else None,
            reviewCount=p.review_count or 0,
            # Media references are resolved by the storage layer so the
            # frontend receives a canonical media URL. Registered Phase 7
            # associations (when any exist) are the source of truth for NEW
            # media; the legacy authored columns remain the dual-read
            # fallback. See app/services/media/product_media_resolver.py.
            image=media_view.get("image") or resolve_product_image_reference(p.image),
            hoverImage=resolve_product_image_reference(p.hover_image),
            additionalImages=media_view.get("additionalImages")
            if media_view
            else resolve_product_image_list(p.additional_images),
            primaryMediaId=media_view.get("primaryMediaId", p.primary_media_id),
            href=f"/products/{p.slug or p.id}",
            status=p.status,
        )

    async def _to_admin_current(self, p: ProductModel) -> AdminProduct:
        """
        Single-record admin projection with the Phase 7 registered-media
        read model and collection-owned membership resolved.
        """
        collection_names = await self._collection_membership_names([p])
        return self._to_admin(
            p,
            await self._registered_media_items(p.id),
            collection_names.get(str(p.id), []),
        )

    async def _to_employee_current(self, p: ProductModel) -> EmployeeProduct:
        """Single-record employee projection with authoritative reads resolved."""
        collection_names = await self._collection_membership_names([p])
        return self._to_employee(
            p,
            await self._registered_media_items(p.id),
            collection_names.get(str(p.id), []),
        )

    def _to_admin(
        self,
        p: ProductModel,
        registered_media: Optional[List[Dict[str, Any]]] = None,
        collection_names: Optional[List[str]] = None,
    ) -> AdminProduct:
        """Project a ProductModel onto the full AdminProduct DTO."""
        media_view = self._registered_media_view(registered_media or [])
        return AdminProduct(
            id=p.id,
            productId=p.product_id or p.id,
            name=p.name or "",
            slug=p.slug or "",
            sku=p.sku or "",
            brand=p.brand or "Pratikshya Fashon",
            productType=p.product_type or "fashion",
            productCode=p.product_code or "",
            barcode=p.barcode or "",
            internalReference=p.internal_reference or "",
            category=p.category or "",
            subcategory=p.subcategory or "",
            gender=p.gender or "Women",
            shortDescription=p.short_description or "",
            description=p.description or "",
            highlights=p.highlights or [],
            specifications=p.specifications or {},
            careInstructions=p.care_instructions or [],
            deliveryInfo=p.delivery_info or "",
            returnInfo=p.return_info or "",
            returnPolicy=getattr(p, "return_policy", None) or None,
            fabric=p.fabric or "",
            material=p.material or "",
            primaryColor=p.primary_color or "",
            secondaryColor=p.secondary_color or "",
            colors=p.colors or [],
            patterns=p.patterns or [],
            work=p.work or [],
            occasion=p.occasion or [],
            sizes=p.sizes or [],
            unavailableColors=p.unavailable_colors or [],
            unavailableSizes=p.unavailable_sizes or [],
            season=p.season or "",
            fit=p.fit or "",
            length=p.length or "",
            collection=p.collection or "",
            collections=collection_names if collection_names is not None else (p.collections or []),
            tags=p.tags or [],
            badges=p.badges or [],
            isFeatured=bool(p.is_featured),
            isBestseller=bool(p.is_bestseller),
            isNew=bool(p.is_new),
            isLimitedEdition=bool(p.is_limited_edition),
            isTrending=bool(p.is_trending),
            flags=p.flags or {},
            price=p.price or 0,
            originalPrice=p.original_price,
            compareAtPrice=p.compare_at_price,
            currency=p.currency or "INR",
            pricing=p.pricing,
            priceHistory=p.price_history or [],
            stock=p.stock or 0,
            availability=p.availability or "in-stock",
            inventoryTracked=bool(p.inventory_tracked),
            lowStockThreshold=p.low_stock_threshold or 5,
            rating=float(p.rating) if p.rating else None,
            reviewCount=p.review_count or 0,
            seo=p.seo,
            status=p.status,
            published=bool(p.published),
            review=p.review,
            reviewFlags=p.review_flags or [],
            assignedEmployeeId=p.assigned_employee_id,
            mediaIds=media_view.get("mediaIds", p.media_ids or []),
            primaryMediaId=media_view.get("primaryMediaId", p.primary_media_id),
            galleryMediaIds=media_view.get("galleryMediaIds", p.gallery_media_ids or []),
            image=media_view.get("image") or resolve_product_image_reference(p.image),
            hoverImage=resolve_product_image_reference(p.hover_image),
            additionalImages=media_view.get("additionalImages")
            if media_view
            else resolve_product_image_list(p.additional_images),
            createdBy=p.created_by,
            createdAt=p.created_at.isoformat() if p.created_at else None,
            updatedBy=p.updated_by,
            updatedAt=p.updated_at.isoformat() if p.updated_at else None,
            publishedBy=p.published_by,
            publishedAt=p.published_at.isoformat() if p.published_at else None,
            history=p.history or [],
        )

    def _to_employee(
        self,
        p: ProductModel,
        registered_media: Optional[List[Dict[str, Any]]] = None,
        collection_names: Optional[List[str]] = None,
    ) -> EmployeeProduct:
        """Project a product without admin-only workflow/audit fields."""
        admin_payload = self._to_admin(p, registered_media, collection_names).model_dump(
            by_alias=True
        )
        return EmployeeProduct.model_validate(admin_payload)

    async def _category_status_map(self) -> Dict[str, str]:
        """Map category id/slug/name to status for storefront visibility."""
        result = await self.db.execute(select(CategoryModel))
        rows = result.scalars().all()
        status_map: Dict[str, str] = {}
        for category in rows:
            if category.id:
                status_map[category.id] = category.status
            if category.slug:
                status_map[category.slug] = category.status
            if category.name:
                status_map[category.name] = category.status
        return status_map

    async def _subcategory_status_map(self) -> Dict[str, str]:
        """
        Map subcategory id/slug/name to status for storefront visibility.

        Deliberately the exact mirror of `_category_status_map` — same key
        triple, same shape — because PHASE_3_PRODUCT_CATALOG_IMPLEMENTATION_PLAN.md
        §10.4(1) asks for *parity* between the two levels, and because
        `catalog_product.subcategory` is the same untyped `String(100)`
        reference column as `catalog_product.category`.

        Ambiguity note: subcategory slugs/names are only unique WITHIN a
        category, so if two categories own a same-named subcategory with
        different statuses this flat map keeps the last row scanned. Block 2
        (API-204) normalises every NEW write to the canonical row id, so the
        ambiguity can only ever affect legacy rows that still carry a slug or
        a name. Resolving it properly needs the category-scoped pair, which is
        what `_resolve_taxonomy` already enforces on the write path.
        """
        result = await self.db.execute(select(SubcategoryModel))
        rows = result.scalars().all()
        status_map: Dict[str, str] = {}
        for subcategory in rows:
            if subcategory.id:
                status_map[subcategory.id] = subcategory.status
            if subcategory.slug:
                status_map[subcategory.slug] = subcategory.status
            if subcategory.name:
                status_map[subcategory.name] = subcategory.status
        return status_map

    # ── The storefront visibility gate (Phase 3 §10 — API-180 / PF3-N06) ──────
    #
    # ONE predicate, used by every public read path, so `GET /products`,
    # `GET /products/{id}`, `/explore`, `/search`, `/categories/{id}/products`,
    # `/collections/{id}/products`, recommendations and recently-viewed can
    # never disagree about a row (plan §25 acceptance criterion 13). Before
    # this block the same expression was hand-copied into four places.
    #
    # A product is publicly visible when ALL of:
    #   1. `status == "PUBLISHED"`            — asserted by each caller's query
    #   2. `published IS TRUE`                — asserted by each caller's query
    #   3. its category is not a KNOWN non-ACTIVE `catalog_category`
    #   4. its subcategory (when set) is not a KNOWN non-ACTIVE
    #      `catalog_subcategory`                              ← NEW (PF3-N06)
    #
    # Rules 3 and 4 both FAIL OPEN on a reference that resolves to no taxonomy
    # row. That default is deliberate and is NOT an oversight: flipping it to
    # fail-closed is PF3-N07, and plan §24 step 7 admits it "only after the
    # step 0 report is reviewed", with §23 R1 ("Never flip the default blind")
    # rating it the single highest regression risk in Phase 3. The step 0
    # reconciliation (`SELECT DISTINCT category` over the real catalogue)
    # needs a PostgreSQL server, which this environment does not have
    # (plan Appendix B). See PHASE_3_BLOCK_5_IMPLEMENTATION_REPORT.md §23.

    @staticmethod
    def _taxonomy_visible(
        product: ProductModel,
        category_status_map: Dict[str, str],
        subcategory_status_map: Dict[str, str],
    ) -> bool:
        """True when the product's taxonomy references do not hide it."""
        if category_status_map.get(product.category, "ACTIVE") != "ACTIVE":
            return False
        subcategory = (product.subcategory or "").strip()
        if subcategory and subcategory_status_map.get(subcategory, "ACTIVE") != "ACTIVE":
            return False
        return True

    async def _visibility_maps(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Load both taxonomy status maps for one storefront read."""
        return await self._category_status_map(), await self._subcategory_status_map()

    # ── Product ↔ taxonomy contract (Phase 3 — API-204) ───────────────────────
    #
    # `catalog_product.category` / `.subcategory` are plain String(100) columns
    # with NO foreign key (deliberately — Phase 3 is migration-free), so the
    # ONLY place the reference can be enforced is here, on the write path.
    #
    # Rules, taken from PHASE_3_PRODUCT_CATALOG_IMPLEMENTATION_PLAN.md §12.3,
    # §16.2 and §22.1:
    #   1. An incoming value is resolved against `catalog_category` /
    #      `catalog_subcategory` by id, then slug, then name (the same triple
    #      `_category_status_map` already keys on).
    #   2. A value that resolves to nothing is rejected — never stored.
    #   3. A taxonomy node may only be ASSIGNED while it is ACTIVE; DRAFT and
    #      ARCHIVED nodes are rejected (§16.2 "Inactive category assigned").
    #      The rule applies to the field being WRITTEN: a patch that does not
    #      touch a field cannot be failed by that field's status.
    #   4. The subcategory must belong to the resulting category — the pair is
    #      validated, not merely the existence of two ids.
    #   5. What is stored is the canonical row id, so the visibility gate, the
    #      admin filter and the editor's selects all key on one vocabulary.
    #
    # Every rejection is HTTP 422 `VALIDATION_ERROR` in the Phase 1 envelope,
    # with FastAPI-shaped field details, so the admin UI renders it exactly
    # like a schema rejection.

    ASSIGNABLE_TAXONOMY_STATUS = "ACTIVE"

    @staticmethod
    def _taxonomy_rejection(
        field: str, message: str, error_type: str, value: Any
    ) -> ValidationException:
        """Canonical 422 for one taxonomy field."""
        return ValidationException(
            message=message,
            details=[
                {
                    "loc": ["body", field],
                    "field": field,
                    "msg": message,
                    "type": error_type,
                    "input": value,
                }
            ],
        )

    async def _lookup_category(self, value: str) -> Optional[CategoryModel]:
        """Resolve a category reference by id, then slug, then name."""
        rows = (
            await self.db.execute(
                select(CategoryModel).where(
                    or_(
                        CategoryModel.id == value,
                        CategoryModel.slug == value,
                        CategoryModel.name == value,
                    )
                )
            )
        ).scalars().all()
        for attribute in ("id", "slug", "name"):
            for row in rows:
                if getattr(row, attribute, None) == value:
                    return row
        return None

    async def _lookup_subcategories(self, value: str) -> List[SubcategoryModel]:
        """Every subcategory whose id, slug or name matches (any category)."""
        rows = (
            await self.db.execute(
                select(SubcategoryModel).where(
                    or_(
                        SubcategoryModel.id == value,
                        SubcategoryModel.slug == value,
                        SubcategoryModel.name == value,
                    )
                )
            )
        ).scalars().all()
        return [row for row in rows]

    @staticmethod
    def _pick_subcategory(
        rows: List[SubcategoryModel], value: str, category_id: str
    ) -> Optional[SubcategoryModel]:
        """The id/slug/name match that actually belongs to `category_id`."""
        owned = [row for row in rows if getattr(row, "category_id", None) == category_id]
        for attribute in ("id", "slug", "name"):
            for row in owned:
                if getattr(row, attribute, None) == value:
                    return row
        return None

    async def _resolve_taxonomy(
        self,
        data: Dict[str, Any],
        current_category: Optional[str] = None,
        current_subcategory: Optional[str] = None,
    ) -> None:
        """
        Validate — and canonicalise in place — the taxonomy of one write.

        `data` carries only the explicitly-set request fields (`exclude_unset`),
        so PATCH semantics are preserved: a field that is absent is never
        validated, never written and never turned into a PUT. The values that
        are absent are read from the stored record (`current_*`) purely to
        validate the RESULTING pair.
        """
        category_supplied = "category" in data
        subcategory_supplied = "subcategory" in data
        if not category_supplied and not subcategory_supplied:
            return

        raw_category = data.get("category") if category_supplied else current_category
        raw_subcategory = (
            data.get("subcategory") if subcategory_supplied else current_subcategory
        )
        category_value = str(raw_category or "").strip()
        subcategory_value = str(raw_subcategory or "").strip()

        # A write that only CLEARS the subcategory and leaves the category
        # untouched assigns nothing, so there is no pair to validate. Without
        # this, a legacy free-text category on an old row would trap the clear.
        if not category_supplied and not subcategory_value:
            return

        # Report against the field the caller actually sent, so the UI can
        # point at an input the operator can fix.
        category_field = "category" if category_supplied else "subcategory"
        subcategory_field = "subcategory" if subcategory_supplied else "category"

        category_row: Optional[CategoryModel] = None
        if category_value:
            category_row = await self._lookup_category(category_value)
            if category_row is None:
                raise self._taxonomy_rejection(
                    category_field,
                    f"Unknown category '{category_value}'.",
                    "value_error.taxonomy.unknown_category",
                    category_value,
                )
            if (
                category_supplied
                and category_row.status != self.ASSIGNABLE_TAXONOMY_STATUS
            ):
                raise self._taxonomy_rejection(
                    category_field,
                    f"Category '{category_row.name}' is {category_row.status} "
                    f"and cannot be assigned to a product.",
                    "value_error.taxonomy.category_status",
                    category_value,
                )
            if category_supplied:
                data["category"] = category_row.id

        if not subcategory_value:
            return

        if category_row is None:
            raise self._taxonomy_rejection(
                subcategory_field,
                f"Subcategory '{subcategory_value}' cannot be assigned without "
                f"a category.",
                "value_error.taxonomy.subcategory_without_category",
                subcategory_value,
            )

        candidates = await self._lookup_subcategories(subcategory_value)
        if not candidates:
            raise self._taxonomy_rejection(
                subcategory_field,
                f"Unknown subcategory '{subcategory_value}' for category "
                f"'{category_row.name}'.",
                "value_error.taxonomy.unknown_subcategory",
                subcategory_value,
            )

        subcategory_row = self._pick_subcategory(
            candidates, subcategory_value, category_row.id
        )
        if subcategory_row is None:
            raise self._taxonomy_rejection(
                subcategory_field,
                f"Subcategory '{subcategory_value}' does not belong to category "
                f"'{category_row.name}'.",
                "value_error.taxonomy.subcategory_category_mismatch",
                subcategory_value,
            )

        if (
            subcategory_supplied
            and subcategory_row.status != self.ASSIGNABLE_TAXONOMY_STATUS
        ):
            raise self._taxonomy_rejection(
                subcategory_field,
                f"Subcategory '{subcategory_row.name}' is {subcategory_row.status} "
                f"and cannot be assigned to a product.",
                "value_error.taxonomy.subcategory_status",
                subcategory_value,
            )

        if subcategory_supplied:
            data["subcategory"] = subcategory_row.id

    # ── SKU / slug uniqueness (Phase 3 — PF3-N03 / PF3-N04) ──────────────────
    #
    # `ix_catalog_product_sku` and `ix_catalog_product_slug` are NON-unique
    # indexes (`597f883749d8:115-116`). Plan §19 states the constraint (and the
    # de-duplication pass it needs) is deliberately SEPARATED into Phase 4, and
    # that Phase 3 enforces uniqueness "at the service layer — a SELECT before
    # insert. No constraint needed." That is what these helpers do; the
    # concurrency limitation is documented in API_CONTRACT.md §9.4.
    #
    # Normalisation (plan §22.1 "case/whitespace normalisation defined and
    # tested"): surrounding whitespace is stripped, and the collision test is
    # CASE-INSENSITIVE — `pf-sar-0001` and `PF-SAR-0001` are the same identity.
    # The caller's own casing is preserved in storage; only the comparison is
    # folded, so nothing is silently rewritten.

    @staticmethod
    def _normalise_identity(value: Any) -> str:
        """Trim an identity value; `None`/blank collapse to ""."""
        return str(value or "").strip()

    async def _product_with_sku(
        self, sku: str, exclude_id: Optional[str] = None
    ) -> Optional[ProductModel]:
        stmt = select(ProductModel).where(func.lower(ProductModel.sku) == sku.lower())
        if exclude_id:
            stmt = stmt.where(ProductModel.id != exclude_id)
        return (await self.db.execute(stmt)).scalars().first()

    async def _product_with_slug(
        self, slug: str, exclude_id: Optional[str] = None
    ) -> Optional[ProductModel]:
        stmt = select(ProductModel).where(func.lower(ProductModel.slug) == slug.lower())
        if exclude_id:
            stmt = stmt.where(ProductModel.id != exclude_id)
        return (await self.db.execute(stmt)).scalars().first()

    async def _assert_sku_available(
        self, sku: str, exclude_id: Optional[str] = None
    ) -> str:
        """A taken SKU is a 409 — never a silent accept (plan §16.2)."""
        if await self._product_with_sku(sku, exclude_id=exclude_id):
            raise ConflictException(
                f"SKU '{sku}' is already in use.",
                details={"field": "sku", "value": sku},
            )
        return sku

    async def _assert_slug_available(
        self, slug: str, exclude_id: Optional[str] = None
    ) -> str:
        """
        A taken slug is a 409 carrying a deterministic `suggestedSlug` — never
        a silent `-1` rename (plan §16.2, §25.7, R3).
        """
        if await self._product_with_slug(slug, exclude_id=exclude_id):
            suggested = await self._generate_unique_slug(
                slug, base=True, exclude_id=exclude_id
            )
            raise ConflictException(
                f"Slug '{slug}' is already in use.",
                details={"field": "slug", "value": slug, "suggestedSlug": suggested},
            )
        return slug

    # ── Public storefront catalogue ───────────────────────────────────────────

    async def list_storefront_products(
        self,
        query: ProductListQuery,
        category_status_map: Optional[Dict[str, str]] = None,
        subcategory_status_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        GET /products — apply visibility gate + filters + facets + sort + pagination.

        Visibility gate: status PUBLISHED, published=True, category ACTIVE and
        subcategory (when set) ACTIVE — see `_taxonomy_visible`. This one
        method also backs `/explore`, `/search`, `/categories/{id}/products`
        and `/collections/{id}/products`, so the gate is applied once for all
        of them.
        """
        stmt = select(ProductModel).where(
            ProductModel.status == "PUBLISHED",
            ProductModel.published.is_(True),
        )
        result = await self.db.execute(stmt)
        all_products = list(result.scalars().all())

        # Apply the taxonomy-active filter. General storefront/search/explore
        # callers do not pass maps, so the service loads them from the existing
        # category/subcategory tables. Unknown legacy references remain visible
        # for backward compatibility (PF3-N07, deferred — see
        # `_taxonomy_visible`); known inactive/archived nodes are hidden at
        # BOTH levels.
        if category_status_map is None:
            category_status_map = await self._category_status_map()
        if subcategory_status_map is None:
            subcategory_status_map = await self._subcategory_status_map()
        if category_status_map or subcategory_status_map:
            all_products = [
                p for p in all_products
                if self._taxonomy_visible(p, category_status_map, subcategory_status_map)
            ]

        # When called from GET /collections/{id}/products the router pre-resolves
        # membership and passes the id list via _collection_product_ids.
        # Restrict the working set to only those products.
        _coll_ids = getattr(query, "collection_product_ids", None)
        if _coll_ids is not None:
            allowed = set(_coll_ids)
            all_products = [p for p in all_products if p.id in allowed]

        # Registered product-media associations (Phase 7 source of truth for
        # new media), bulk-loaded in ONE query for the whole working set —
        # products without associations keep their legacy columns untouched.
        registered_map = await self._registered_media_map([p.id for p in all_products])
        collection_map = await self._collection_membership_names(all_products)

        # Convert to storefront DTOs for in-memory filtering. Collection
        # membership is read from collection-owned data; the legacy scalar is
        # retained separately for backwards-compatible facet/search matching.
        items = [
            self._to_storefront(
                p,
                registered_map.get(p.id),
                collection_map.get(str(p.id), []),
            )
            for p in all_products
        ]

        # Apply search
        if query.q:
            norm_q = _normalise_search(query.q)
            items = [
                it for it in items
                if any(
                    norm_q in _normalise_search(str(v))
                    for v in [
                        it.name, it.brand, it.category, it.subcategory,
                        it.fabric, it.material, " ".join(it.colors),
                        " ".join(it.occasion), " ".join(it.tags),
                        it.collection, it.sku,
                    ]
                )
            ]

        # Apply facet filters (AND across facets, OR within)
        def _to_list(v) -> List[str]:
            if v is None:
                return []
            if isinstance(v, list):
                return [str(x) for x in v]
            return [str(v)]

        def _matches_facet(field_val: Any, filter_vals: List[str]) -> bool:
            if not filter_vals:
                return True
            if isinstance(field_val, list):
                return any(str(fv).lower() in [str(v).lower() for v in field_val] for fv in filter_vals)
            return str(field_val).lower() in [str(fv).lower() for fv in filter_vals]

        def _matches_price_band(price: int, band_ids: List[str]) -> bool:
            if not band_ids:
                return True
            for band_id in band_ids:
                band = next((b for b in PRICE_BANDS if b["id"] == band_id), None)
                if band and band["min"] <= price <= band["max"]:
                    return True
            return False

        filters = {
            "category": _to_list(query.category),
            "subcategory": _to_list(query.subcategory),
            "gender": _to_list(query.gender),
            "size": _to_list(query.size),
            "color": _to_list(query.color),
            "fabric": _to_list(query.fabric),
            "material": _to_list(query.material),
            "occasion": _to_list(query.occasion),
            "collection": _to_list(query.collection),
            "availability": _to_list(query.availability),
        }

        price_filters = _to_list(query.price)
        rating_filters = _to_list(query.rating)

        def _product_matches(it: StorefrontProduct) -> bool:
            return (
                _matches_facet(it.category, filters["category"])
                and _matches_facet(it.subcategory, filters["subcategory"])
                and _matches_facet(it.gender, filters["gender"])
                and _matches_facet(it.sizes, filters["size"])
                and _matches_facet(it.colors, filters["color"])
                and _matches_facet(it.fabric, filters["fabric"])
                and _matches_facet(it.material, filters["material"])
                and _matches_facet(it.occasion, filters["occasion"])
                and _matches_facet(it.collections + ([it.collection] if it.collection else []), filters["collection"])
                and _matches_facet(it.availability, filters["availability"])
                and _matches_price_band(it.price, price_filters)
                and (
                    not rating_filters
                    or (it.rating is not None and any(it.rating >= float(r) for r in rating_filters))
                )
            )

        filtered = [it for it in items if _product_matches(it)]

        # Build facets (counts against other applied filters)
        facets = self._build_facets(items, filtered, filters, price_filters, rating_filters)

        # Sort
        filtered = self._sort_products(filtered, query.sort)

        # Paginate
        total = len(filtered)
        page_size = max(1, query.page_size)
        page = max(1, query.page)
        offset = (page - 1) * page_size
        page_items = filtered[offset: offset + page_size]

        applied_filters = {k: v for k, v in filters.items() if v}
        if price_filters:
            applied_filters["price"] = price_filters

        return {
            "ok": True,
            "items": page_items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "facets": facets,
            "appliedFilters": applied_filters,
        }

    def _build_facets(
        self,
        all_items: List[StorefrontProduct],
        filtered: List[StorefrontProduct],
        active_filters: Dict[str, List[str]],
        price_filters: List[str],
        rating_filters: List[str],
    ) -> FacetCounts:
        """
        Build facet counts — each facet counted against the other applied filters.
        Counts reflect what would remain if only that facet was deselected.
        """

        def _count_facet(
            facet_key: str,
            get_vals,
            items_without_this_facet: List[StorefrontProduct],
        ) -> List[FacetValue]:
            counts: Dict[str, int] = {}
            for it in items_without_this_facet:
                vals = get_vals(it)
                if isinstance(vals, list):
                    for v in vals:
                        counts[v] = counts.get(v, 0) + 1
                elif vals:
                    counts[str(vals)] = counts.get(str(vals), 0) + 1
            return [
                FacetValue(value=k, label=k, count=v)
                for k, v in sorted(counts.items(), key=lambda x: -x[1])
            ]

        def _items_excl(facet_key: str) -> List[StorefrontProduct]:
            """All items passing every filter EXCEPT the given facet."""
            excl_filters = {k: v for k, v in active_filters.items() if k != facet_key}
            excl_price = price_filters if facet_key != "price" else []
            excl_rating = rating_filters if facet_key != "rating" else []

            def _matches(it: StorefrontProduct) -> bool:
                for fk, fv in excl_filters.items():
                    if not fv:
                        continue
                    field = getattr(it, fk, None)
                    if fk == "size":
                        field = it.sizes
                    elif fk == "color":
                        field = it.colors
                    elif fk == "occasion":
                        field = it.occasion
                    elif fk == "collection":
                        field = it.collections + ([it.collection] if it.collection else [])
                    if isinstance(field, list):
                        if not any(str(f).lower() in [str(v).lower() for v in field] for f in fv):
                            return False
                    else:
                        if str(field or "").lower() not in [str(f).lower() for f in fv]:
                            return False
                if excl_price:
                    if not any(
                        b["min"] <= it.price <= b["max"]
                        for bid in excl_price
                        for b in PRICE_BANDS
                        if b["id"] == bid
                    ):
                        return False
                if excl_rating:
                    if it.rating is None or not any(it.rating >= float(r) for r in excl_rating):
                        return False
                return True

            return [it for it in all_items if _matches(it)]

        return FacetCounts(
            category=_count_facet("category", lambda it: it.category, _items_excl("category")),
            subcategory=_count_facet("subcategory", lambda it: it.subcategory, _items_excl("subcategory")),
            gender=_count_facet("gender", lambda it: it.gender, _items_excl("gender")),
            price=_count_facet(
                "price",
                lambda it: _get_price_band(it.price),
                _items_excl("price"),
            ),
            size=_count_facet("size", lambda it: it.sizes, _items_excl("size")),
            color=_count_facet("color", lambda it: it.colors, _items_excl("color")),
            fabric=_count_facet("fabric", lambda it: it.fabric, _items_excl("fabric")),
            material=_count_facet("material", lambda it: it.material, _items_excl("material")),
            occasion=_count_facet("occasion", lambda it: it.occasion, _items_excl("occasion")),
            collection=_count_facet(
                "collection",
                lambda it: it.collections + ([it.collection] if it.collection else []),
                _items_excl("collection"),
            ),
            rating=_count_facet(
                "rating",
                lambda it: str(int(it.rating)) if it.rating else None,
                _items_excl("rating"),
            ),
            availability=_count_facet("availability", lambda it: it.availability, _items_excl("availability")),
        )

    def _sort_products(self, items: List[StorefrontProduct], sort: str) -> List[StorefrontProduct]:
        """Sort products exactly as resolveSort / sortProducts in the frontend."""
        sort = SORT_ALIASES.get(sort, sort)
        if sort == "newest":
            return sorted(items, key=lambda it: it.id, reverse=True)
        elif sort == "price-asc":
            return sorted(items, key=lambda it: (it.price, it.id))
        elif sort == "price-desc":
            return sorted(items, key=lambda it: (-it.price, it.id))
        elif sort == "discount":
            return sorted(items, key=lambda it: (-it.discount_percent, it.id))
        elif sort == "name-asc":
            return sorted(items, key=lambda it: (it.name.lower(), it.id))
        elif sort == "popularity":
            return sorted(items, key=lambda it: (-(it.review_count or 0), it.id))
        elif sort == "rating":
            return sorted(items, key=lambda it: (-(it.rating or 0), it.id))
        else:  # recommended (default)
            return sorted(items, key=lambda it: it.id)

    # ── Get single product (storefront) ──────────────────────────────────────

    async def get_storefront_product(self, id_or_slug: str) -> StorefrontProduct:
        """
        GET /products/{id} — fetch single published product.

        Caches the serialised DTO in Redis for TTL_PRODUCT_DETAIL seconds.
        Cache key: ``product:storefront:{id_or_slug}``
        Cache is invalidated when the product is published, unpublished, updated,
        or archived (call invalidate_product_cache from those service methods).
        """
        cache_key = f"product:storefront:{id_or_slug}"
        cached = await cache.get_json(cache_key)
        if cached:
            return StorefrontProduct(**cached)

        p = await self._get_or_404(id_or_slug)
        if p.status != "PUBLISHED" or not p.published:
            raise NotFoundException(f"Product '{id_or_slug}' not found.")
        category_status_map, subcategory_status_map = await self._visibility_maps()
        if not self._taxonomy_visible(p, category_status_map, subcategory_status_map):
            raise NotFoundException(f"Product '{id_or_slug}' not found.")

        registered = await self._registered_media_items(p.id)
        collection_names = await self._collection_membership_names([p])
        dto = self._to_storefront(
            p,
            registered,
            collection_names.get(str(p.id), []),
        )
        await cache.set_json(cache_key, dto.model_dump(), TTL_PRODUCT_DETAIL)
        return dto

    async def invalidate_product_cache(self, product_id: str, slug: Optional[str] = None) -> None:
        """
        Remove all cached storefront representations for a product.
        Called after ANY admin or employee write (create, update, lifecycle
        transition, duplicate, id change) — not just status changes — so a
        saved admin mutation is visible on the next read instead of up to
        the TTL later.
        """
        keys = [f"product:storefront:{product_id}"]
        if slug:
            keys.append(f"product:storefront:{slug}")
        await cache.delete(*keys)
        # Also invalidate the broad catalog cache so listing pages refresh
        await cache.invalidate_pattern("pratikshya:cache:*products*")
        # The decorated @cache responses live in fastapi-cache2's own backend
        # (FastAPICache.init in main.py), which `cache.invalidate_pattern`
        # cannot reach — clear that layer too so storefront GETs stop serving
        # pre-write snapshots.
        await invalidate_response_cache()

    # ── Recommendations ───────────────────────────────────────────────────────

    async def get_recommendations(
        self, product_id: str, rec_type: str = "related"
    ) -> List[StorefrontProduct]:
        """
        GET /products/{id}/recommendations
        Simple category-affinity for now — same visibility gate applies.
        """
        source = await self._get_or_404(product_id)
        stmt = select(ProductModel).where(
            ProductModel.status == "PUBLISHED",
            ProductModel.published.is_(True),
            ProductModel.id != source.id,
        )
        if rec_type in ("related",):
            stmt = stmt.where(ProductModel.category == source.category)
        result = await self.db.execute(stmt.limit(12))
        products = result.scalars().all()
        category_status_map, subcategory_status_map = await self._visibility_maps()
        products = [
            p for p in products
            if self._taxonomy_visible(p, category_status_map, subcategory_status_map)
        ]
        registered_map = await self._registered_media_map([p.id for p in products])
        collection_map = await self._collection_membership_names(products)
        return [
            self._to_storefront(
                p,
                registered_map.get(p.id),
                collection_map.get(str(p.id), []),
            )
            for p in products
        ]

    # ── Recently viewed ───────────────────────────────────────────────────────

    async def get_recently_viewed(self, customer_id: str) -> List[StorefrontProduct]:
        """
        GET /products/recently-viewed

        Reads the customer's recently-viewed list from Redis (key: ``rv:{customer_id}``).
        Products are fetched from the DB and filtered to only PUBLISHED ones.
        Missing / unpublished products are silently skipped.
        """
        product_ids = await cache.list_range(f"rv:{customer_id}", 0, RECENTLY_VIEWED_LIMIT - 1)
        if not product_ids:
            return []

        stmt = select(ProductModel).where(
            ProductModel.id.in_(product_ids),
            ProductModel.status == "PUBLISHED",
            ProductModel.published.is_(True),
        )
        result = await self.db.execute(stmt)
        category_status_map, subcategory_status_map = await self._visibility_maps()
        product_map: Dict[str, ProductModel] = {
            p.id: p
            for p in result.scalars().all()
            if self._taxonomy_visible(p, category_status_map, subcategory_status_map)
        }

        registered_map = await self._registered_media_map(list(product_map.keys()))
        collection_map = await self._collection_membership_names(list(product_map.values()))

        # Preserve recency order (product_ids is already newest-first)
        return [
            self._to_storefront(
                product_map[pid],
                registered_map.get(pid),
                collection_map.get(str(pid), []),
            )
            for pid in product_ids
            if pid in product_map
        ]

    async def add_recently_viewed(self, customer_id: str, product_id: str) -> None:
        """
        POST /products/recently-viewed

        Pushes *product_id* to the front of the customer's Redis List,
        removes any prior occurrence (dedup), and trims to RECENTLY_VIEWED_LIMIT.
        TTL is refreshed to TTL_RECENTLY_VIEWED (30 days) on every push.
        """
        await cache.list_push(
            key=f"rv:{customer_id}",
            value=product_id,
            maxlen=RECENTLY_VIEWED_LIMIT,
            ttl=TTL_RECENTLY_VIEWED,
        )

    # ── Admin — list products ─────────────────────────────────────────────────

    async def list_admin_products(self, query: AdminProductListQuery) -> Dict[str, Any]:
        """
        GET /admin/products — server-authoritative admin catalogue list.

        Search (`q`), `status`, `category`, `subcategory` and
        `assignedEmployeeId` are applied as real query filters; `sort` is
        chosen from the ADMIN_SORTS allow-list; `page`/`pageSize` paginate
        the response and `total` reports the FULL filtered count (never just
        the page), so the desk can page honestly instead of treating a
        fetched subset as the whole catalogue.
        """
        stmt = select(ProductModel)
        if query.status:
            stmt = stmt.where(ProductModel.status == query.status)
        if query.category:
            stmt = stmt.where(ProductModel.category == query.category)
        if query.subcategory:
            stmt = stmt.where(ProductModel.subcategory == query.subcategory)
        if query.assigned_employee_id:
            stmt = stmt.where(ProductModel.assigned_employee_id == query.assigned_employee_id)
        if query.q:
            norm_q = f"%{query.q.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(ProductModel.name).like(norm_q),
                    func.lower(ProductModel.sku).like(norm_q),
                    func.lower(ProductModel.id).like(norm_q),
                    func.lower(ProductModel.product_id).like(norm_q),
                    func.lower(ProductModel.category).like(norm_q),
                    func.lower(ProductModel.subcategory).like(norm_q),
                    func.lower(ProductModel.fabric).like(norm_q),
                )
            )

        count_result = await self.db.execute(
            select(func.count()).select_from(stmt.subquery())
        )
        total = count_result.scalar() or 0

        result = await self.db.execute(stmt)
        products = result.scalars().all()
        registered_map = await self._registered_media_map([p.id for p in products])
        collection_map = await self._collection_membership_names(products)
        items = [
            self._to_admin(
                p,
                registered_map.get(p.id),
                collection_map.get(str(p.id), []),
            )
            for p in products
        ]

        sort = query.sort if query.sort in ADMIN_SORTS else "newest"
        if sort == "newest":
            items.sort(key=lambda p: p.created_at or "", reverse=True)
        elif sort == "oldest":
            items.sort(key=lambda p: p.created_at or "")
        elif sort == "name":
            items.sort(key=lambda p: (p.name or "").lower())
        elif sort == "price-asc":
            items.sort(key=lambda p: p.price or 0)
        elif sort == "price-desc":
            items.sort(key=lambda p: p.price or 0, reverse=True)
        elif sort == "status":
            items.sort(key=lambda p: (p.status or "", (p.name or "").lower()))
        elif sort == "updated":
            items.sort(key=lambda p: p.updated_at or "", reverse=True)

        page = max(1, query.page)
        page_size = max(1, query.page_size)
        offset = (page - 1) * page_size
        page_items = items[offset : offset + page_size]

        return {
            "ok": True,
            "items": page_items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # ── Admin — create product ────────────────────────────────────────────────

    def _content_data(self, req) -> Dict[str, Any]:
        """Explicitly-set admin content fields, keyed by model column name."""
        return req.model_dump(exclude_unset=True, by_alias=False)

    # Columns declared NOT NULL with a server-side default. On CREATE an
    # explicit null falls back to the column default; on UPDATE an explicit
    # null is ignored — clearing one of these fields is not a supported
    # operation and must never become a 500 at flush time.
    _NOT_NULL_DEFAULTS = {
        "name": "",
        "slug": "",
        "sku": "",
        "brand": "Pratikshya Fashon",
        "product_type": "fashion",
        "category": "",
        "gender": "Women",
        "price": 0,
        "currency": "INR",
        "stock": 0,
        "availability": "in-stock",
        "inventory_tracked": False,
        "low_stock_threshold": 5,
        "is_featured": False,
        "is_bestseller": False,
        "is_new": False,
        "is_limited_edition": False,
        "is_trending": False,
    }

    def _sanitize_for_create(self, data: Dict[str, Any]) -> None:
        for key, default in self._NOT_NULL_DEFAULTS.items():
            if key in data and data[key] is None:
                data[key] = default

    def _sanitize_for_update(self, data: Dict[str, Any]) -> None:
        for key in self._NOT_NULL_DEFAULTS:
            if data.get(key, "") is None and key in data:
                data.pop(key)

    def _derive_pricing(self, data: Dict[str, Any]) -> None:
        """
        Run the provided `pricing` dict through the SHARED pricing engine and
        derive the storefront money fields. Incomplete draft pricing is kept
        without blocking the save (the publish gate — `get_publish_issues` —
        is what refuses publication); a complete, valid pricing always wins
        over a raw `price`, so money on the record is server-computed.
        """
        pricing = data.get("pricing")
        if isinstance(pricing, dict) and pricing:
            computed = compute_pricing(pricing)
            if not computed["errors"] and computed.get("finalPrice", 0) > 0:
                data["price"] = computed["finalPrice"]
                mrp = int(pricing.get("mrp") or pricing.get("sellingPrice") or 0)
                if mrp > computed["finalPrice"]:
                    data["original_price"] = mrp
                else:
                    data.setdefault("original_price", None)

    def _flag_mirror(self, data: Dict[str, Any]) -> None:
        """`flags` is a derived mirror of the flat merchandising booleans."""
        if any(
            key in data
            for key in ("is_featured", "is_bestseller", "is_new", "is_limited_edition", "is_trending")
        ):
            data["flags"] = {
                "featured": bool(data.get("is_featured")),
                "bestseller": bool(data.get("is_bestseller")),
                "newArrival": bool(data.get("is_new")),
                "limitedEdition": bool(data.get("is_limited_edition")),
                "trending": bool(data.get("is_trending")),
            }

    async def create_product(
        self, req: ProductCreateRequest, actor: str
    ) -> AdminProduct:
        """
        POST /admin/products — create with a server-allocated runtime id.

        The whole supported content contract is persisted (identity, taxonomy,
        attributes, pricing, stock snapshot, SEO, media references, flags);
        fields the schema does not have are not part of the request model, so
        nothing can half-persist. The response is the authoritative server
        record the admin UI reconciles against.
        """
        import time
        new_id = f"pf-{int(time.time() * 1000):x}"

        # Taxonomy is validated FIRST: an invalid reference must not consume an
        # id, a slug or a sku, and must never reach the row.
        data = self._content_data(req)
        await self._resolve_taxonomy(data)

        # Check for id collision (unlikely but safe)
        existing = await self.db.execute(
            select(ProductModel).where(ProductModel.id == new_id)
        )
        if existing.scalars().first():
            new_id = f"{new_id}-{int(time.time() * 1000) % 1000}"

        data.setdefault("name", "")

        # Identity: a supplied slug/sku is honoured VERBATIM on this path too
        # (PF3-N04 — it used to be discarded), and a collision is a 409 rather
        # than a silent `-1` rename. Generation happens only when the caller
        # supplied nothing.
        supplied_slug = self._normalise_identity(data.get("slug"))
        supplied_sku = self._normalise_identity(data.get("sku"))
        data["slug"] = (
            await self._assert_slug_available(supplied_slug)
            if supplied_slug
            else await self._generate_unique_slug(req.name or new_id)
        )
        data["sku"] = (
            await self._assert_sku_available(supplied_sku)
            if supplied_sku
            else await self._generate_unique_sku()
        )
        data["brand"] = data.get("brand") or "Pratikshya Fashon"
        data["product_type"] = data.get("product_type") or "fashion"
        data["currency"] = data.get("currency") or "INR"
        self._derive_pricing(data)
        self._flag_mirror(data)
        self._sanitize_for_create(data)

        product = ProductModel(
            id=new_id,
            product_id=new_id,
            **data,
            status="DRAFT",
            published=False,
            review={"state": "NONE", "submittedBy": None, "submittedAt": None,
                    "reviewedBy": None, "reviewedAt": None, "rejectionReason": ""},
            review_flags=[],
            history=[],
            price_history=[],
            created_by=actor,
            updated_by=actor,
        )
        self.db.add(product)
        await self.db.flush()
        await self.invalidate_product_cache(new_id, product.slug)
        return self._to_admin(product)

    # ── Admin — create draft with caller-supplied id ───────────────────────────

    async def create_draft(self, req: ProductDraftRequest, actor: str) -> AdminProduct:
        """
        POST /admin/products/draft — the canonical product-creation path:
        a DRAFT under the caller's permanent ID, in the SAME record shape the
        editor edits afterwards (every supported field is persisted here).
        Uniqueness of the permanent ID is enforced against `catalog_product.id`
        (a taken ID is a 409, never a silent ID swap).
        """
        existing = await self.db.execute(
            select(ProductModel).where(ProductModel.id == req.id)
        )
        if existing.scalars().first():
            raise ConflictException(f"Product ID '{req.id}' is already taken.")

        data = self._content_data(req)
        data.pop("id", None)
        # Same domain rule as POST /admin/products — one validator, both
        # create paths, so neither can store an unauthorised reference.
        await self._resolve_taxonomy(data)
        base_name = data.get("name") or req.id
        data.setdefault("name", base_name)
        # Identical identity rules to POST /admin/products — one contract, two
        # entry points: supplied values are stored verbatim or 409, and only an
        # absent value is generated.
        supplied_slug = self._normalise_identity(data.get("slug"))
        supplied_sku = self._normalise_identity(data.get("sku"))
        data["slug"] = (
            await self._assert_slug_available(supplied_slug)
            if supplied_slug
            else await self._generate_unique_slug(base_name)
        )
        data["sku"] = (
            await self._assert_sku_available(supplied_sku)
            if supplied_sku
            else await self._generate_unique_sku(prefix=req.id)
        )
        data["brand"] = data.get("brand") or "Pratikshya Fashon"
        data["product_type"] = data.get("product_type") or "fashion"
        data["currency"] = data.get("currency") or "INR"
        self._derive_pricing(data)
        self._flag_mirror(data)
        self._sanitize_for_create(data)

        product = ProductModel(
            id=req.id,
            product_id=req.id,
            **data,
            status="DRAFT",
            published=False,
            review={"state": "NONE", "submittedBy": None, "submittedAt": None,
                    "reviewedBy": None, "reviewedAt": None, "rejectionReason": ""},
            review_flags=[],
            history=[],
            price_history=[],
            created_by=actor,
            updated_by=actor,
        )
        self.db.add(product)
        await self.db.flush()
        await self.invalidate_product_cache(req.id, product.slug)
        return self._to_admin(product)

    # ── Admin — get single ────────────────────────────────────────────────────

    async def get_admin_product(self, product_id: str) -> AdminProduct:
        p = await self._get_or_404(product_id)
        return await self._to_admin_current(p)

    async def get_employee_product(self, product_id: str) -> EmployeeProduct:
        """GET /employee/products/{id} — employee-safe product projection."""
        p = await self._get_or_404(product_id)
        return await self._to_employee_current(p)

    # ── Admin — update ────────────────────────────────────────────────────────

    async def update_product(
        self, product_id: str, req: ProductUpdateRequest, actor: str
    ) -> AdminProduct:
        """
        PATCH /admin/products/{id} — field-level admin patch.

        Only the fields present in the request are written (`exclude_unset`),
        so partial saves cannot clobber untouched columns with stale values,
        and the response is the authoritative post-write record the admin UI
        reconciles against (preventing the next stale-snapshot save).
        Lifecycle fields are rejected by the request schema; status changes
        go through the dedicated, guarded lifecycle endpoints only.
        """
        p = await self._get_or_404(product_id)
        data = req.model_dump(exclude_unset=True, by_alias=False)
        data.pop("id", None)

        self._sanitize_for_update(data)

        # Taxonomy: only validated when the patch actually carries one of the
        # two fields. The resulting PAIR is validated — a category-only patch
        # is checked against the stored subcategory and vice versa — while an
        # untouched field is never failed for its own status.
        await self._resolve_taxonomy(
            data, current_category=p.category, current_subcategory=p.subcategory
        )

        # Identity uniqueness is decided BEFORE any attribute is mutated, so a
        # rejected patch writes nothing. A value the product already owns is
        # never a conflict (the current row is excluded from the probe), and a
        # taken one is a 409 with `suggestedSlug` — no silent -1 rename
        # (PF3-N03 / PF3-N04, plan §8 "the silent slug rename").
        if "slug" in data:
            requested_slug = self._normalise_identity(data["slug"])
            if requested_slug:
                data["slug"] = await self._assert_slug_available(
                    requested_slug, exclude_id=p.id
                )
        if "sku" in data:
            requested_sku = self._normalise_identity(data["sku"])
            if requested_sku:
                data["sku"] = await self._assert_sku_available(
                    requested_sku, exclude_id=p.id
                )

        # Server-side pricing: a valid supplied pricing block recomputes the
        # money fields so the stored price is never trust-the-client garbage.
        self._derive_pricing(data)

        old_price = p.price

        for field, new_val in data.items():
            if field == "price_history":
                continue
            try:
                old_val = getattr(p, field, None)
            except Exception:
                continue
            if old_val != new_val:
                setattr(p, field, new_val)
                self._append_history(p, field, old_val, new_val, actor)

        # `flags` is a derived mirror — recompute from the *merged* record so
        # a partial merchandising patch cannot zero out untouched flags.
        if any(
            key in data
            for key in ("is_featured", "is_bestseller", "is_new", "is_limited_edition", "is_trending")
        ):
            merged_flags = {
                "featured": bool(p.is_featured),
                "bestseller": bool(p.is_bestseller),
                "newArrival": bool(p.is_new),
                "limitedEdition": bool(p.is_limited_edition),
                "trending": bool(p.is_trending),
            }
            if (p.flags or {}) != merged_flags:
                p.flags = merged_flags

        # Handle price change → price history (from = OLD price, to = NEW).
        if "price" in data and data["price"] != old_price:
            self._append_price_history(p, old_price, data["price"], actor)

        # Keep published flag in sync
        p.published = p.status == "PUBLISHED"
        p.updated_by = actor
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_admin_current(p)

    # ── Employee — update (whitelist only) ────────────────────────────────────

    async def update_product_employee(
        self,
        product_id: str,
        req: EmployeeProductUpdateRequest,
        employee_id: str,
        is_super_admin: bool = False,
        employee_user_id: Optional[str] = None,
    ) -> EmployeeProduct:
        """PATCH /employee/products/{id} — whitelisted fields only."""
        p = await self._get_or_404(product_id)

        # Authorization check. `employee_id` is the employee code, which is the
        # canonical product assignment contract.  The UUID fallback preserves
        # access to legacy rows that were assigned before the identity fix.
        allowed_assignees = {str(employee_id)}
        if employee_user_id:
            allowed_assignees.add(str(employee_user_id))
        if not is_super_admin and str(p.assigned_employee_id) not in allowed_assignees:
            raise ForbiddenException("You are not assigned to this product.")

        data = req.model_dump(exclude_unset=True, by_alias=False)

        # Schema validation rejects fields outside this write contract before
        # this method runs; keep the explicit set as a defence-in-depth guard.
        snake_whitelist = {
            "name", "price", "compare_at_price", "description", "short_description",
            "category", "subcategory", "gender", "fabric", "material",
            "primary_color", "secondary_color", "colors", "patterns", "work",
            "occasion", "sizes", "season", "fit", "length", "highlights",
            "care_instructions", "tags",
            "stock", "availability",
        }
        data = {k: v for k, v in data.items() if k in snake_whitelist}

        # The employee whitelist includes `category`/`subcategory`, so it is a
        # product-taxonomy write path and carries the identical domain rule.
        await self._resolve_taxonomy(
            data, current_category=p.category, current_subcategory=p.subcategory
        )

        old_price = p.price
        for field, new_val in data.items():
            old_val = getattr(p, field, None)
            if old_val != new_val:
                setattr(p, field, new_val)
                self._append_history(p, field, old_val, new_val, employee_id)

        # Price history must record OLD → NEW (the old code recorded the
        # post-write price as both endpoints).
        if "price" in data and data["price"] != old_price:
            self._append_price_history(p, old_price, data["price"], employee_id)

        p.updated_by = employee_id
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_employee_current(p)

    # ── Assign employee ───────────────────────────────────────────────────────

    async def assign_employee(
        self, product_id: str, req: AssignEmployeeRequest, actor: str
    ) -> AdminProduct:
        p = await self._get_or_404(product_id)
        if req.employee_id is not None:
            employee_result = await self.db.execute(
                select(EmployeeProfileModel.employee_code).where(
                    EmployeeProfileModel.employee_code == req.employee_id
                )
            )
            if employee_result.scalars().first() is None:
                raise BusinessLogicException(
                    f"Unknown employee code '{req.employee_id}'.",
                    details={"field": "employeeId", "value": req.employee_id},
                )
        old = p.assigned_employee_id
        p.assigned_employee_id = req.employee_id
        self._append_history(p, "assignedEmployeeId", old, req.employee_id, actor)
        p.updated_by = actor
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_admin_current(p)

    # ── Workflow actions ──────────────────────────────────────────────────────

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @staticmethod
    def _lifecycle_state(p: ProductModel) -> Tuple[str, str]:
        """The product's two independent lifecycle axes, normalised."""
        status = (p.status or "DRAFT").upper()
        review_state = str((p.review or {}).get("state") or "NONE").upper()
        return status, review_state

    @classmethod
    def _assert_transition(cls, p: ProductModel, action: str, message: str) -> Tuple[str, str]:
        """
        Enforce the DECLARED source states for `action` (plan §24 step 8).

        Both axes are checked independently against `LIFECYCLE_TRANSITIONS`, so
        a product can never satisfy a guard on one axis while violating the
        other.  The previous per-method guards combined the two axes with
        `and`, which meant an ARCHIVED product whose review was still PENDING
        (reachable by archiving a submitted product) passed both the approve
        and the reject guard — the latter silently resurrecting it to DRAFT.

        Raises BusinessLogicException — the project's canonical
        422 BUSINESS_RULE_VIOLATION. No new error code is introduced.
        """
        rule = LIFECYCLE_TRANSITIONS[action]
        status, review_state = cls._lifecycle_state(p)
        allowed_status = rule["from_status"]
        allowed_review = rule["from_review"]
        if allowed_status is not None and status not in allowed_status:
            raise BusinessLogicException(f"{message} (current status: {status}).")
        if allowed_review is not None and review_state not in allowed_review:
            raise BusinessLogicException(f"{message} (review state: {review_state}).")
        return status, review_state

    async def submit_for_review(
        self,
        product_id: str,
        actor: str,
        require_assignment: bool = False,
        employee_user_id: Optional[str] = None,
    ) -> AdminProduct:
        p = await self._get_or_404(product_id)

        if require_assignment:
            allowed_assignees = {str(actor)}
            if employee_user_id:
                # Legacy safety: old rows may have stored the user UUID. The
                # canonical contract remains employee_code and all new history
                # uses `actor` (employee code).
                allowed_assignees.add(str(employee_user_id))
            if not p.assigned_employee_id or str(p.assigned_employee_id) not in allowed_assignees:
                raise ForbiddenException("You can only submit products assigned to you.")

        status = (p.status or "DRAFT").upper()
        review_state = str((p.review or {}).get("state") or "NONE").upper()
        if status == "PUBLISHED":
            raise BusinessLogicException("This product is already published.")
        if status == "ARCHIVED":
            raise BusinessLogicException("Archived products cannot be submitted for review.")
        if status == "PENDING_REVIEW" or review_state == "PENDING":
            raise BusinessLogicException("This product is already pending review.")
        if review_state == "APPROVED":
            raise BusinessLogicException("Approved products cannot be resubmitted; publish or return them first.")

        # Review-submission completeness — distinct from the stricter publish
        # gate below; an obviously incomplete record is rejected here with an
        # actionable list instead of burning reviewer time.
        missing = [
            label
            for label, value in (
                ("name", (p.name or "").strip()),
                ("SKU", (p.sku or "").strip()),
                ("category", (p.category or "").strip()),
            )
            if not value
        ]
        if not p.price or p.price <= 0:
            missing.append("price (must be greater than zero)")
        if missing:
            raise BusinessLogicException(
                "Product is not ready for review. Missing: " + ", ".join(missing) + "."
            )

        previous_status = p.status or "DRAFT"
        now = _now_utc().isoformat()
        p.status = "PENDING_REVIEW"
        p.published = False
        p.review = {
            "state": "PENDING",
            "submittedBy": actor,
            "submittedAt": now,
            "reviewedBy": None,
            "reviewedAt": None,
            "rejectionReason": "",
        }
        self._append_history(p, "status", previous_status, "PENDING_REVIEW", actor)
        p.updated_by = actor
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_admin_current(p)

    async def approve_product(self, product_id: str, actor: str) -> AdminProduct:
        """
        PUT /admin/products/{id}/approve — approves the REVIEW, not the shop.

        Approving sets `review.state = APPROVED` and leaves the product in
        PENDING_REVIEW visibility state; going live is the separate, gated
        publish action (C-29: approval previously double-fired as an
        immediate publish).
        """
        p = await self._get_or_404(product_id)
        # Declared transition (plan §24 step 8): PENDING_REVIEW on the status
        # axis AND PENDING/APPROVED on the review axis. Checking the two axes
        # independently is what stops an ARCHIVED product whose review is still
        # PENDING from being approved.
        status, review_state = self._assert_transition(
            p, "approve",
            "Only products pending review can be approved; submit it for review first",
        )
        if review_state == "APPROVED":
            return await self._to_admin_current(p)  # idempotent — already approved
        now = _now_utc()
        p.review = {
            **(p.review or {}),
            "state": "APPROVED",
            "reviewedBy": actor,
            "reviewedAt": now.isoformat(),
        }
        self._append_history(p, "review.state", review_state, "APPROVED", actor)
        p.updated_by = actor
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_admin_current(p)

    async def reject_product(self, product_id: str, req: RejectProductRequest, actor: str) -> AdminProduct:
        """
        PUT /admin/products/{id}/reject — only a submitted product can be
        rejected; the outcome returns it to DRAFT with a visible rejection.
        """
        p = await self._get_or_404(product_id)
        # Declared transition (plan §24 step 8). Previously the two axes were
        # combined with `and`, so an ARCHIVED product still carrying a PENDING
        # review could be "rejected" — which set status=DRAFT and silently
        # resurrected it out of the archive.
        self._assert_transition(
            p, "reject",
            "Only products pending review can be rejected or returned",
        )
        previous_status = p.status or "PENDING_REVIEW"
        now = _now_utc().isoformat()
        p.status = "DRAFT"
        p.published = False
        p.review = {
            **(p.review or {}),
            "state": "REJECTED",
            "reviewedBy": actor,
            "reviewedAt": now,
            "rejectionReason": req.reason,
        }
        self._append_history(p, "status", previous_status, "DRAFT", actor)
        p.updated_by = actor
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_admin_current(p)

    async def publish_product(self, product_id: str, actor: str) -> AdminProduct:
        """
        PUT /admin/products/{id}/publish — the ONLY path to PUBLISHED.

        Enforced server-side: the product must exist, must not be archived,
        must have passed review (`review.state == APPROVED`) and must have no
        unresolved publish issues. The frontend publish-issues checklist is a
        convenience pre-check; this gate is the authority. `status`,
        `published`, `published_by` and `published_at` are written together
        so listing filters (status) and detail projection (published) cannot
        disagree.
        """
        p = await self._get_or_404(product_id)
        status = (p.status or "").upper()
        if status == "ARCHIVED":
            raise BusinessLogicException("Archived products cannot be published; restore them first.")
        if status == "PUBLISHED" and p.published:
            return await self._to_admin_current(p)  # idempotent — already live
        review_state = str((p.review or {}).get("state") or "NONE").upper()
        if review_state != "APPROVED":
            raise BusinessLogicException(
                "This product has not been approved for publication yet. "
                "Submit it for review and approve it before publishing "
                f"(review state: {review_state})."
            )
        # The gate reads the authoritative registered association inside this
        # same request session/transaction; a pending Phase 7 migration makes
        # the helper answer [] so the legacy media branch keeps working.
        issues = get_publish_issues(p, await self._registered_media_items(p.id))
        if issues:
            raise BusinessLogicException(
                "Product has unresolved publish issues.", details={"errors": issues}
            )
        previous_status = p.status or "DRAFT"
        now = _now_utc()
        p.status = "PUBLISHED"
        p.published = True
        p.published_by = actor
        p.published_at = now
        self._append_history(p, "status", previous_status, "PUBLISHED", actor)
        p.updated_by = actor
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_admin_current(p)

    async def unpublish_product(self, product_id: str, actor: str) -> AdminProduct:
        p = await self._get_or_404(product_id)
        if (p.status or "").upper() != "PUBLISHED":
            raise BusinessLogicException(
                f"Only published products can be unpublished (current status: {p.status or 'DRAFT'})."
            )
        self._append_history(p, "status", p.status, "DRAFT", actor)
        p.status = "DRAFT"
        p.published = False
        p.updated_by = actor
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_admin_current(p)

    async def archive_product(self, product_id: str, actor: str) -> AdminProduct:
        p = await self._get_or_404(product_id)
        if (p.status or "").upper() == "ARCHIVED":
            raise BusinessLogicException("This product is already archived.")
        self._append_history(p, "status", p.status, "ARCHIVED", actor)
        p.status = "ARCHIVED"
        p.published = False
        p.updated_by = actor
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_admin_current(p)

    async def restore_product(self, product_id: str, actor: str) -> AdminProduct:
        p = await self._get_or_404(product_id)
        if (p.status or "").upper() != "ARCHIVED":
            raise BusinessLogicException(
                f"Only archived products can be restored (current status: {p.status or 'DRAFT'})."
            )
        self._append_history(p, "status", p.status, "DRAFT", actor)
        p.status = "DRAFT"
        p.published = False
        p.updated_by = actor
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_admin_current(p)

    # ── Publish issues ────────────────────────────────────────────────────────

    async def get_publish_issues(self, product_id: str) -> List[str]:
        p = await self._get_or_404(product_id)
        return get_publish_issues(p, await self._registered_media_items(p.id))

    # ── Change ID ─────────────────────────────────────────────────────────────

    async def change_product_id(
        self, product_id: str, req: ChangeProductIdRequest, actor: str
    ) -> AdminProduct:
        """
        POST /admin/products/{id}/change-id — change the DISPLAY LABEL only.

        Plan §24 step 8 requires the "BACKEND DECISION REQUIRED: cascade to
        media, inventory, collection, order history" question to be resolved.

        RESOLUTION — no cascade is required, because no cascade target exists.
        This route has never touched `catalog_product.id`, the primary key that
        `media_product_media.product_id`, inventory, collection membership and
        order lines all reference. It only rewrites `catalog_product.product_id`,
        the human-facing label. Every foreign reference therefore stays valid by
        construction, and a cascade would in fact be the bug.

        What DID need restricting is the freeness check: it tested the new label
        against the primary key only, so two rows could end up sharing one
        `product_id`. `_get_or_404` resolves on id OR product_id OR slug, so a
        duplicate label makes admin lookups silently ambiguous — the same class
        of defect Block 3 closed for SKU and slug, and it is closed the same
        way, with a service-layer 409 and no UNIQUE constraint.
        """
        p = await self._get_or_404(product_id)
        new_id = req.new_id
        # The new label must collide with neither a primary key nor another
        # row's display label (self-collision is a no-op, not a conflict).
        clash = await self.db.execute(
            select(ProductModel).where(
                or_(
                    ProductModel.id == new_id,
                    ProductModel.product_id == new_id,
                ),
                ProductModel.id != p.id,
            )
        )
        if clash.scalars().first():
            raise ConflictException(f"Product ID '{new_id}' is already taken.")
        old_id = p.product_id or p.id
        p.product_id = new_id
        self._append_history(p, "productId", old_id, new_id, actor)
        p.updated_by = actor
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_admin_current(p)

    # ── Duplicate ─────────────────────────────────────────────────────────────

    async def duplicate_product(self, product_id: str, actor: str) -> AdminProduct:
        import time
        p = await self._get_or_404(product_id)
        new_id = f"pf-{int(time.time() * 1000):x}"
        new_slug = await self._generate_unique_slug(f"{p.name}-copy")
        new_sku = await self._generate_unique_sku()

        dup = ProductModel(
            id=new_id,
            product_id=new_id,
            name=f"{p.name} (Copy)" if p.name else "",
            slug=new_slug,
            sku=new_sku,
            brand=p.brand,
            product_type=p.product_type,
            category=p.category,
            subcategory=p.subcategory,
            gender=p.gender,
            short_description=p.short_description,
            description=p.description,
            highlights=p.highlights,
            specifications=p.specifications,
            care_instructions=p.care_instructions,
            fabric=p.fabric,
            material=p.material,
            primary_color=p.primary_color,
            secondary_color=p.secondary_color,
            colors=p.colors,
            patterns=p.patterns,
            work=p.work,
            occasion=p.occasion,
            sizes=p.sizes,
            season=p.season,
            fit=p.fit,
            length=p.length,
            collection=p.collection,
            collections=p.collections,
            tags=p.tags,
            badges=p.badges,
            # Merchandising booleans + their derived mirror must survive the
            # copy — dropping them silently changed the copy's storefront
            # presentation versus the original.
            is_featured=p.is_featured,
            is_bestseller=p.is_bestseller,
            is_new=p.is_new,
            is_limited_edition=p.is_limited_edition,
            is_trending=p.is_trending,
            flags=p.flags,
            product_code=p.product_code,
            barcode=p.barcode,
            internal_reference=p.internal_reference,
            delivery_info=p.delivery_info,
            return_info=p.return_info,
            return_policy=p.return_policy,
            unavailable_colors=p.unavailable_colors,
            unavailable_sizes=p.unavailable_sizes,
            price=p.price,
            original_price=p.original_price,
            compare_at_price=p.compare_at_price,
            currency=p.currency,
            pricing=p.pricing,
            stock=p.stock,
            availability=p.availability,
            low_stock_threshold=p.low_stock_threshold,
            seo=p.seo,
            media_ids=p.media_ids,
            primary_media_id=p.primary_media_id,
            gallery_media_ids=p.gallery_media_ids,
            image=p.image,
            hover_image=p.hover_image,
            additional_images=p.additional_images,
            status="DRAFT",
            published=False,
            review={"state": "NONE", "submittedBy": None, "submittedAt": None,
                    "reviewedBy": None, "reviewedAt": None, "rejectionReason": ""},
            review_flags=[],
            history=[],
            price_history=[],
            created_by=actor,
            updated_by=actor,
        )
        self.db.add(dup)
        await self.db.flush()
        return self._to_admin(dup)

    # ── Bulk update ───────────────────────────────────────────────────────────

    # Whitelist for bulk edits: merchandising + content columns only. Bulk
    # actions must NEVER move lifecycle state — that would bypass the
    # per-product publish gate — so `status`, review, media ids and pricing
    # overrides are intentionally absent.
    BULK_UPDATABLE_FIELDS = {
        "is_featured": "isFeatured",
        "is_bestseller": "isBestseller",
        "is_new": "isNew",
        "is_limited_edition": "isLimitedEdition",
        "is_trending": "isTrending",
        "category": "category",
        "subcategory": "subcategory",
        "gender": "gender",
        "season": "season",
        "occasion": "occasion",
        "care_instructions": "careInstructions",
        "tags": "tags",
        "seo": "seo",
    }

    async def bulk_update(self, req: BulkUpdateRequest, actor: str) -> Dict[str, Any]:
        incoming = dict(req.updates or {})
        allowed: Dict[str, Any] = {}
        rejected: List[str] = []
        for key, value in incoming.items():
            column = None
            if key in self.BULK_UPDATABLE_FIELDS:
                column = key
            else:
                for col, alias in self.BULK_UPDATABLE_FIELDS.items():
                    if key == alias:
                        column = col
                        break
            if column is None or value is None:
                rejected.append(key)
            else:
                allowed[column] = value
        if not allowed:
            raise BusinessLogicException(
                "No supported bulk fields in request.",
                details={
                    "rejected": sorted(set(rejected)),
                    "supported": sorted(self.BULK_UPDATABLE_FIELDS),
                    "hint": (
                        "Bulk edits support merchandising/content flags only. "
                        "Status changes go through the per-product lifecycle "
                        "endpoints so publish rules are enforced."
                    ),
                },
            )

        updated: List[str] = []
        skipped: List[str] = []
        for pid in req.product_ids:
            try:
                p = await self._get_or_404(pid)
            except NotFoundException:
                skipped.append(pid)
                continue
            for field, val in allowed.items():
                old_val = getattr(p, field, None)
                if old_val != val:
                    setattr(p, field, val)
                    self._append_history(p, field, old_val, val, actor)
            if any(k in allowed for k in (
                "is_featured", "is_bestseller", "is_new", "is_limited_edition", "is_trending",
            )):
                p.flags = {
                    "featured": bool(p.is_featured),
                    "bestseller": bool(p.is_bestseller),
                    "newArrival": bool(p.is_new),
                    "limitedEdition": bool(p.is_limited_edition),
                    "trending": bool(p.is_trending),
                }
            p.updated_by = actor
            updated.append(p.id)
            await self.invalidate_product_cache(p.id, p.slug)
        await self.db.flush()
        return {
            "ok": True,
            "updatedCount": len(updated),
            "updatedIds": updated,
            "notFound": skipped,
            "rejectedFields": sorted(set(rejected)),
        }

    # ── Availability ──────────────────────────────────────────────────────────

    async def check_availability(
        self,
        sku: Optional[str] = None,
        slug: Optional[str] = None,
        exclude_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Pre-flight identity probe for the admin editor.

        This is a CONVENIENCE, not the enforcement layer: the authority remains
        the 409 raised by `_assert_sku_available` / `_assert_slug_available` on
        the write itself. To make the two agree, this method calls exactly the
        SAME probe helpers and the SAME slug generator the write path uses —
        there is deliberately no second copy of the collision rule here. Trim,
        case-insensitive comparison and self-exclusion therefore behave
        identically, so `skuTaken == false` cannot be followed by a 409 for
        that same value in the same exclusion context (barring a concurrent
        write between the two requests — see `API_CONTRACT.md` §9.5).

        `exclude_id` is the product being edited. Its own SKU/slug must report
        as FREE, exactly as `PATCH` accepts a product's own identity. An
        `exclude_id` matching no row simply excludes nothing.
        """
        sku_taken = False
        slug_taken = False
        suggested_slug = None

        sku = self._normalise_identity(sku)
        slug = self._normalise_identity(slug)
        exclude_id = self._normalise_identity(exclude_id) or None

        if sku:
            sku_taken = await self._product_with_sku(sku, exclude_id=exclude_id) is not None

        if slug:
            slug_taken = await self._product_with_slug(slug, exclude_id=exclude_id) is not None
            if slug_taken:
                suggested_slug = await self._generate_unique_slug(
                    slug, base=True, exclude_id=exclude_id
                )

        return {"ok": True, "skuTaken": sku_taken, "slugTaken": slug_taken, "suggestedSlug": suggested_slug}

    # ── Next stable ID ────────────────────────────────────────────────────────

    async def get_next_id(
        self, category_id: str, preferred_number: Optional[int] = None
    ) -> str:
        """
        Deterministic nextStableProductId() — never random.
        Scans the register, honours preferredNumber, picks lowest free integer.

        Canonical form: `PF-{CATEGORY_CODE}-{NNNN}` — a four-digit serial under
        a server-derived prefix. This is the SINGLE product-id authority: the
        client requests the id here and stores it verbatim, and never derives
        one from a local taxonomy snapshot.
        """
        code = CATEGORY_ID_PREFIXES.get(category_id, "GEN")
        prefix = f"PF-{code}"
        result = await self.db.execute(
            select(ProductModel.id).where(
                ProductModel.id.like(f"{prefix}-%")
            )
        )
        taken_ids = {row[0] for row in result}

        pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
        taken_nums = set()
        for pid in taken_ids:
            m = pattern.match(pid)
            if m:
                taken_nums.add(int(m.group(1)))

        if preferred_number and preferred_number not in taken_nums:
            n = preferred_number
        else:
            n = 1
            while n in taken_nums:
                n += 1

        return f"{prefix}-{n:04d}"

    # ── Metrics ───────────────────────────────────────────────────────────────

    async def get_metrics(self) -> CatalogMetricsResponse:
        result = await self.db.execute(select(ProductModel.status))
        statuses = [row[0] for row in result]
        result2 = await self.db.execute(
            select(ProductModel.review_flags).where(ProductModel.status != "ARCHIVED")
        )
        blocking_flags = set(REVIEW_FLAG_BLOCKING)
        blocked = sum(
            1 for (flags,) in result2
            if flags and set(flags) & blocking_flags
        )
        result3 = await self.db.execute(
            select(func.count()).where(
                ProductModel.assigned_employee_id.is_(None),
                ProductModel.status.in_(["DRAFT", "PENDING_REVIEW"]),
            )
        )
        unassigned = result3.scalar() or 0

        return CatalogMetricsResponse(
            total=len(statuses),
            draft=statuses.count("DRAFT"),
            pendingReview=statuses.count("PENDING_REVIEW"),
            published=statuses.count("PUBLISHED"),
            archived=statuses.count("ARCHIVED"),
            unassigned=unassigned,
            blocked=blocked,
        )

    # ── Clear review flags ────────────────────────────────────────────────────

    async def clear_review_flags(
        self, product_id: str, req: ClearReviewFlagsRequest, actor: str
    ) -> AdminProduct:
        p = await self._get_or_404(product_id)
        old_flags = list(p.review_flags or [])
        new_flags = [f for f in old_flags if f not in req.flags]
        p.review_flags = new_flags
        if old_flags != new_flags:
            self._append_history(p, "reviewFlags", old_flags, new_flags, actor)
            p.updated_by = actor
        await self.db.flush()
        await self.invalidate_product_cache(p.id, p.slug)
        return await self._to_admin_current(p)

    # ── Internal slug/sku helpers ─────────────────────────────────────────────

    async def _generate_unique_slug(
        self, base_text: str, base: bool = False, exclude_id: Optional[str] = None
    ) -> str:
        """
        The generator used when the caller supplied NO slug (and to compute a
        `suggestedSlug` after a collision). It uses the same case-insensitive
        comparison as the enforcement path, so a suggestion can never come back
        as a 409.
        """
        base_slug = _slugify(base_text) if not base else base_text
        slug = base_slug
        counter = 1
        while True:
            if not await self._product_with_slug(slug, exclude_id=exclude_id):
                return slug
            slug = f"{base_slug}-{counter}"
            counter += 1

    async def _generate_unique_sku(self, prefix: str = "PF") -> str:
        """Generate a unique SKU in PF-##### format."""
        import random
        for _ in range(100):
            n = random.randint(10000, 99999)
            candidate = f"{prefix[:2].upper()}-{n:05d}"
            if not await self._product_with_sku(candidate):
                return candidate
        raise BusinessLogicException("Could not generate a unique SKU.")
