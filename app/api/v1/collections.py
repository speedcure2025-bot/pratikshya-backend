"""
Collections — API router.

URL mapping (API_CONTRACT.md § COLLECTIONS → implementation):

  Public / Customer
  ─────────────────────────────────────────────────────────────────────────────
  GET  /collections                               ← list active collections
  GET  /collections/{idOrSlug}                   ← single active collection
  GET  /collections/{id}/products                ← products filtered by collection

  Admin
  ─────────────────────────────────────────────────────────────────────────────
  GET  /admin/collections                         ← list all (no status gate)
  GET  /admin/collections/{id}                    ← single (no status gate)
  POST /admin/collections                         ← create collection
  PATCH /admin/collections/{id}                   ← update collection
  POST /admin/collections/{id}/activate           ← status → ACTIVE
  POST /admin/collections/{id}/pause              ← status → PAUSED
  POST /admin/collections/{id}/archive            ← status → ARCHIVED
  POST /admin/collections/{id}/restore            ← status → DRAFT
  PUT  /admin/collections/{id}/products           ← replace explicit product list

  Taxonomy metrics (shared with categories)
  ─────────────────────────────────────────────────────────────────────────────
  GET  /admin/taxonomy/metrics                    ← counts by status
  GET  /admin/taxonomy/product-counts             ← per-collection product counts

Notes:
  - GET /collections/{id}/products delegates to ProductService.list_storefront_products()
    with the resolved product IDs pre-applied so all 12 facets, sort aliases,
    and pagination remain consistent with GET /products.
  - Route order: /{id}/products registered BEFORE /{idOrSlug} to prevent path ambiguity.
  - displayStatus is DERIVED server-side from (status, startDate, endDate) —
    it is never stored blindly.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_admin, get_db, require_admin_permission
from app.models.auth.user import UserModel
from app.schemas.catalog.collection import (
    AssignProductsRequest,
    CollectionCreateRequest,
    CollectionListResponse,
    CollectionUpdateRequest,
    OkResponse,
    SingleCollectionResponse,
    TaxonomyMetricsResponse,
    TaxonomyProductCountsResponse,
)
from app.api.v1.response_docs import canonical_error_responses
from app.schemas.catalog.product import ProductListQuery, ProductListResponse
from app.services.catalog.collection_service import CollectionService
from app.services.catalog.product_service import ProductService

router = APIRouter(tags=["Collections"])


# ===========================================================================
# PUBLIC — Products for a collection  (BEFORE /{idOrSlug} to avoid collision)
# ===========================================================================

@router.get(
    "/collections/{collection_id}/products",
    response_model=ProductListResponse,
    summary="Products in a collection — full facet/sort/pagination support",
    description=(
        "Public endpoint. Resolves the collection's member product IDs "
        "(MANUAL explicit list + label match + RULE_BASED evaluation), "
        "then applies the full catalogue query with facets, sort, and pagination.  \n"
        "Gate: `PUBLISHED`, `published=true`. Collection must be `ACTIVE`."
    ),
)
async def get_collection_products(
    collection_id: str,
    q: Optional[str] = Query(None),
    category: Optional[List[str]] = Query(None),
    subcategory: Optional[List[str]] = Query(None),
    gender: Optional[List[str]] = Query(None),
    price: Optional[List[str]] = Query(None),
    size: Optional[List[str]] = Query(None),
    color: Optional[List[str]] = Query(None),
    fabric: Optional[List[str]] = Query(None),
    material: Optional[List[str]] = Query(None),
    occasion: Optional[List[str]] = Query(None),
    rating: Optional[List[str]] = Query(None),
    availability: Optional[List[str]] = Query(None),
    sort: str = Query("recommended"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200, alias="pageSize"),
    db: AsyncSession = Depends(get_db),
):
    col_service = CollectionService(db)
    col_model, resolved_ids = await col_service.get_collection_product_ids(collection_id)

    query = ProductListQuery(
        q=q,
        category=category,
        # The resolved IDs are already the authoritative collection filter.
        # Do not also send the collection facet: product responses expose
        # collection names, so filtering that projection by the collection ID
        # would discard valid explicit associations.
        collection=None,
        subcategory=subcategory,
        gender=gender,
        price=price,
        size=size,
        color=color,
        fabric=fabric,
        material=material,
        occasion=occasion,
        rating=rating,
        availability=availability,
        sort=sort,
        page=page,
        pageSize=pageSize,
        # Inform the service of the pre-resolved ids so it can restrict to them.
        collection_product_ids=resolved_ids,
    )
    prod_service = ProductService(db)
    result = await prod_service.list_storefront_products(query)
    return ProductListResponse(**result)


# ===========================================================================
# PUBLIC — List & single collection  (AFTER /{id}/products)
# ===========================================================================

@router.get(
    "/collections",
    response_model=CollectionListResponse,
    summary="List active collections",
    description=(
        "Public endpoint.  \n"
        "Default filter: `status=ACTIVE`.  \n"
        "Optional filter: `featured=true/false`.  \n"
        "Sorted by `sortOrder` ASC, then `name` ASC.  \n"
        "Response shape: `[{ id, name, slug, eyebrow, description, image, "
        "heroMediaId, thumbnailMediaId, type, status, displayStatus, featured, "
        "sortOrder, startDate, endDate, rule, explicitProductIds[], resolvedProductCount }]`"
    ),
)
async def list_collections(
    status_filter: Optional[str] = Query(
        "ACTIVE",
        alias="status",
        description="DRAFT | SCHEDULED | ACTIVE | PAUSED | EXPIRED | ARCHIVED. Default: ACTIVE.",
    ),
    featured: Optional[bool] = Query(None, description="Filter by featured flag."),
    db: AsyncSession = Depends(get_db),
):
    service = CollectionService(db)
    items = await service.list_collections(status_filter=status_filter, featured=featured)
    return CollectionListResponse(items=items)


@router.get(
    "/collections/{id_or_slug}",
    response_model=SingleCollectionResponse,
    summary="Get a single collection by id or slug",
    description=(
        "Public endpoint. Returns `404` if the collection is not `ACTIVE`.  \n"
        "`displayStatus` is derived from `(status, startDate, endDate)` "
        "server-side and must not be stored blindly."
    ),
)
async def get_collection(
    id_or_slug: str,
    db: AsyncSession = Depends(get_db),
):
    service = CollectionService(db)
    collection = await service.get_collection(id_or_slug)
    return SingleCollectionResponse(collection=collection)


# ===========================================================================
# ADMIN — List & single (no status gate)
# ===========================================================================

@router.get(
    "/admin/collections",
    response_model=CollectionListResponse,
    summary="Admin — list all collections (no status gate)",
    description=(
        "Authorization: `collections.view` (admin).  \n"
        "Optional filters: `status`, `featured`, `q` (name search).  \n"
        "Returns all collections including DRAFT, ARCHIVED, etc."
    ),
)
async def admin_list_collections(
    status_filter: Optional[str] = Query(None, alias="status"),
    featured: Optional[bool] = Query(None),
    q: Optional[str] = Query(None),
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "collections.view")
    service = CollectionService(db)
    items = await service.admin_list_collections(
        status_filter=status_filter, featured=featured, q=q
    )
    return CollectionListResponse(items=items)


@router.get(
    "/admin/collections/{collection_id}",
    response_model=SingleCollectionResponse,
    summary="Admin — get a collection by id (no status gate)",
)
async def admin_get_collection(
    collection_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "collections.view")
    service = CollectionService(db)
    collection = await service.admin_get_collection(collection_id)
    return SingleCollectionResponse(collection=collection)


# ===========================================================================
# ADMIN — Create & update
# ===========================================================================

@router.post(
    "/admin/collections",
    response_model=SingleCollectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin — create a collection",
    description=(
        "Authorization: `collections.create`.  \n"
        "Starts with `status = DRAFT`.  \n"
        "Slug is auto-derived from `name` if omitted.  \n"
        "`type` must be `MANUAL` or `RULE_BASED`.  \n"
        "Activity: `COLLECTION_CREATED`."
    ),
)
async def admin_create_collection(
    req: CollectionCreateRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "collections.create")
    service = CollectionService(db)
    collection = await service.create_collection(req, actor=current_user.id)
    return SingleCollectionResponse(collection=collection)


@router.patch(
    "/admin/collections/{collection_id}",
    response_model=SingleCollectionResponse,
    summary="Admin — update a collection",
    description=(
        "Authorization: `collections.edit`.  \n"
        "Partial patch — omit any field to leave it unchanged.  \n"
        "Activity: `COLLECTION_UPDATED`."
    ),
)
async def admin_update_collection(
    collection_id: str,
    req: CollectionUpdateRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "collections.edit")
    service = CollectionService(db)
    collection = await service.update_collection(collection_id, req, actor=current_user.id)
    return SingleCollectionResponse(collection=collection)


# ===========================================================================
# ADMIN — Lifecycle actions
# ===========================================================================

@router.post(
    "/admin/collections/{collection_id}/activate",
    response_model=SingleCollectionResponse,
    summary="Admin — activate a collection → ACTIVE",
    description=(
        "Authorization: `collections.edit`.  \n"
        "ARCHIVED collections must be restored before activating.  \n"
        "Activity: `COLLECTION_ACTIVATED`."
    ),
)
async def admin_activate_collection(
    collection_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "collections.edit")
    service = CollectionService(db)
    collection = await service.activate_collection(collection_id, actor=current_user.id)
    return SingleCollectionResponse(collection=collection)


@router.post(
    "/admin/collections/{collection_id}/pause",
    response_model=SingleCollectionResponse,
    summary="Admin — pause a collection → PAUSED",
    description=(
        "Authorization: `collections.edit`.  \n"
        "Only `ACTIVE` or `SCHEDULED` collections can be paused.  \n"
        "Activity: `COLLECTION_PAUSED`."
    ),
)
async def admin_pause_collection(
    collection_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "collections.edit")
    service = CollectionService(db)
    collection = await service.pause_collection(collection_id, actor=current_user.id)
    return SingleCollectionResponse(collection=collection)


@router.post(
    "/admin/collections/{collection_id}/archive",
    response_model=SingleCollectionResponse,
    summary="Admin — archive a collection → ARCHIVED",
    description=(
        "Authorization: `collections.archive`.  \n"
        "Archived collections are hidden from all customer surfaces.  \n"
        "Activity: `COLLECTION_ARCHIVED`."
    ),
)
async def admin_archive_collection(
    collection_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "collections.archive")
    service = CollectionService(db)
    collection = await service.archive_collection(collection_id, actor=current_user.id)
    return SingleCollectionResponse(collection=collection)


@router.post(
    "/admin/collections/{collection_id}/restore",
    response_model=SingleCollectionResponse,
    summary="Admin — restore an archived collection → DRAFT",
    description=(
        "Authorization: `collections.archive`.  \n"
        "Only `ARCHIVED` collections can be restored.  \n"
        "Activity: `COLLECTION_RESTORED`."
    ),
)
async def admin_restore_collection(
    collection_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "collections.archive")
    service = CollectionService(db)
    collection = await service.restore_collection(collection_id, actor=current_user.id)
    return SingleCollectionResponse(collection=collection)


# ===========================================================================
# ADMIN — Assign products to a MANUAL collection
# ===========================================================================

@router.put(
    "/admin/collections/{collection_id}/products",
    response_model=SingleCollectionResponse,
    summary="Admin — replace explicit product list (MANUAL collections only)",
    description=(
        "Authorization: `collections.assign`.  \n"
        "Body: `{ productIds: string[] }` — **replaces** the full explicit product list.  \n"
        "Sources: `assignProductsToCollection`, `addProductsToCollection`, "
        "`removeProductsFromCollection`.  \n"
        "Only works for `MANUAL` collections; use PATCH to update the rule on `RULE_BASED`.  \n"
        "Activity: `COLLECTION_PRODUCTS_UPDATED`."
    ),
)
async def admin_assign_products(
    collection_id: str,
    req: AssignProductsRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "collections.assign")
    service = CollectionService(db)
    collection = await service.assign_products(collection_id, req, actor=current_user.id)
    return SingleCollectionResponse(collection=collection)


# ===========================================================================
# ADMIN — Taxonomy metrics
# ===========================================================================

@router.get(
    "/admin/taxonomy/metrics",
    response_model=TaxonomyMetricsResponse,
    summary="Admin — taxonomy metrics (collection / category / subcategory counts by status)",
    description="Sources: `metrics()`, `productCounts()`. Returns counts for all taxonomy entities.",
    responses=canonical_error_responses(401, 403, 500),
)
async def admin_taxonomy_metrics(
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "collections.view")
    service = CollectionService(db)
    return await service.taxonomy_metrics()


@router.get(
    "/admin/taxonomy/product-counts",
    response_model=TaxonomyProductCountsResponse,
    summary="Admin — per-collection resolved product counts",
    description=(
        "Sources: `productCounts()`, `collectionsForProduct()`, `isProductInCollection()`.  \n"
        "Returns `{ counts: [{ collectionId, name, productCount }] }`."
    ),
    responses=canonical_error_responses(401, 403, 500),
)
async def admin_taxonomy_product_counts(
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "collections.view")
    service = CollectionService(db)
    return await service.taxonomy_product_counts()
