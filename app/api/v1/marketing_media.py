"""
Marketing media API — B-02.

Admin CRUD for backend-managed marketing placements (HOME_HERO etc.) and
public retrieval for homepage.

Routes:

  Admin (requires admin + media.view/upload/assign):
    GET    /admin/marketing/media              list (optional placement, activeOnly)
    POST   /admin/marketing/media              create
    GET    /admin/marketing/media/{id}         get one
    PATCH  /admin/marketing/media/{id}         update
    DELETE /admin/marketing/media/{id}         delete
    PUT    /admin/marketing/media/reorder      reorder (placement + items)

  Storefront (public, no auth):
    GET    /marketing/placements/{placement}   active entries for placement
    GET    /marketing/hero                     alias for HOME_HERO active

All media URLs are resolved via build_media_url (canonical /api/v1/media/objects/...),
never /images/... bypass.

Authorization follows existing admin permission model:
  - media.view for read
  - media.upload / media.assign for write
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_admin, get_db, require_admin_permission
from app.models.auth.user import UserModel
from app.schemas.media.marketing import (
    HOME_HERO_PLACEMENT,
    MarketingMediaCreate,
    MarketingMediaListResponse,
    MarketingMediaReorderRequest,
    MarketingMediaReorderResponse,
    MarketingMediaResponse,
    MarketingMediaUpdate,
)
from app.services.media.marketing_media_service import MarketingMediaService

# ---------------------------------------------------------------------------
# Admin router
# ---------------------------------------------------------------------------

admin_router = APIRouter(prefix="/admin/marketing", tags=["Admin Marketing Media"])


@admin_router.get(
    "/media",
    response_model=MarketingMediaListResponse,
    summary="List marketing media (admin)",
)
async def admin_list_marketing_media(
    placement: Optional[str] = Query(None, description="Filter by placement, e.g. HOME_HERO"),
    active_only: bool = Query(False, alias="activeOnly"),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(current_user, db, "media.view")
    service = MarketingMediaService(db)
    rows = await service.list(placement=placement, active_only=active_only)
    items = service.to_response_list(rows)
    return {
        "ok": True,
        "items": items,
        "total": len(items),
        "placement": placement,
    }


@admin_router.post(
    "/media",
    response_model=MarketingMediaResponse,
    status_code=201,
    summary="Create marketing media entry (admin)",
)
async def admin_create_marketing_media(
    payload: MarketingMediaCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(current_user, db, "media.upload")
    service = MarketingMediaService(db)
    row = await service.create(payload, actor_id=current_user.id)
    return service.to_response(row)


@admin_router.get(
    "/media/{media_id}",
    response_model=MarketingMediaResponse,
    summary="Get one marketing media entry (admin)",
)
async def admin_get_marketing_media(
    media_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(current_user, db, "media.view")
    service = MarketingMediaService(db)
    row = await service.get_by_id(media_id)
    return service.to_response(row)


@admin_router.patch(
    "/media/{media_id}",
    response_model=MarketingMediaResponse,
    summary="Update marketing media entry (admin)",
)
async def admin_update_marketing_media(
    media_id: str,
    payload: MarketingMediaUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(current_user, db, "media.assign")
    service = MarketingMediaService(db)
    row = await service.update(media_id, payload, actor_id=current_user.id)
    return service.to_response(row)


@admin_router.delete(
    "/media/{media_id}",
    summary="Delete marketing media entry (admin)",
)
async def admin_delete_marketing_media(
    media_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(current_user, db, "media.delete")
    service = MarketingMediaService(db)
    await service.delete(media_id)
    return {"ok": True, "deleted": media_id}


@admin_router.put(
    "/media/reorder",
    response_model=MarketingMediaReorderResponse,
    summary="Reorder marketing media entries (admin)",
)
async def admin_reorder_marketing_media(
    payload: MarketingMediaReorderRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(current_user, db, "media.assign")
    service = MarketingMediaService(db)
    # Convert Pydantic items to dicts for service
    items = [{"id": item.id, "sort_order": item.sort_order} for item in payload.items]
    rows = await service.reorder(payload.placement, items, actor_id=current_user.id)
    return {"ok": True, "items": service.to_response_list(rows)}


# ---------------------------------------------------------------------------
# Public/storefront router
# ---------------------------------------------------------------------------

public_router = APIRouter(prefix="/marketing", tags=["Marketing Media"])


@public_router.get(
    "/placements/{placement}",
    response_model=MarketingMediaListResponse,
    summary="Get active marketing media for a placement (public)",
)
async def public_list_placement(
    placement: str,
    db: AsyncSession = Depends(get_db),
):
    service = MarketingMediaService(db)
    rows = await service.list(placement=placement, active_only=True)
    items = service.to_response_list(rows)
    return {
        "ok": True,
        "items": items,
        "total": len(items),
        "placement": placement,
    }


@public_router.get(
    "/hero",
    response_model=MarketingMediaListResponse,
    summary="Get active HOME_HERO entries (public)",
)
async def public_get_home_hero(
    db: AsyncSession = Depends(get_db),
):
    service = MarketingMediaService(db)
    rows = await service.list_active_home_hero()
    items = service.to_response_list(rows)
    return {
        "ok": True,
        "items": items,
        "total": len(items),
        "placement": HOME_HERO_PLACEMENT,
    }


# Combined router for inclusion
router = APIRouter()
router.include_router(admin_router)
router.include_router(public_router)
