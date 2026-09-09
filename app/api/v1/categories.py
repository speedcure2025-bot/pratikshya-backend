"""
Categories — API router.

URL mapping (API_CONTRACT.md → implementation):

  Public / Customer
  ─────────────────────────────────────────────────────────────────────────────
  GET  /categories                                         ← list categories
  GET  /categories/{idOrSlug}                             ← single category
  GET  /categories/{categoryId}/subcategories             ← subcategory list
  GET  /categories/{id}/products                          ← product list filtered by category

  Admin
  ─────────────────────────────────────────────────────────────────────────────
  GET   /admin/categories                                  ← admin list (all statuses)
  GET   /admin/categories/{id}                             ← single category, any status
  GET   /admin/categories/{id}/subcategories               ← admin subcategory list
  POST  /admin/categories/{id}/activate                    ← DRAFT → ACTIVE
  POST  /admin/categories                                  ← create category
  PATCH /admin/categories/{id}                            ← update category
  POST  /admin/categories/{id}/archive                    ← archive category
  POST  /admin/categories/{id}/restore                    ← restore category
  POST  /admin/categories/{categoryId}/subcategories      ← create subcategory
  PATCH /admin/subcategories/{id}                         ← update subcategory
  POST  /admin/subcategories/{id}/archive                 ← archive subcategory
  POST  /admin/subcategories/{id}/restore                 ← restore subcategory

Notes:
  - GET /categories/{id}/products delegates to CategoryService + ProductService
    so that the full facet/sort/pagination logic is reused.
  - Route order: /{categoryId}/subcategories and /{id}/products are registered
    before /{idOrSlug} to prevent path ambiguity.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from fastapi_cache.decorator import cache
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTL_CATEGORIES
from app.dependencies import get_current_admin, get_db, require_admin_permission
from app.models.auth.user import UserModel
from app.schemas.catalog.category import (
    CategoryCreateRequest,
    CategoryListResponse,
    CategoryUpdateRequest,
    OkResponse,
    SingleCategoryResponse,
    SingleSubcategoryResponse,
    SubcategoryCreateRequest,
    SubcategoryListResponse,
    SubcategoryUpdateRequest,
)
from app.schemas.catalog.product import ProductListQuery, ProductListResponse
from app.services.catalog.category_service import CategoryService
from app.services.catalog.product_service import ProductService

router = APIRouter(tags=["Categories"])


# ===========================================================================
# PUBLIC — List categories
# ===========================================================================

@router.get(
    "/categories",
    response_model=CategoryListResponse,
    summary="List categories",
    description=(
        "Public endpoint.  \n"
        "Default filter: `status=ACTIVE`.  \n"
        "Optional filter: `featured=true/false`.  \n"
        "Sorted by `sortOrder` ASC, then `name` ASC.  \n"
        "Response shape: `[{ id, name, slug, eyebrow, description, image, "
        "bannerMediaId, status, sortOrder, featured, seoTitle, seoDescription, productCount }]`"
    ),
)
@cache(expire=TTL_CATEGORIES)
async def list_categories(
    status_filter: Optional[str] = Query(
        "ACTIVE",
        alias="status",
        description="DRAFT | ACTIVE | ARCHIVED. Default: ACTIVE.",
    ),
    featured: Optional[bool] = Query(None, description="Filter by featured flag."),
    db: AsyncSession = Depends(get_db),
):
    service = CategoryService(db)
    items = await service.list_categories(
        status_filter=status_filter,
        featured=featured,
    )
    return CategoryListResponse(items=items)


# ===========================================================================
# PUBLIC — Subcategory list  (registered BEFORE /{idOrSlug} to avoid collision)
# ===========================================================================

@router.get(
    "/categories/{category_id}/subcategories",
    response_model=SubcategoryListResponse,
    summary="List subcategories for a category",
    description=(
        "Public endpoint.  \n"
        "Default filter: `status=ACTIVE`.  \n"
        "Response shape: `[{ id, categoryId, name, slug, description, image, "
        "status, sortOrder, productCount }]`.  \n"
        "ID convention: `<categoryId>-<slug>`, e.g. `sarees-pato-saree`. "
        "Slugs are unique **within** a category."
    ),
)
@cache(expire=TTL_CATEGORIES)
async def list_subcategories(
    category_id: str,
    status_filter: Optional[str] = Query("ACTIVE", alias="status"),
    db: AsyncSession = Depends(get_db),
):
    service = CategoryService(db)
    items = await service.list_subcategories(
        category_id=category_id,
        status_filter=status_filter,
    )
    return SubcategoryListResponse(items=items)


# ===========================================================================
# PUBLIC — Products for a category  (registered BEFORE /{idOrSlug})
# ===========================================================================

@router.get(
    "/categories/{category_id}/products",
    response_model=ProductListResponse,
    summary="Products in a category — full facet/sort/pagination support",
    description=(
        "Public endpoint. Delegates to the same catalogue query as `GET /products`, "
        "with `category` pre-filled.  \n"
        "Gate: `PUBLISHED`, `published=true`, category must be `ACTIVE`.  \n"
        "Supports all 12 facets, all sort aliases, and `page` / `pageSize` (default 20)."
    ),
)
async def get_category_products(
    category_id: str,
    q: Optional[str] = Query(None),
    subcategory: Optional[List[str]] = Query(None),
    gender: Optional[List[str]] = Query(None),
    price: Optional[List[str]] = Query(None),
    size: Optional[List[str]] = Query(None),
    color: Optional[List[str]] = Query(None),
    fabric: Optional[List[str]] = Query(None),
    material: Optional[List[str]] = Query(None),
    occasion: Optional[List[str]] = Query(None),
    collection: Optional[List[str]] = Query(None),
    rating: Optional[List[str]] = Query(None),
    availability: Optional[List[str]] = Query(None),
    sort: str = Query("recommended"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200, alias="pageSize"),
    db: AsyncSession = Depends(get_db),
):
    # Resolve category so we return 404 for unknown ids/slugs
    cat_service = CategoryService(db)
    category = await cat_service.get_category(category_id)

    query = ProductListQuery(
        q=q,
        category=[category.id],   # filter by the resolved id
        subcategory=subcategory,
        gender=gender,
        price=price,
        size=size,
        color=color,
        fabric=fabric,
        material=material,
        occasion=occasion,
        collection=collection,
        rating=rating,
        availability=availability,
        sort=sort,
        page=page,
        pageSize=pageSize,
    )
    prod_service = ProductService(db)
    result = await prod_service.list_storefront_products(query)
    return ProductListResponse(**result)


# ===========================================================================
# PUBLIC — Single category  (registered AFTER the two path-specific routes)
# ===========================================================================

@router.get(
    "/categories/{id_or_slug}",
    response_model=SingleCategoryResponse,
    summary="Get a single category by id or slug",
    description=(
        "Public endpoint. Returns `404` if the category is not `ACTIVE`.  \n"
        "Response: `{ ok: true, category: { … } }`"
    ),
)
@cache(expire=TTL_CATEGORIES)
async def get_category(
    id_or_slug: str,
    db: AsyncSession = Depends(get_db),
):
    service = CategoryService(db)
    category = await service.get_category(id_or_slug)
    return SingleCategoryResponse(category=category)


# ===========================================================================
# ADMIN — Categories
# ===========================================================================

@router.get(
    "/admin/categories",
    summary="Admin — category list including DRAFT and ARCHIVED",
    description=(
        "Authorization: `categories.view`.  \n"
        "Optional `status` filter; without it every status is returned.  \n"
        "Rows carry server-computed `productCount` (published) and "
        "`productCountTotal` (all statuses) for the taxonomy desk tiles."
    ),
)
async def admin_list_categories(
    status_filter: Optional[str] = Query(None, alias="status"),
    featured: Optional[bool] = Query(None),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.view")
    service = CategoryService(db)
    items = await service.list_admin_categories(status_filter=status_filter, featured=featured)
    return {"ok": True, "items": items, "total": len(items)}


@router.get(
    "/admin/categories/{category_id}",
    response_model=SingleCategoryResponse,
    summary="Admin — get one category (any status)",
    description="Authorization: `categories.view`. Resolves DRAFT/ARCHIVED rows too.",
)
async def admin_get_category(
    category_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.view")
    service = CategoryService(db)
    category = await service.get_admin_category(category_id)
    return SingleCategoryResponse(category=category)


@router.get(
    "/admin/categories/{category_id}/subcategories",
    response_model=SubcategoryListResponse,
    summary="Admin — subcategory list including DRAFT and ARCHIVED",
    description="Authorization: `categories.view`. Optional `status` filter.",
)
async def admin_list_subcategories(
    category_id: str,
    status_filter: Optional[str] = Query(None, alias="status"),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.view")
    service = CategoryService(db)
    items = await service.list_admin_subcategories(
        category_id=category_id, status_filter=status_filter
    )
    return SubcategoryListResponse(items=items)


@router.post(
    "/admin/categories",
    response_model=SingleCategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin — create a category",
    description=(
        "Authorization: `categories.create`.  \n"
        "Starts with `status = DRAFT`. Slug is auto-derived from `name` if omitted.  \n"
        "Activity: `CATEGORY_CREATED`."
    ),
)
async def admin_create_category(
    req: CategoryCreateRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.create")
    service = CategoryService(db)
    category = await service.create_category(req, actor=current_user.id)
    return SingleCategoryResponse(category=category)


@router.patch(
    "/admin/categories/{category_id}",
    response_model=SingleCategoryResponse,
    summary="Admin — update a category",
    description=(
        "Authorization: `categories.edit`.  \n"
        "Partial patch — omit any field to leave it unchanged.  \n"
        "Activity: `CATEGORY_UPDATED`."
    ),
)
async def admin_update_category(
    category_id: str,
    req: CategoryUpdateRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.edit")
    service = CategoryService(db)
    category = await service.update_category(category_id, req, actor=current_user.id)
    return SingleCategoryResponse(category=category)


@router.post(
    "/admin/categories/{category_id}/activate",
    response_model=SingleCategoryResponse,
    summary="Admin — activate a DRAFT category (DRAFT → ACTIVE)",
    description=(
        "Authorization: `categories.edit`.  \n"
        "Activation is the promotion step for categories created by the admin "
        "desk (creation always starts in DRAFT so an unfinished category is "
        "never surfaced to customers).  \n"
        "Only `DRAFT` categories can be activated; `ARCHIVED` uses restore."
    ),
)
async def admin_activate_category(
    category_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.edit")
    service = CategoryService(db)
    category = await service.activate_category(category_id, actor=current_user.id)
    return SingleCategoryResponse(category=category)


@router.post(
    "/admin/categories/{category_id}/archive",
    response_model=SingleCategoryResponse,
    summary="Admin — archive a category",
    description=(
        "Authorization: `categories.archive`.  \n"
        "**CRITICAL**: Archiving removes ALL products in this category from every "
        "customer surface (the visibility gate reads category status).  \n"
        "The operator must confirm this before calling.  \n"
        "Activity: `CATEGORY_ARCHIVED`."
    ),
)
async def admin_archive_category(
    category_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.archive")
    service = CategoryService(db)
    category = await service.archive_category(category_id, actor=current_user.id)
    return SingleCategoryResponse(category=category)


@router.post(
    "/admin/categories/{category_id}/restore",
    response_model=SingleCategoryResponse,
    summary="Admin — restore an archived category",
    description=(
        "Authorization: `categories.archive`.  \n"
        "Only `ARCHIVED` categories can be restored. Sets `status = ACTIVE`.  \n"
        "Activity: `CATEGORY_RESTORED`."
    ),
)
async def admin_restore_category(
    category_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.archive")
    service = CategoryService(db)
    category = await service.restore_category(category_id, actor=current_user.id)
    return SingleCategoryResponse(category=category)


# ===========================================================================
# ADMIN — Subcategories
# ===========================================================================

@router.post(
    "/admin/categories/{category_id}/subcategories",
    response_model=SingleSubcategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin — create a subcategory",
    description=(
        "Authorization: `categories.create`.  \n"
        "Slug is auto-derived from `name` if omitted, and must be unique within the category.  \n"
        "Activity: `SUBCATEGORY_CREATED`."
    ),
)
async def admin_create_subcategory(
    category_id: str,
    req: SubcategoryCreateRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.create")
    service = CategoryService(db)
    sub = await service.create_subcategory(category_id, req, actor=current_user.id)
    return SingleSubcategoryResponse(subcategory=sub)


@router.patch(
    "/admin/subcategories/{subcategory_id}",
    response_model=SingleSubcategoryResponse,
    summary="Admin — update a subcategory",
    description=(
        "Authorization: `categories.edit`.  \n"
        "Partial patch.  \n"
        "Activity: `SUBCATEGORY_UPDATED`."
    ),
)
async def admin_update_subcategory(
    subcategory_id: str,
    req: SubcategoryUpdateRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.edit")
    service = CategoryService(db)
    sub = await service.update_subcategory(subcategory_id, req, actor=current_user.id)
    return SingleSubcategoryResponse(subcategory=sub)


@router.post(
    "/admin/subcategories/{subcategory_id}/activate",
    response_model=SingleSubcategoryResponse,
    summary="Admin — activate a DRAFT subcategory (DRAFT → ACTIVE)",
    description="Authorization: `categories.edit`. Only DRAFT rows can be activated; archived uses restore.",
)
async def admin_activate_subcategory(
    subcategory_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.edit")
    service = CategoryService(db)
    sub = await service.activate_subcategory(subcategory_id, actor=current_user.id)
    return SingleSubcategoryResponse(subcategory=sub)


@router.post(
    "/admin/subcategories/{subcategory_id}/archive",
    response_model=SingleSubcategoryResponse,
    summary="Admin — archive a subcategory",
    description="Authorization: `categories.archive`. Activity: `SUBCATEGORY_ARCHIVED`.",
)
async def admin_archive_subcategory(
    subcategory_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.archive")
    service = CategoryService(db)
    sub = await service.archive_subcategory(subcategory_id, actor=current_user.id)
    return SingleSubcategoryResponse(subcategory=sub)


@router.post(
    "/admin/subcategories/{subcategory_id}/restore",
    response_model=SingleSubcategoryResponse,
    summary="Admin — restore an archived subcategory",
    description=(
        "Authorization: `categories.archive`.  \n"
        "Only `ARCHIVED` subcategories can be restored. Sets `status = ACTIVE`."
    ),
)
async def admin_restore_subcategory(
    subcategory_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "categories.archive")
    service = CategoryService(db)
    sub = await service.restore_subcategory(subcategory_id, actor=current_user.id)
    return SingleSubcategoryResponse(subcategory=sub)
