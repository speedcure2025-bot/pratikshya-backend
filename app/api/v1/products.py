"""
Products — API router.

URL mapping (API_CONTRACT.md → implementation):

  Public / Customer
  ─────────────────────────────────────────────────────────────────────────────
  GET  /products                                ← storefront catalogue
  GET  /products/recently-viewed                ← customer recently viewed (guest + auth)
  POST /products/recently-viewed                ← record viewed product
  GET  /products/{idOrSlug}                     ← product detail page
  GET  /products/{id}/recommendations           ← related/complete-look/cart recs
  GET  /products/{id}/media-set                 ← canonical media set (delegated to media module)

  Admin
  ─────────────────────────────────────────────────────────────────────────────
  GET  /admin/products                          ← full admin catalogue list
  POST /admin/products                          ← create product (runtime id)
  POST /admin/products/draft                    ← create draft with supplied permanent id
  GET  /admin/products/next-id                  ← deterministic next stable id
  GET  /admin/products/availability             ← sku / slug availability check
  GET  /admin/products/metrics                  ← counts by status / review state
  GET  /admin/workflow/metrics                  ← alias for above
  GET  /admin/products/{id}                     ← full admin record
  PATCH /admin/products/{id}                    ← full-field patch
  POST /admin/products/{id}/assign              ← assign / unassign employee
  POST /admin/products/{id}/approve             ← approve review (gates publish)
  POST /admin/products/{id}/reject              ← reject → DRAFT
  POST /admin/products/{id}/publish             ← publish
  POST /admin/products/{id}/unpublish           ← unpublish → DRAFT
  POST /admin/products/{id}/archive             ← archive
  POST /admin/products/{id}/restore             ← restore → DRAFT
  GET  /admin/products/{id}/publish-issues      ← live blocker list
  POST /admin/products/{id}/change-id           ← change permanent id
  POST /admin/products/{id}/duplicate           ← duplicate
  POST /admin/products/bulk                     ← bulk status / field update
  POST /admin/products/{id}/review-flags/clear  ← clear specific flags

  Employee
  ─────────────────────────────────────────────────────────────────────────────
  GET  /employee/products/{id}                  ← employee-safe product projection
  PATCH /employee/products/{id}                 ← whitelisted product-content patch

  Storefront submit-review (assigned employee or admin)
  ─────────────────────────────────────────────────────────────────────────────
  POST /products/{id}/submit-review             ← status = PENDING_REVIEW
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from fastapi_cache.decorator import cache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTL_PRODUCTS_LIST, TTL_PRODUCT_DETAIL, TTL_RECOMMENDATIONS
from app.dependencies import (
    get_current_admin,
    get_current_employee,
    get_current_user,
    get_db,
    require_admin_permission,
    require_permission_for_user,
)
from app.models.auth.user import UserModel
from app.models.employee.employee import EmployeeProfileModel
from app.schemas.catalog.product import (
    AdminProduct,
    AdminProductListQuery,
    AdminProductListResponse,
    AssignEmployeeRequest,
    AvailabilityResponse,
    BulkUpdateRequest,
    CatalogMetricsResponse,
    ChangeProductIdRequest,
    ClearReviewFlagsRequest,
    EmployeeProductUpdateRequest,
    NextIdResponse,
    OkResponse,
    ProductCreateRequest,
    ProductDraftRequest,
    ProductListQuery,
    ProductListResponse,
    ProductUpdateRequest,
    PublishIssuesResponse,
    RecentlyViewedResponse,
    RecommendationsResponse,
    RejectProductRequest,
    SingleEmployeeProductResponse,
    SingleProductResponse,
    StorefrontProduct,
    StorefrontProductResponse,
)
from app.services.catalog.product_service import ProductService

router = APIRouter(tags=["Product Catalog"])


async def _employee_code_for_user(user: UserModel, db: AsyncSession) -> Optional[str]:
    res = await db.execute(
        select(EmployeeProfileModel.employee_code).where(EmployeeProfileModel.user_id == user.id)
    )
    return res.scalars().first()


# ===========================================================================
# PUBLIC / CUSTOMER — Catalogue
# ===========================================================================

@router.get(
    "/products",
    response_model=ProductListResponse,
    summary="Storefront product catalogue — filtered, sorted, paginated with facet counts",
    description=(
        "Gate: `PUBLISHED`, `published=true`, `category.status=ACTIVE` and — when set — "
        "`subcategory.status=ACTIVE`. A reference that resolves to no taxonomy row "
        "does not hide the product.  \n"
        "Facets: 12 (category, subcategory, gender, price, size, color, fabric, "
        "material, occasion, collection, rating, availability).  \n"
        "Sort: recommended (default), newest, price-asc, price-desc, discount, "
        "name-asc, popularity, rating. Aliases: price-low, price-high, name, az.  \n"
        "Pagination: `page` + `pageSize` (default 20)."
    ),
)
@cache(expire=TTL_PRODUCTS_LIST)
async def list_products(
    q: Optional[str] = Query(None, description="Full-text search"),
    category: Optional[List[str]] = Query(None),
    subcategory: Optional[List[str]] = Query(None),
    gender: Optional[List[str]] = Query(None),
    price: Optional[List[str]] = Query(None, description="Price band IDs"),
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
    query = ProductListQuery(
        q=q, category=category, subcategory=subcategory, gender=gender,
        price=price, size=size, color=color, fabric=fabric, material=material,
        occasion=occasion, collection=collection, rating=rating,
        availability=availability, sort=sort, page=page, pageSize=pageSize,
    )
    service = ProductService(db)
    result = await service.list_storefront_products(query)
    return ProductListResponse(**result)


@router.get(
    "/products/recently-viewed",
    response_model=RecentlyViewedResponse,
    summary="Get customer recently viewed products",
    description=(
        "Returns the recently viewed product list for the authenticated customer.  \n"
        "Guest recently-viewed is client-only; merging on sign-in is `BACKEND DECISION REQUIRED`."
    ),
)
async def get_recently_viewed(
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = ProductService(db)
    items = await service.get_recently_viewed(current_user.id)
    return RecentlyViewedResponse(items=items)


@router.post(
    "/products/recently-viewed",
    response_model=OkResponse,
    status_code=status.HTTP_200_OK,
    summary="Record a product as recently viewed",
)
async def add_recently_viewed(
    product_id: str = Query(..., alias="productId"),
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = ProductService(db)
    await service.add_recently_viewed(current_user.id, product_id)
    return OkResponse()


@router.get(
    "/products/{id_or_slug}",
    response_model=StorefrontProductResponse,
    summary="Product detail page — accepts product id or slug",
    description=(
        "Returns the full `StorefrontProduct` shape including `details`, `careInstructions`, "
        "`deliveryInfo`, `returnInfo`, `specifications`.  \n"
        "Only `PUBLISHED` products in an `ACTIVE` category and — when set — an `ACTIVE` "
        "subcategory are visible. Everything else, including a product that is "
        "APPROVED but not yet published, returns the canonical `404 NOT_FOUND`."
    ),
)
@cache(expire=TTL_PRODUCT_DETAIL)
async def get_product(
    id_or_slug: str,
    db: AsyncSession = Depends(get_db),
):
    service = ProductService(db)
    product = await service.get_storefront_product(id_or_slug)
    return StorefrontProductResponse(product=product)


@router.get(
    "/products/{id}/recommendations",
    response_model=RecommendationsResponse,
    summary="Product recommendations — related, complete-the-look, cart, recommended",
    description=(
        "Query param `type`: `related` | `complete-the-look` | `recommended` | `cart`.  \n"
        "Same visibility gate applies. Never returns the source product."
    ),
)
@cache(expire=TTL_RECOMMENDATIONS)
async def get_recommendations(
    id: str,
    type: str = Query("related", alias="type"),
    db: AsyncSession = Depends(get_db),
):
    service = ProductService(db)
    items = await service.get_recommendations(id, rec_type=type)
    return RecommendationsResponse(items=items)


# ===========================================================================
# CUSTOMER / EMPLOYEE — Submit for review
# ===========================================================================

@router.post(
    "/products/{id}/submit-review",
    response_model=SingleProductResponse,
    summary="Submit product for review — status → PENDING_REVIEW",
    description=(
        "Authorization: assigned employee **or** admin.  \n"
        "Effect: `status = PENDING_REVIEW`, `review.state = PENDING`."
    ),
)
async def submit_for_review(
    id: str,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.user_type == "customer":
        from app.core.exceptions import ForbiddenException
        raise ForbiddenException("Customers cannot submit products for review.")

    service = ProductService(db)
    actor_id = current_user.id

    if current_user.user_type == "employee":
        await require_permission_for_user(current_user, db, "products.manage")
        employee_code = await _employee_code_for_user(current_user, db)
        if not employee_code:
            from app.core.exceptions import ForbiddenException
            raise ForbiddenException("Employee profile is required for product workflow actions.")
        actor_id = employee_code
    elif current_user.user_type == "admin":
        await require_permission_for_user(current_user, db, "products.manage")
    else:
        from app.core.exceptions import ForbiddenException
        raise ForbiddenException("Employee or admin authentication required.")

    product = await service.submit_for_review(
        id,
        actor=actor_id,
        require_assignment=current_user.user_type == "employee",
        employee_user_id=current_user.id if current_user.user_type == "employee" else None,
    )
    return SingleProductResponse(product=product)


# ===========================================================================
# ADMIN — Products
# ===========================================================================

@router.get(
    "/admin/products",
    response_model=AdminProductListResponse,
    summary="Admin — full product catalogue list",
    description=(
        "Authorization: `products.view`.  \n"
        "Filters: `status`, `category`, `subcategory`, `assignedEmployeeId`, `q` "
        "(name / SKU / id / label / taxonomy / fabric).  \n"
        "Sort: `newest|oldest|name|price-asc|price-desc|status|updated`.  \n"
        "Pagination: `page` + `pageSize` (1–500); `total` is the FULL filtered "
        "count. Returns full records including `review`, `reviewFlags`, "
        "`history`, `assignedEmployeeId`."
    ),
)
async def admin_list_products(
    status_filter: Optional[str] = Query(None, alias="status"),
    category: Optional[str] = Query(None),
    subcategory: Optional[str] = Query(None),
    assignedEmployeeId: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    sort: str = Query("newest"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=500),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.view")
    query = AdminProductListQuery(
        status=status_filter,
        category=category,
        subcategory=subcategory,
        assignedEmployeeId=assignedEmployeeId,
        q=q,
        sort=sort,
        page=page,
        pageSize=pageSize,
    )
    service = ProductService(db)
    result = await service.list_admin_products(query)
    return AdminProductListResponse(**result)


@router.post(
    "/admin/products",
    response_model=SingleProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin — create product (runtime pf-<base36> id)",
    description="Authorization: `products.manage`. Starts as `DRAFT`.",
)
async def admin_create_product(
    req: ProductCreateRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.create_product(req, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.post(
    "/admin/products/draft",
    response_model=SingleProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin — create draft with caller-supplied permanent ID",
    description=(
        "Authorization: `products.manage`.  \n"
        "Body: `{ id, ...full supported product fields }`.  \n"
        "ID must match `^[A-Z0-9][A-Z0-9-]{1,35}$` (covers the canonical "
        "`PF-<DEPT>-<FAMILY>-<SUB>-NNNN` ids) and be unique — a taken ID is a 409.  \n"
        "Lifecycle/unsupported keys are rejected; every other supported field "
        "is persisted in the same call."
    ),
)
async def admin_create_draft(
    req: ProductDraftRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.create_draft(req, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.get(
    "/admin/products/next-id",
    response_model=NextIdResponse,
    summary="Admin — deterministic next stable product id",
    description=(
        "Source: `nextStableProductId()`.  \n"
        "Query: `category={categoryId}&preferredNumber={n}` (optional).  \n"
        "Never random; scans the register and picks the lowest free integer.  \n"
        "Canonical form: `PF-{CATEGORY_CODE}-{NNNN}` (four-digit serial)."
    ),
)
async def admin_get_next_id(
    category: str = Query(...),
    preferredNumber: Optional[int] = Query(None),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.view")
    service = ProductService(db)
    next_id = await service.get_next_id(category, preferred_number=preferredNumber)
    return NextIdResponse(nextId=next_id)


@router.get(
    "/admin/products/availability",
    response_model=AvailabilityResponse,
    summary="Admin — check SKU and slug availability",
    description=(
        "Query: `sku=&slug=&excludeId=`. Returns "
        "`{ skuTaken, slugTaken, suggestedSlug? }`.  \n"
        "Pre-flight only — the authoritative uniqueness verdict is the **409 "
        "`CONFLICT`** raised by the create/update endpoints, which use the same "
        "trimmed, case-insensitive comparison. Pass `excludeId` when editing an "
        "existing product so its own SKU/slug is reported as free."
    ),
)
async def admin_check_availability(
    sku: Optional[str] = Query(None),
    slug: Optional[str] = Query(None),
    excludeId: Optional[str] = Query(
        None, description="Product id to exclude — the product currently being edited."
    ),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.view")
    service = ProductService(db)
    result = await service.check_availability(sku=sku, slug=slug, exclude_id=excludeId)
    return AvailabilityResponse(**result)


@router.get(
    "/admin/products/metrics",
    response_model=CatalogMetricsResponse,
    summary="Admin — catalogue metrics (counts by status, review state, unassigned, blocked)",
)
async def admin_product_metrics(
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.view")
    service = ProductService(db)
    return await service.get_metrics()


@router.get(
    "/admin/workflow/metrics",
    response_model=CatalogMetricsResponse,
    summary="Admin — workflow metrics (compatibility alias of /admin/products/metrics)",
    description=(
        "Compatibility alias retained for existing workflow clients. It returns "
        "the same CatalogMetricsResponse and uses the same authorization and "
        "service path as `/admin/products/metrics`."
    ),
)
async def admin_workflow_metrics(
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.view")
    service = ProductService(db)
    return await service.get_metrics()


@router.post(
    "/admin/products/bulk",
    response_model=OkResponse,
    summary="Admin — bulk update products",
    description=(
        "Authorization: `products.manage`.  \n"
        "Body: `{ productIds: string[], updates: {} }`.  \n"
        "Applies the same field patches to all listed products."
    ),
)
async def admin_bulk_update(
    req: BulkUpdateRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    result = await service.bulk_update(req, actor=current_user.id)
    return OkResponse(message=f"Updated {result['updatedCount']} product(s).")


@router.get(
    "/admin/products/{id}",
    response_model=SingleProductResponse,
    summary="Admin — get full product record",
)
async def admin_get_product(
    id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.view")
    service = ProductService(db)
    product = await service.get_admin_product(id)
    return SingleProductResponse(product=product)


@router.patch(
    "/admin/products/{id}",
    response_model=SingleProductResponse,
    summary="Admin — full-field patch",
    description="Authorization: `products.manage`.",
)
async def admin_update_product(
    id: str,
    req: ProductUpdateRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.update_product(id, req, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.post(
    "/admin/products/{id}/assign",
    response_model=SingleProductResponse,
    summary="Admin — assign or unassign employee",
    description=(
        "Body: `{ employeeId: string | null }`.  \n"
        "`null` unassigns. Logs `PRODUCT_ASSIGNED` activity."
    ),
)
async def admin_assign_employee(
    id: str,
    req: AssignEmployeeRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.assign_employee(id, req, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.post(
    "/admin/products/{id}/approve",
    response_model=SingleProductResponse,
    summary="Admin — approve the review (does not publish)",
    description=(
        "Authorization: `products.manage`.  \n"
        "Precondition: product is pending review.  \n"
        "Effect: `review.state = APPROVED`; visibility stays PENDING_REVIEW. "
        "Going live requires the separate, gated publish action."
    ),
)
async def admin_approve_product(
    id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.approve_product(id, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.post(
    "/admin/products/{id}/reject",
    response_model=SingleProductResponse,
    summary="Admin — reject product → DRAFT",
    description=(
        "Body: `{ reason: string }`.  \n"
        "Effect: `status = DRAFT`, `review.state = REJECTED`, `rejectionReason = reason`."
    ),
)
async def admin_reject_product(
    id: str,
    req: RejectProductRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.reject_product(id, req, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.post(
    "/admin/products/{id}/publish",
    response_model=SingleProductResponse,
    summary="Admin — publish product",
    description=(
        "Authorization: `products.manage`.  \n"
        "Preconditions: review approved (`review.state == APPROVED`) AND no "
        "unresolved `getPublishIssues()`. Effect: `status = PUBLISHED`, "
        "`published = true`, `published_by/_at` recorded."
    ),
)
async def admin_publish_product(
    id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.publish_product(id, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.post(
    "/admin/products/{id}/unpublish",
    response_model=SingleProductResponse,
    summary="Admin — unpublish product → DRAFT",
)
async def admin_unpublish_product(
    id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.unpublish_product(id, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.post(
    "/admin/products/{id}/archive",
    response_model=SingleProductResponse,
    summary="Admin — archive product",
    description=(
        "Effect: `status = ARCHIVED`. **Removes the product from every customer surface** "
        "until restored. This must be surfaced to the operator before confirming."
    ),
)
async def admin_archive_product(
    id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.archive_product(id, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.post(
    "/admin/products/{id}/restore",
    response_model=SingleProductResponse,
    summary="Admin — restore archived product → DRAFT",
)
async def admin_restore_product(
    id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.restore_product(id, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.get(
    "/admin/products/{id}/publish-issues",
    response_model=PublishIssuesResponse,
    summary="Admin — live publish blocker list",
    description=(
        "Returns `{ issues: string[] }` — the same list that `approve` and `publish` "
        "check. Non-empty ⇒ the action buttons are disabled in the UI."
    ),
)
async def admin_publish_issues(
    id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.view")
    service = ProductService(db)
    issues = await service.get_publish_issues(id)
    return PublishIssuesResponse(issues=issues)


@router.post(
    "/admin/products/{id}/change-id",
    response_model=SingleProductResponse,
    summary="Admin — change permanent product ID",
    description=(
        "Body: `{ newId }` — validated `^[A-Z0-9][A-Z0-9-]{1,35}$`, must be free.  \n"
        "Activity `PRODUCT_RENAMED_ID`.  \n"
        "**Cascade question resolved (plan §24 step 8):** this route rewrites the "
        "human-facing **display label** (`productId`) only — never "
        "`catalog_product.id`, the primary key that media, inventory, collection "
        "membership and order history reference. No cascade is required, and none "
        "is performed. `newId` must collide with neither an existing primary key "
        "nor another product's display label, otherwise **409 `CONFLICT`**."
    ),
)
async def admin_change_product_id(
    id: str,
    req: ChangeProductIdRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.change_product_id(id, req, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.post(
    "/admin/products/{id}/duplicate",
    response_model=SingleProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin — duplicate a product",
    description="Creates a `DRAFT` copy with a new runtime id. Activity `PRODUCT_DUPLICATED`.",
)
async def admin_duplicate_product(
    id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.duplicate_product(id, actor=current_user.id)
    return SingleProductResponse(product=product)


@router.post(
    "/admin/products/{id}/review-flags/clear",
    response_model=SingleProductResponse,
    summary="Admin — clear specific review flags",
    description=(
        "Body: `{ flags: string[] }` — each flag must be a member of the declared "
        "review-flag vocabulary (plan §24 step 8); an unknown flag is a "
        "**422 `VALIDATION_ERROR`** naming it. Blocking flags: "
        "`NAME_REVIEW_REQUIRED`, `PRICE_REVIEW_REQUIRED`, `TAXONOMY_REVIEW_REQUIRED`, "
        "`GROUP_REVIEW_REQUIRED`, `VARIANT_REVIEW_REQUIRED`, `NEEDS_MEDIA`, "
        "`MEDIA_OWNERSHIP_REVIEW`, `CONFLICT_UNRESOLVED`, `KIDS_MIGRATION_REVIEW`. "
        "Informational flags: `CONFLICT_REVIEW_LATER`, `MEDIA_OWNERSHIP_MOVED`, "
        "`MEDIA_UNASSIGNED`.  \n"
        "Activity `PRODUCT_REVIEW_FLAGS_CLEARED`."
    ),
)
async def admin_clear_review_flags(
    id: str,
    req: ClearReviewFlagsRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "products.manage")
    service = ProductService(db)
    product = await service.clear_review_flags(id, req, actor=current_user.id)
    return SingleProductResponse(product=product)


# ===========================================================================
# EMPLOYEE — Products
# ===========================================================================

@router.get(
    "/employee/products/{id}",
    response_model=SingleEmployeeProductResponse,
    summary="Employee — get product record (employee projection)",
    description=(
        "Returns catalogue content, operational state, assignment and read-only media "
        "projections. Internal review history and actor/audit fields are not exposed."
    ),
)
async def employee_get_product(
    id: str,
    current_user: UserModel = Depends(get_current_employee),
    db: AsyncSession = Depends(get_db),
):
    service = ProductService(db)
    product = await service.get_employee_product(id)
    return SingleEmployeeProductResponse(product=product)


@router.patch(
    "/employee/products/{id}",
    response_model=SingleEmployeeProductResponse,
    summary="Employee — whitelisted 30-field patch",
    description=(
        "Authorization: `products.manage` AND `product.assignedEmployeeId === employee.employeeId` "
        "(SUPER_ADMIN bypasses).  \n"
        "Whitelist: name, price, compareAtPrice, description, shortDescription, category, subcategory, "
        "gender, fabric, material, primaryColor, secondaryColor, colors, patterns, work, occasion, sizes, "
        "season, fit, length, highlights, careInstructions, tags, stock, availability.  \n"
        "Unsupported fields are rejected with the canonical 422 validation envelope."
    ),
)
async def employee_update_product(
    id: str,
    req: EmployeeProductUpdateRequest,
    current_user: UserModel = Depends(get_current_employee),
    db: AsyncSession = Depends(get_db),
):
    await require_permission_for_user(current_user, db, "products.manage")
    employee_code = await _employee_code_for_user(current_user, db)
    if not employee_code:
        from app.core.exceptions import ForbiddenException
        raise ForbiddenException("Employee profile is required for product workflow actions.")

    service = ProductService(db)
    product = await service.update_product_employee(
        id,
        req,
        employee_id=employee_code,
        employee_user_id=current_user.id,
    )
    return SingleEmployeeProductResponse(product=product)
