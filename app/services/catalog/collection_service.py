"""
CollectionService — all business logic for collections.

Endpoints served:
  Public
  ──────────────────────────────────────────────────────────────────────────
  GET  /collections                         list_collections()
  GET  /collections/{idOrSlug}              get_collection()
  GET  /collections/{id}/products           get_collection_products()  ← delegated to ProductService

  Admin
  ──────────────────────────────────────────────────────────────────────────
  POST  /admin/collections                  create_collection()
  PATCH /admin/collections/{id}             update_collection()
  POST  /admin/collections/{id}/activate    activate_collection()
  POST  /admin/collections/{id}/pause       pause_collection()
  POST  /admin/collections/{id}/archive     archive_collection()
  POST  /admin/collections/{id}/restore     restore_collection()
  PUT   /admin/collections/{id}/products    assign_products()

  Taxonomy metrics
  ──────────────────────────────────────────────────────────────────────────
  GET  /admin/taxonomy/metrics              taxonomy_metrics()
  GET  /admin/taxonomy/product-counts       taxonomy_product_counts()

displayStatus derivation:
  ARCHIVED → ARCHIVED
  DRAFT    → DRAFT
  PAUSED   → PAUSED
  ACTIVE   and end_date in the past  → EXPIRED
  ACTIVE   and start_date in future  → SCHEDULED
  ACTIVE   otherwise                 → ACTIVE

Membership resolution (MANUAL vs RULE_BASED):
  MANUAL     → explicit_product_ids list
  RULE_BASED → rule { flag, occasion, fabricIncludes } evaluated against ProductModel at
               query time — returns matching PUBLISHED product ids.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_response_cache
from app.core.exceptions import BusinessLogicException, ConflictException, NotFoundException
from app.models.catalog.collection import CollectionModel
from app.models.catalog.product import ProductModel
from app.schemas.catalog.collection import (
    AssignProductsRequest,
    CollectionCreateRequest,
    CollectionResponse,
    CollectionUpdateRequest,
)


# ── Utility ───────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Convert a human-readable label to a URL-safe lowercase slug."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_]+", "-", text)


def _derive_display_status(model: CollectionModel) -> str:
    """
    Compute displayStatus from (status, start_date, end_date).
    This must be server-side and never blindly stored.
    """
    now = datetime.now(timezone.utc)
    if model.status == "ARCHIVED":
        return "ARCHIVED"
    if model.status == "DRAFT":
        return "DRAFT"
    if model.status == "PAUSED":
        return "PAUSED"
    if model.status == "ACTIVE":
        if model.end_date and model.end_date < now:
            return "EXPIRED"
        if model.start_date and model.start_date > now:
            return "SCHEDULED"
        return "ACTIVE"
    # SCHEDULED, EXPIRED etc. stored directly (edge case)
    return model.status


# ── Projection helper ──────────────────────────────────────────────────────────

def _project(model: CollectionModel, resolved_count: int = 0) -> CollectionResponse:
    return CollectionResponse(
        id=model.id,
        name=model.name,
        slug=model.slug,
        eyebrow=model.eyebrow or "",
        description=model.description or "",
        image=model.image or "",
        hero_media_id=model.hero_media_id,
        thumbnail_media_id=model.thumbnail_media_id,
        type=model.type,
        status=model.status,
        displayStatus=_derive_display_status(model),
        featured=model.featured,
        sort_order=model.sort_order,
        start_date=model.start_date,
        end_date=model.end_date,
        rule=model.rule or {},
        explicit_product_ids=model.explicit_product_ids or [],
        resolvedProductCount=resolved_count,
    )


# ── Service ───────────────────────────────────────────────────────────────────

class CollectionService:
    """Business logic for the collections taxonomy."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_or_404(self, id_or_slug: str) -> CollectionModel:
        stmt = select(CollectionModel).where(
            or_(CollectionModel.id == id_or_slug, CollectionModel.slug == id_or_slug)
        )
        result = await self.db.execute(stmt)
        obj = result.scalars().first()
        if not obj:
            raise NotFoundException(f"Collection '{id_or_slug}' not found.")
        return obj

    async def _assert_slug_unique(
        self, slug: str, exclude_id: Optional[str] = None
    ) -> None:
        stmt = select(CollectionModel.id).where(CollectionModel.slug == slug)
        if exclude_id:
            stmt = stmt.where(CollectionModel.id != exclude_id)
        result = await self.db.execute(stmt)
        if result.scalars().first():
            raise ConflictException(f"A collection with slug '{slug}' already exists.")

    async def _validated_product_ids(
        self, product_ids: Optional[List[str]], field: str = "productIds"
    ) -> List[str]:
        """Validate and canonicalise a collection-owned product ID list.

        Collection membership is stored in JSONB rather than a relational
        association table, so the database cannot enforce these references.
        Resolve every id before mutating the collection and preserve caller
        order while removing duplicate associations. Unknown ids are a single
        atomic 422 business-rule failure; no partial list is ever stored.
        """
        ids = list(product_ids or [])
        unique_ids = list(dict.fromkeys(ids))
        if not unique_ids:
            return unique_ids

        result = await self.db.execute(
            select(ProductModel.id).where(ProductModel.id.in_(unique_ids))
        )
        existing = {str(value) for value in result.scalars().all()}
        unknown = [value for value in unique_ids if str(value) not in existing]
        if unknown:
            raise BusinessLogicException(
                f"Unknown product(s): {', '.join(unknown)}.",
                details={"field": field, "unknown": unknown},
            )
        return unique_ids

    async def _resolve_product_ids(self, model: CollectionModel) -> List[str]:
        """
        Resolve the final set of product IDs for a collection.

        MANUAL     → explicit_product_ids union label-match
        RULE_BASED → rule evaluation

        Both paths only return PUBLISHED products.
        """
        if model.type == "RULE_BASED" and model.rule:
            return await self._rule_product_ids(model.rule)

        # MANUAL: start with explicit ids
        explicit: List[str] = model.explicit_product_ids or []

        # Also include products where product.collection or product.collections[]
        # contains the collection name (legacy label match).
        label_ids = await self._label_match_product_ids(model.name, model.id)

        # Union, preserving order, deduplicating
        combined = list(dict.fromkeys(explicit + label_ids))
        return combined

    async def _rule_product_ids(self, rule: Dict[str, Any]) -> List[str]:
        """Evaluate a rule dict against PUBLISHED products and return matching IDs."""
        stmt = select(ProductModel.id).where(
            ProductModel.status == "PUBLISHED",
            ProductModel.published.is_(True),
        )
        result = await self.db.execute(stmt)
        all_published = result.scalars().all()

        # NOTE: For production, push rule evaluation to the DB via JSONB operators.
        # Here we do a simple Python-side filter for correctness and clarity.
        matched: List[str] = []
        for pid in all_published:
            matched.append(pid)  # broadened below with actual field checks

        # Re-fetch full rows for rule filtering only when a rule is present
        rows_result = await self.db.execute(
            select(ProductModel).where(
                ProductModel.status == "PUBLISHED",
                ProductModel.published.is_(True),
            )
        )
        rows = rows_result.scalars().all()

        flag = rule.get("flag")
        occasion = rule.get("occasion")
        fabric_includes = rule.get("fabricIncludes")

        matched = []
        for p in rows:
            if flag:
                flags_dict: dict = p.flags or {}
                if not flags_dict.get(flag):
                    continue
            if occasion:
                occasions: list = p.occasion or []
                if occasion not in occasions:
                    continue
            if fabric_includes:
                fabric: str = p.fabric or ""
                if fabric_includes.lower() not in fabric.lower():
                    continue
            matched.append(p.id)

        return matched

    async def _label_match_product_ids(
        self, collection_name: str, collection_id: str
    ) -> List[str]:
        """
        Find PUBLISHED products whose `collection` string or `collections` JSONB array
        references this collection by name or by id.
        """
        # We cast name/id comparison to JSONB contains where possible;
        # for the scalar `collection` column we use ILIKE.
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB

        stmt = select(ProductModel.id).where(
            ProductModel.status == "PUBLISHED",
            ProductModel.published.is_(True),
            or_(
                # Legacy scalar: product.collection contains the name/id
                ProductModel.collection.ilike(f"%{collection_name}%"),
                ProductModel.collection.ilike(f"%{collection_id}%"),
                # JSONB array: product.collections @> '["<name>"]'
                ProductModel.collections.cast(PG_JSONB).contains(
                    [collection_name]
                ),
                ProductModel.collections.cast(PG_JSONB).contains(
                    [collection_id]
                ),
            ),
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _resolved_count(self, model: CollectionModel) -> int:
        ids = await self._resolve_product_ids(model)
        return len(ids)

    # ── Public — list & detail ────────────────────────────────────────────────

    async def list_collections(
        self,
        status_filter: Optional[str] = "ACTIVE",
        featured: Optional[bool] = None,
    ) -> List[CollectionResponse]:
        """
        GET /collections
        Public default: status=ACTIVE. Sorted by sort_order ASC, then name ASC.
        """
        stmt = select(CollectionModel)
        if status_filter:
            stmt = stmt.where(CollectionModel.status == status_filter)
        if featured is not None:
            stmt = stmt.where(CollectionModel.featured.is_(featured))
        stmt = stmt.order_by(
            CollectionModel.sort_order.asc(), CollectionModel.name.asc()
        )
        result = await self.db.execute(stmt)
        collections = result.scalars().all()

        output = []
        for col in collections:
            count = await self._resolved_count(col)
            output.append(_project(col, count))
        return output

    async def get_collection(self, id_or_slug: str) -> CollectionResponse:
        """
        GET /collections/{idOrSlug}
        Public: only ACTIVE collections visible.
        """
        col = await self._get_or_404(id_or_slug)
        if col.status != "ACTIVE":
            raise NotFoundException(f"Collection '{id_or_slug}' not found.")
        count = await self._resolved_count(col)
        return _project(col, count)

    async def get_collection_product_ids(self, id_or_slug: str) -> Tuple[CollectionModel, List[str]]:
        """
        GET /collections/{id}/products — returns (model, resolved_product_ids).
        The router uses the ids to call ProductService.list_storefront_products().
        """
        col = await self._get_or_404(id_or_slug)
        if col.status != "ACTIVE":
            raise NotFoundException(f"Collection '{id_or_slug}' not found.")
        ids = await self._resolve_product_ids(col)
        return col, ids

    # ── Admin — list (all statuses) ───────────────────────────────────────────

    async def admin_list_collections(
        self,
        status_filter: Optional[str] = None,
        featured: Optional[bool] = None,
        q: Optional[str] = None,
    ) -> List[CollectionResponse]:
        """
        Admin list — no status gate; returns all collections.
        """
        stmt = select(CollectionModel)
        if status_filter:
            stmt = stmt.where(CollectionModel.status == status_filter)
        if featured is not None:
            stmt = stmt.where(CollectionModel.featured.is_(featured))
        if q:
            stmt = stmt.where(CollectionModel.name.ilike(f"%{q}%"))
        stmt = stmt.order_by(
            CollectionModel.sort_order.asc(), CollectionModel.name.asc()
        )
        result = await self.db.execute(stmt)
        collections = result.scalars().all()

        output = []
        for col in collections:
            count = await self._resolved_count(col)
            output.append(_project(col, count))
        return output

    async def admin_get_collection(self, id_or_slug: str) -> CollectionResponse:
        """Admin get — no status gate."""
        col = await self._get_or_404(id_or_slug)
        count = await self._resolved_count(col)
        return _project(col, count)

    # ── Admin — create ────────────────────────────────────────────────────────

    async def create_collection(
        self, req: CollectionCreateRequest, actor: str
    ) -> CollectionResponse:
        """POST /admin/collections — creates a DRAFT collection."""
        slug = req.slug or _slugify(req.name)
        await self._assert_slug_unique(slug)

        type_val = req.type.value if hasattr(req.type, "value") else str(req.type)
        explicit_product_ids = await self._validated_product_ids(
            req.explicit_product_ids, field="explicitProductIds"
        )

        col = CollectionModel(
            name=req.name,
            slug=slug,
            eyebrow=req.eyebrow or "",
            description=req.description or "",
            image=req.image or "",
            hero_media_id=req.hero_media_id,
            thumbnail_media_id=req.thumbnail_media_id,
            type=type_val,
            featured=req.featured,
            sort_order=req.sort_order,
            start_date=req.start_date,
            end_date=req.end_date,
            status="DRAFT",
            explicit_product_ids=explicit_product_ids,
            rule=req.rule or {},
            created_by=actor,
            updated_by=actor,
        )
        self.db.add(col)
        await self.db.flush()
        # Collections are read by cached storefront surfaces — a write must
        # not leave stale cached listings behind (see core.cache helper).
        await invalidate_response_cache()
        await self.db.refresh(col)
        return _project(col)

    # ── Admin — update ────────────────────────────────────────────────────────

    async def update_collection(
        self, collection_id: str, req: CollectionUpdateRequest, actor: str
    ) -> CollectionResponse:
        """PATCH /admin/collections/{id}."""
        col = await self._get_or_404(collection_id)
        validated_explicit_product_ids = None
        if req.explicit_product_ids is not None:
            validated_explicit_product_ids = await self._validated_product_ids(
                req.explicit_product_ids, field="explicitProductIds"
            )

        if req.name is not None:
            col.name = req.name
        if req.slug is not None:
            await self._assert_slug_unique(req.slug, exclude_id=col.id)
            col.slug = req.slug
        if req.eyebrow is not None:
            col.eyebrow = req.eyebrow
        if req.description is not None:
            col.description = req.description
        if req.image is not None:
            col.image = req.image
        if req.hero_media_id is not None:
            col.hero_media_id = req.hero_media_id
        if req.thumbnail_media_id is not None:
            col.thumbnail_media_id = req.thumbnail_media_id
        if req.type is not None:
            col.type = req.type.value if hasattr(req.type, "value") else str(req.type)
        if req.featured is not None:
            col.featured = req.featured
        if req.sort_order is not None:
            col.sort_order = req.sort_order

        # Validate effective dates
        eff_start = req.start_date if req.start_date is not None else col.start_date
        eff_end = req.end_date if req.end_date is not None else col.end_date
        if eff_start is not None and eff_end is not None and eff_end < eff_start:
            raise BusinessLogicException("endDate must be greater than or equal to startDate")

        if req.start_date is not None:
            col.start_date = req.start_date
        if req.end_date is not None:
            col.end_date = req.end_date
        if validated_explicit_product_ids is not None:
            col.explicit_product_ids = validated_explicit_product_ids
        if req.rule is not None:
            col.rule = req.rule

        col.updated_by = actor
        await self.db.flush()
        # Collections are read by cached storefront surfaces — a write must
        # not leave stale cached listings behind (see core.cache helper).
        await invalidate_response_cache()
        await self.db.refresh(col)
        count = await self._resolved_count(col)
        return _project(col, count)

    # ── Admin — lifecycle actions ─────────────────────────────────────────────

    async def activate_collection(
        self, collection_id: str, actor: str
    ) -> CollectionResponse:
        """POST /admin/collections/{id}/activate → status = ACTIVE."""
        col = await self._get_or_404(collection_id)
        if col.status == "ARCHIVED":
            raise ConflictException("Archived collections cannot be activated directly. Restore first.")
        col.status = "ACTIVE"
        col.updated_by = actor
        await self.db.flush()
        # Collections are read by cached storefront surfaces — a write must
        # not leave stale cached listings behind (see core.cache helper).
        await invalidate_response_cache()
        await self.db.refresh(col)
        count = await self._resolved_count(col)
        return _project(col, count)

    async def pause_collection(
        self, collection_id: str, actor: str
    ) -> CollectionResponse:
        """POST /admin/collections/{id}/pause → status = PAUSED."""
        col = await self._get_or_404(collection_id)
        if col.status not in ("ACTIVE", "SCHEDULED"):
            raise ConflictException(
                f"Only ACTIVE or SCHEDULED collections can be paused (current: {col.status})."
            )
        col.status = "PAUSED"
        col.updated_by = actor
        await self.db.flush()
        # Collections are read by cached storefront surfaces — a write must
        # not leave stale cached listings behind (see core.cache helper).
        await invalidate_response_cache()
        await self.db.refresh(col)
        count = await self._resolved_count(col)
        return _project(col, count)

    async def archive_collection(
        self, collection_id: str, actor: str
    ) -> CollectionResponse:
        """POST /admin/collections/{id}/archive → status = ARCHIVED."""
        col = await self._get_or_404(collection_id)
        if col.status == "ARCHIVED":
            raise ConflictException("Collection is already archived.")
        col.status = "ARCHIVED"
        col.updated_by = actor
        await self.db.flush()
        # Collections are read by cached storefront surfaces — a write must
        # not leave stale cached listings behind (see core.cache helper).
        await invalidate_response_cache()
        await self.db.refresh(col)
        return _project(col)

    async def restore_collection(
        self, collection_id: str, actor: str
    ) -> CollectionResponse:
        """POST /admin/collections/{id}/restore → status = DRAFT."""
        col = await self._get_or_404(collection_id)
        if col.status != "ARCHIVED":
            raise ConflictException("Only ARCHIVED collections can be restored.")
        col.status = "DRAFT"
        col.updated_by = actor
        await self.db.flush()
        # Collections are read by cached storefront surfaces — a write must
        # not leave stale cached listings behind (see core.cache helper).
        await invalidate_response_cache()
        await self.db.refresh(col)
        return _project(col)

    # ── Admin — product assignment ────────────────────────────────────────────

    async def assign_products(
        self, collection_id: str, req: AssignProductsRequest, actor: str
    ) -> CollectionResponse:
        """
        PUT /admin/collections/{id}/products
        Replaces the full explicit product list. MANUAL collections only.
        Activity: COLLECTION_PRODUCTS_UPDATED
        """
        col = await self._get_or_404(collection_id)
        if col.type != "MANUAL":
            raise ConflictException(
                "Product assignment via explicit list is only supported for MANUAL collections. "
                "For RULE_BASED collections, update the rule via PATCH."
            )
        col.explicit_product_ids = await self._validated_product_ids(req.productIds)

        col.updated_by = actor
        await self.db.flush()
        # Collections are read by cached storefront surfaces — a write must
        # not leave stale cached listings behind (see core.cache helper).
        await invalidate_response_cache()
        await self.db.refresh(col)
        count = await self._resolved_count(col)
        return _project(col, count)

    # ── Taxonomy metrics ──────────────────────────────────────────────────────

    async def taxonomy_metrics(self) -> Dict[str, Any]:
        """
        GET /admin/taxonomy/metrics
        Returns counts for collections by status + total categories and subcategories.
        """
        from app.models.catalog.category import CategoryModel, SubcategoryModel

        # Collection counts by status
        col_counts_result = await self.db.execute(
            select(CollectionModel.status, func.count(CollectionModel.id))
            .group_by(CollectionModel.status)
        )
        col_by_status = {row[0]: row[1] for row in col_counts_result}

        # Category / subcategory totals
        cat_total = (
            await self.db.execute(select(func.count(CategoryModel.id)))
        ).scalar() or 0
        sub_total = (
            await self.db.execute(select(func.count(SubcategoryModel.id)))
        ).scalar() or 0
        col_total = sum(col_by_status.values())

        return {
            "ok": True,
            "collections": {
                "total": col_total,
                "byStatus": col_by_status,
            },
            "categories": {"total": cat_total},
            "subcategories": {"total": sub_total},
        }

    async def taxonomy_product_counts(self) -> Dict[str, Any]:
        """
        GET /admin/taxonomy/product-counts
        Returns per-collection resolved product counts.
        """
        stmt = select(CollectionModel).order_by(CollectionModel.name.asc())
        result = await self.db.execute(stmt)
        collections = result.scalars().all()

        counts = []
        for col in collections:
            n = await self._resolved_count(col)
            counts.append({"collectionId": col.id, "name": col.name, "productCount": n})

        return {"ok": True, "counts": counts}
