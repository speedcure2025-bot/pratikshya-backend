"""
CategoryService — all business logic for categories and subcategories.

Endpoints served:
  Public
  ──────────────────────────────────────────────────────────────────────
  GET /categories                             list_categories()
  GET /categories/{idOrSlug}                  get_category()
  GET /categories/{categoryId}/subcategories  list_subcategories()

  Admin
  ──────────────────────────────────────────────────────────────────────
  POST   /admin/categories                              create_category()
  PATCH  /admin/categories/{id}                         update_category()
  POST   /admin/categories/{id}/archive                 archive_category()
  POST   /admin/categories/{id}/restore                 restore_category()
  POST   /admin/categories/{categoryId}/subcategories   create_subcategory()
  PATCH  /admin/subcategories/{id}                      update_subcategory()
  POST   /admin/subcategories/{id}/archive              archive_subcategory()
  POST   /admin/subcategories/{id}/restore              restore_subcategory()
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache, invalidate_response_cache
from app.core.exceptions import ConflictException, NotFoundException
from app.models.catalog.category import CategoryModel, SubcategoryModel
from app.models.catalog.product import ProductModel
from app.schemas.catalog.category import (
    CategoryCreateRequest,
    CategoryResponse,
    CategoryUpdateRequest,
    SubcategoryCreateRequest,
    SubcategoryResponse,
    SubcategoryUpdateRequest,
)


# ── Utility ───────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Convert a human-readable label to a URL-safe lowercase slug."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_]+", "-", text)


# ── Projection helpers ─────────────────────────────────────────────────────────

def _project_category(model: CategoryModel, product_count: int = 0) -> CategoryResponse:
    return CategoryResponse(
        id=model.id,
        name=model.name,
        slug=model.slug,
        eyebrow=model.eyebrow or "",
        description=model.description or "",
        image=model.image or "",
        banner_media_id=model.banner_media_id,
        status=model.status,
        sort_order=model.sort_order,
        featured=model.featured,
        seo_title=model.seo_title or "",
        seo_description=model.seo_description or "",
        productCount=product_count,
    )


def _project_subcategory(model: SubcategoryModel, product_count: int = 0) -> SubcategoryResponse:
    return SubcategoryResponse(
        id=model.id,
        category_id=model.category_id,
        name=model.name,
        slug=model.slug,
        description=model.description or "",
        image=model.image or "",
        status=model.status,
        sort_order=model.sort_order,
        productCount=product_count,
    )


# ── Service ───────────────────────────────────────────────────────────────────

class CategoryService:
    """Business logic for the category taxonomy."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_category_or_404(self, id_or_slug: str) -> CategoryModel:
        stmt = select(CategoryModel).where(
            or_(CategoryModel.id == id_or_slug, CategoryModel.slug == id_or_slug)
        )
        result = await self.db.execute(stmt)
        category = result.scalars().first()
        if not category:
            raise NotFoundException(f"Category '{id_or_slug}' not found.")
        return category

    async def _get_subcategory_or_404(self, subcategory_id: str) -> SubcategoryModel:
        result = await self.db.execute(
            select(SubcategoryModel).where(SubcategoryModel.id == subcategory_id)
        )
        sub = result.scalars().first()
        if not sub:
            raise NotFoundException(f"Subcategory '{subcategory_id}' not found.")
        return sub

    async def _product_count_for_category(self, category_id: str) -> int:
        """Count PUBLISHED products in a category."""
        result = await self.db.execute(
            select(func.count()).select_from(ProductModel).where(
                ProductModel.category == category_id,
                ProductModel.status == "PUBLISHED",
                ProductModel.published.is_(True),
            )
        )
        return result.scalar() or 0

    async def _product_count_for_subcategory(self, category_id: str, subcategory_slug: str) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(ProductModel).where(
                ProductModel.category == category_id,
                ProductModel.subcategory == subcategory_slug,
                ProductModel.status == "PUBLISHED",
                ProductModel.published.is_(True),
            )
        )
        return result.scalar() or 0

    async def _assert_slug_unique(self, slug: str, exclude_id: Optional[str] = None) -> None:
        stmt = select(CategoryModel.id).where(CategoryModel.slug == slug)
        if exclude_id:
            stmt = stmt.where(CategoryModel.id != exclude_id)
        result = await self.db.execute(stmt)
        if result.scalars().first():
            raise ConflictException(f"A category with slug '{slug}' already exists.")

    async def _assert_sub_slug_unique(
        self, category_id: str, slug: str, exclude_id: Optional[str] = None
    ) -> None:
        stmt = select(SubcategoryModel.id).where(
            SubcategoryModel.category_id == category_id,
            SubcategoryModel.slug == slug,
        )
        if exclude_id:
            stmt = stmt.where(SubcategoryModel.id != exclude_id)
        result = await self.db.execute(stmt)
        if result.scalars().first():
            raise ConflictException(
                f"A subcategory with slug '{slug}' already exists in this category."
            )

    async def _invalidate_taxonomy_cache(self) -> None:
        """
        Every category/subcategory write changes what the cached public
        taxonomy + product surfaces may legitimately serve, because the
        storefront visibility gate reads category AND subcategory status
        (`ProductService._taxonomy_visible`).

        Two layers have to go, not one:

        * the decorated `@cache` HTTP response layer, which holds rendered
          `GET /products`, `GET /categories/*` and `GET /collections/*` bodies;
        * the KV entries `product:storefront:{id}` / `{slug}` written by
          `ProductService.get_storefront_product`. That method returns the
          cached DTO **before** it evaluates the taxonomy gate, so archiving a
          category or a subcategory would otherwise leave the product
          reachable on its PDP for the rest of the TTL — the new subcategory
          gate (PF3-N06) would be silently bypassed on exactly the transition
          it exists to catch. `ProductService.invalidate_product_cache` clears
          these keys on product writes; a taxonomy write has to do the same.
          Note the `*products*` glob used there does NOT match the singular
          `product:storefront:` prefix, hence the explicit pattern.

        Plan reference: §24 step 7 ("Extend cache invalidation coverage") and
        §23 R9 ("Adding new read gates must not introduce a second cache key
        that is not invalidated").
        """
        await cache.invalidate_pattern("product:storefront:*")
        await invalidate_response_cache()

    # ── Public — categories ───────────────────────────────────────────────────

    async def list_categories(
        self,
        status_filter: Optional[str] = "ACTIVE",
        featured: Optional[bool] = None,
    ) -> List[CategoryResponse]:
        """
        GET /categories
        Default: status=ACTIVE. Sorted by sort_order ASC, then name ASC.
        """
        stmt = select(CategoryModel)
        if status_filter:
            stmt = stmt.where(CategoryModel.status == status_filter)
        if featured is not None:
            stmt = stmt.where(CategoryModel.featured.is_(featured))
        stmt = stmt.order_by(CategoryModel.sort_order.asc(), CategoryModel.name.asc())

        result = await self.db.execute(stmt)
        categories = result.scalars().all()

        output = []
        for cat in categories:
            count = await self._product_count_for_category(cat.id)
            output.append(_project_category(cat, count))
        return output

    async def get_category(self, id_or_slug: str) -> CategoryResponse:
        """GET /categories/{idOrSlug} — public, only ACTIVE visible."""
        cat = await self._get_category_or_404(id_or_slug)
        if cat.status != "ACTIVE":
            raise NotFoundException(f"Category '{id_or_slug}' not found.")
        count = await self._product_count_for_category(cat.id)
        return _project_category(cat, count)

    # ── Public — subcategories ────────────────────────────────────────────────

    async def list_subcategories(
        self,
        category_id: str,
        status_filter: Optional[str] = "ACTIVE",
    ) -> List[SubcategoryResponse]:
        """GET /categories/{categoryId}/subcategories."""
        # Resolve the parent (may be id or slug)
        cat = await self._get_category_or_404(category_id)

        stmt = select(SubcategoryModel).where(SubcategoryModel.category_id == cat.id)
        if status_filter:
            stmt = stmt.where(SubcategoryModel.status == status_filter)
        stmt = stmt.order_by(SubcategoryModel.sort_order.asc(), SubcategoryModel.name.asc())

        result = await self.db.execute(stmt)
        subs = result.scalars().all()

        output = []
        for sub in subs:
            count = await self._product_count_for_subcategory(cat.id, sub.slug)
            output.append(_project_subcategory(sub, count))
        return output

    # ── Admin — categories ────────────────────────────────────────────────────

    async def create_category(
        self, req: CategoryCreateRequest, actor: str
    ) -> CategoryResponse:
        """POST /admin/categories — creates a DRAFT category."""
        import uuid
        slug = req.slug or _slugify(req.name)
        await self._assert_slug_unique(slug)

        cat = CategoryModel(
            id=str(uuid.uuid4()),
            name=req.name,
            slug=slug,
            eyebrow=req.eyebrow or "",
            description=req.description or "",
            image=req.image or "",
            banner_media_id=req.banner_media_id,
            sort_order=req.sort_order,
            featured=req.featured,
            seo_title=req.seo_title or "",
            seo_description=req.seo_description or "",
            status="DRAFT",
            created_by=actor,
            updated_by=actor,
        )
        self.db.add(cat)
        await self.db.flush()
        await self.db.refresh(cat)
        await self._invalidate_taxonomy_cache()
        return _project_category(cat)

    async def update_category(
        self, category_id: str, req: CategoryUpdateRequest, actor: str
    ) -> CategoryResponse:
        """PATCH /admin/categories/{id}."""
        cat = await self._get_category_or_404(category_id)

        if req.name is not None:
            cat.name = req.name
        if req.slug is not None:
            new_slug = req.slug
            await self._assert_slug_unique(new_slug, exclude_id=cat.id)
            cat.slug = new_slug
        if req.eyebrow is not None:
            cat.eyebrow = req.eyebrow
        if req.description is not None:
            cat.description = req.description
        if req.image is not None:
            cat.image = req.image
        if req.banner_media_id is not None:
            cat.banner_media_id = req.banner_media_id
        if req.sort_order is not None:
            cat.sort_order = req.sort_order
        if req.featured is not None:
            cat.featured = req.featured
        if req.seo_title is not None:
            cat.seo_title = req.seo_title
        if req.seo_description is not None:
            cat.seo_description = req.seo_description

        cat.updated_by = actor
        await self.db.flush()
        await self.db.refresh(cat)
        await self._invalidate_taxonomy_cache()
        count = await self._product_count_for_category(cat.id)
        return _project_category(cat, count)

    # ── Admin — read paths (any status) + activation ─────────────────────────

    async def list_admin_categories(
        self,
        status_filter: Optional[str] = None,
        featured: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """
        GET /admin/categories — the admin desk view.

        Unlike the public list, DRAFT and ARCHIVED rows are included (pass
        `status` to narrow), and each row carries honest counts:
        `productCount` (live/published) and `productCountTotal` (all statuses)
        so the taxonomy desk tiles read from the server instead of from the
        in-browser catalogue snapshot.
        """
        items = await self.list_categories(status_filter=status_filter, featured=featured)
        output: List[Dict[str, Any]] = []
        for item in items:
            total_result = await self.db.execute(
                select(func.count()).select_from(ProductModel).where(
                    ProductModel.category == item.id
                )
            )
            row = item.model_dump(by_alias=True)
            row["productCountTotal"] = total_result.scalar() or 0
            output.append(row)
        return output

    async def get_admin_category(self, id_or_slug: str) -> CategoryResponse:
        """GET /admin/categories/{id} — resolves DRAFT/ARCHIVED rows too."""
        cat = await self._get_category_or_404(id_or_slug)
        count = await self._product_count_for_category(cat.id)
        return _project_category(cat, count)

    async def list_admin_subcategories(
        self,
        category_id: str,
        status_filter: Optional[str] = None,
    ) -> List[SubcategoryResponse]:
        """GET /admin/categories/{id}/subcategories — includes DRAFT/ARCHIVED."""
        cat = await self._get_category_or_404(category_id)
        stmt = select(SubcategoryModel).where(SubcategoryModel.category_id == cat.id)
        if status_filter:
            stmt = stmt.where(SubcategoryModel.status == status_filter)
        stmt = stmt.order_by(SubcategoryModel.sort_order.asc(), SubcategoryModel.name.asc())
        result = await self.db.execute(stmt)
        output = []
        for sub in result.scalars().all():
            count = await self._product_count_for_subcategory(cat.id, sub.slug)
            output.append(_project_subcategory(sub, count))
        return output

    async def activate_category(self, category_id: str, actor: str) -> CategoryResponse:
        """
        POST /admin/categories/{id}/activate — DRAFT → ACTIVE only.

        Restoring archived rows stays on the dedicated `/restore` route so the
        two state transitions remain auditable separately; going ACTIVE→DRAFT
        is not offered because nothing in the product flow expects it.
        """
        cat = await self._get_category_or_404(category_id)
        if cat.status != "DRAFT":
            raise ConflictException(
                f"Only DRAFT categories can be activated (current status: {cat.status}). "
                "Use restore for archived categories."
            )
        cat.status = "ACTIVE"
        cat.updated_by = actor
        await self.db.flush()
        await self.db.refresh(cat)
        await self._invalidate_taxonomy_cache()
        count = await self._product_count_for_category(cat.id)
        return _project_category(cat, count)

    async def activate_subcategory(self, subcategory_id: str, actor: str) -> SubcategoryResponse:
        """POST /admin/subcategories/{id}/activate — DRAFT → ACTIVE only."""
        sub = await self._get_subcategory_or_404(subcategory_id)
        if sub.status != "DRAFT":
            raise ConflictException(
                f"Only DRAFT subcategories can be activated (current status: {sub.status}). "
                "Use restore for archived subcategories."
            )
        sub.status = "ACTIVE"
        sub.updated_by = actor
        await self.db.flush()
        await self.db.refresh(sub)
        await self._invalidate_taxonomy_cache()
        count = await self._product_count_for_subcategory(sub.category_id, sub.slug)
        return _project_subcategory(sub, count)

    async def archive_category(self, category_id: str, actor: str) -> CategoryResponse:
        """
        POST /admin/categories/{id}/archive → status = ARCHIVED.

        CRITICAL: This removes ALL products in this category from every
        customer surface (the visibility gate reads category status).
        The caller must surface this warning before invoking.
        """
        cat = await self._get_category_or_404(category_id)
        if cat.status == "ARCHIVED":
            raise ConflictException("Category is already archived.")
        cat.status = "ARCHIVED"
        cat.updated_by = actor
        await self.db.flush()
        await self.db.refresh(cat)
        await self._invalidate_taxonomy_cache()
        count = await self._product_count_for_category(cat.id)
        return _project_category(cat, count)

    async def restore_category(self, category_id: str, actor: str) -> CategoryResponse:
        """POST /admin/categories/{id}/restore → status = ACTIVE."""
        cat = await self._get_category_or_404(category_id)
        if cat.status != "ARCHIVED":
            raise ConflictException("Only ARCHIVED categories can be restored.")
        cat.status = "ACTIVE"
        cat.updated_by = actor
        await self.db.flush()
        await self.db.refresh(cat)
        await self._invalidate_taxonomy_cache()
        count = await self._product_count_for_category(cat.id)
        return _project_category(cat, count)

    # ── Admin — subcategories ─────────────────────────────────────────────────

    async def create_subcategory(
        self, category_id: str, req: SubcategoryCreateRequest, actor: str
    ) -> SubcategoryResponse:
        """POST /admin/categories/{categoryId}/subcategories."""
        cat = await self._get_category_or_404(category_id)

        slug = req.slug or _slugify(req.name)
        await self._assert_sub_slug_unique(cat.id, slug)

        sub_id = f"{cat.slug}-{slug}"
        sub = SubcategoryModel(
            id=sub_id,
            category_id=cat.id,
            name=req.name,
            slug=slug,
            description=req.description or "",
            image=req.image or "",
            sort_order=req.sort_order,
            status="DRAFT",
            created_by=actor,
            updated_by=actor,
        )
        self.db.add(sub)
        await self.db.flush()
        await self.db.refresh(sub)
        await self._invalidate_taxonomy_cache()
        return _project_subcategory(sub)

    async def update_subcategory(
        self, subcategory_id: str, req: SubcategoryUpdateRequest, actor: str
    ) -> SubcategoryResponse:
        """PATCH /admin/subcategories/{id}."""
        sub = await self._get_subcategory_or_404(subcategory_id)

        if req.name is not None:
            sub.name = req.name
        if req.slug is not None:
            await self._assert_sub_slug_unique(sub.category_id, req.slug, exclude_id=sub.id)
            sub.slug = req.slug
        if req.description is not None:
            sub.description = req.description
        if req.image is not None:
            sub.image = req.image
        if req.sort_order is not None:
            sub.sort_order = req.sort_order

        sub.updated_by = actor
        await self.db.flush()
        await self.db.refresh(sub)
        await self._invalidate_taxonomy_cache()
        count = await self._product_count_for_subcategory(sub.category_id, sub.slug)
        return _project_subcategory(sub, count)

    async def archive_subcategory(
        self, subcategory_id: str, actor: str
    ) -> SubcategoryResponse:
        """POST /admin/subcategories/{id}/archive."""
        sub = await self._get_subcategory_or_404(subcategory_id)
        if sub.status == "ARCHIVED":
            raise ConflictException("Subcategory is already archived.")
        sub.status = "ARCHIVED"
        sub.updated_by = actor
        await self.db.flush()
        await self.db.refresh(sub)
        await self._invalidate_taxonomy_cache()
        count = await self._product_count_for_subcategory(sub.category_id, sub.slug)
        return _project_subcategory(sub, count)

    async def restore_subcategory(
        self, subcategory_id: str, actor: str
    ) -> SubcategoryResponse:
        """POST /admin/subcategories/{id}/restore."""
        sub = await self._get_subcategory_or_404(subcategory_id)
        if sub.status != "ARCHIVED":
            raise ConflictException("Only ARCHIVED subcategories can be restored.")
        sub.status = "ACTIVE"
        sub.updated_by = actor
        await self.db.flush()
        await self.db.refresh(sub)
        await self._invalidate_taxonomy_cache()
        count = await self._product_count_for_subcategory(sub.category_id, sub.slug)
        return _project_subcategory(sub, count)
