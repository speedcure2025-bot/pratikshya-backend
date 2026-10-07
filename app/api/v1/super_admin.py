"""
Super Admin — exclusively Super-Admin-level endpoints.

All routes in this module are gated by `get_current_super_admin`, which
raises 403 for plain ADMIN accounts (and rejects every other user type).
Plain admins CANNOT reach these endpoints — no imperative guard call inside
the handler body is needed; the dependency enforces it at the FastAPI layer.

URL mapping:

  Settings (write — Super Admin only)
  ─────────────────────────────────────────────────────────────────────────────
  PATCH  /super-admin/settings/{section}        ← update section (deep-merge)
  POST   /super-admin/settings/{section}/reset  ← reset section to defaults
  POST   /super-admin/settings/reset            ← reset ALL sections

  Orders (recovery — Super Admin only)
  ─────────────────────────────────────────────────────────────────────────────
  POST   /super-admin/orders/{orderId}/force-status  ← bypass adjacency rules

Notes:
  - Settings reads (GET) are shared with plain admins and remain at
    GET /admin/settings[/{section}] in admin.py.
  - The `PATCH /admin/settings/{section}` and `POST /admin/settings/*/reset`
    routes that previously lived in admin.py have been removed; every old
    caller should be updated to the /super-admin/... prefix.
  - The force-status order endpoint previously at
    POST /admin/orders/{orderId}/force-status is now at
    POST /super-admin/orders/{orderId}/force-status.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.core.logging import get_logger
from app.dependencies import get_current_super_admin, get_db
from app.models.admin.setting import SettingModel
from app.models.auth.user import UserModel
from app.schemas.orders.order import AdminSingleOrderResponse, ForceStatusRequest
from app.services.orders.order_service import OrderService

logger = get_logger(__name__)

router = APIRouter(prefix="/super-admin", tags=["Super Admin"])

# Settings catalogue shared with admin.py
from app.core.settings_catalog import KNOWN_SECTIONS, merge_defaults  # noqa: E402


def _merge_defaults(section: str, stored: dict) -> dict:
    return merge_defaults(section, stored)


def _deep_merge_dicts(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*.

    For every key in *override*:
    - If both the existing value and the new value are dicts, recurse.
    - Otherwise the new value replaces the existing one.

    This mirrors the ``_deep_merge`` closure inside ``merge_defaults`` but
    operates on two arbitrary dicts so it can be used to persist the merged
    stored value (not just produce a read-time view against the defaults).
    """
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge_dicts(result[k], v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SettingsPatchRequest(BaseModel):
    data: Dict[str, Any]


# ===========================================================================
# SETTINGS — Write (Super Admin only)
# ===========================================================================

@router.patch(
    "/settings/{section}",
    summary="Update a settings section (Super Admin only)",
    description=(
        "Body: `{ data: { ... } }` — partial patch, deep-merged against the "
        "current stored value.  \n"
        "Authorization: **SUPER_ADMIN only** — plain Admin accounts receive 403."
    ),
)
async def update_settings_section(
    section: str,
    req: SettingsPatchRequest,
    current_user: UserModel = Depends(get_current_super_admin),
    db: AsyncSession = Depends(get_db),
):
    if section not in KNOWN_SECTIONS:
        raise NotFoundException(message=f"Unknown settings section '{section}'.")

    stmt = select(SettingModel).where(SettingModel.id == section)
    result = await db.execute(stmt)
    row = result.scalars().first()

    if row:
        # Deep-merge: req.data keys are layered onto the current stored value
        # at every nesting level — avoids wiping sibling keys inside nested
        # dicts (e.g. patching only attendance.startTime must not erase
        # attendance.endTime that was already stored).
        row.value = _deep_merge_dicts(dict(row.value or {}), req.data)
        row.updated_by = current_user.id
    else:
        row = SettingModel(id=section, value=req.data, updated_by=current_user.id)
        db.add(row)

    await db.flush()
    return {"ok": True, "section": section, "data": _merge_defaults(section, row.value)}


@router.post(
    "/settings/{section}/reset",
    summary="Reset a settings section to defaults (Super Admin only)",
    description=(
        "Clears all stored overrides for `section`, reverting it to the "
        "compiled-in defaults.  \n"
        "Authorization: **SUPER_ADMIN only**."
    ),
)
async def reset_settings_section(
    section: str,
    current_user: UserModel = Depends(get_current_super_admin),
    db: AsyncSession = Depends(get_db),
):
    if section not in KNOWN_SECTIONS:
        raise NotFoundException(message=f"Unknown settings section '{section}'.")

    stmt = select(SettingModel).where(SettingModel.id == section)
    result = await db.execute(stmt)
    row = result.scalars().first()
    if row:
        row.value = {}
        row.updated_by = current_user.id
        await db.flush()

    return {"ok": True, "section": section, "data": _merge_defaults(section, {})}


@router.post(
    "/settings/reset",
    summary="Reset ALL settings sections to defaults (Super Admin only)",
    description=(
        "Removes every stored settings override across all sections.  \n"
        "Authorization: **SUPER_ADMIN only**."
    ),
)
async def reset_all_settings(
    current_user: UserModel = Depends(get_current_super_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SettingModel)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    for row in rows:
        row.value = {}
        row.updated_by = current_user.id
    # flush() stages all mutations in the current transaction; the actual
    # COMMIT happens when get_db's context manager exits normally after the
    # response is returned. This is the project-wide flush/commit convention —
    # the response dict is built from in-memory state, not a second DB read,
    # so it is consistent with what will be committed.
    await db.flush()
    return {"ok": True, "message": "All settings reset to defaults."}


# ===========================================================================
# DASHBOARD — Summary (Super Admin — mirrors /admin/dashboard/summary)
# ===========================================================================

@router.get(
    "/dashboard/summary",
    summary="One consolidated super-admin dashboard read (SUPER_ADMIN only)",
    description=(
        "Single request feeding the Super Admin dashboard. Delegates to the "
        "same analytics summary as the Admin dashboard so metric definitions "
        "stay in one place.  \n"
        "Authorization: **SUPER_ADMIN only** — plain Admin accounts receive 403."
    ),
)
async def get_super_admin_dashboard_summary(
    days: int = Query(default=7, ge=1, le=90),
    recent_limit: int = Query(default=5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_super_admin),
):
    """Expose the dashboard summary at GET /super-admin/dashboard/summary.

    The implementation lives under analytics so aggregates are not duplicated.
    Gated by get_current_super_admin — plain ADMIN tokens receive 403.

    Note on the direct call: admin_dashboard_summary is called as a plain
    async function, not via FastAPI routing, so its Depends(...) defaults are
    NOT resolved by the framework — db and current_user are passed explicitly
    as keyword arguments above. The internal require_admin_permission call
    inside admin_dashboard_summary checks for "analytics.view"; SUPER_ADMIN
    satisfies this via the wildcard grant ("*"), so the check always passes.
    If admin_dashboard_summary's internal permission check changes, this
    call site must be reviewed.
    """
    from app.api.v1.analytics import admin_dashboard_summary

    return await admin_dashboard_summary(
        days=days,
        recent_limit=recent_limit,
        db=db,
        current_user=current_user,
    )


# ===========================================================================
# ORDERS — Force-status (Super Admin only)
# ===========================================================================

@router.post(
    "/orders/{order_id}/force-status",
    response_model=AdminSingleOrderResponse,
    summary="Force an order status transition (bypasses adjacency map)",
    description=(
        "**Bypasses `ORDER_TRANSITIONS`** — use only when manual recovery is required.  \n"
        "Body: `{ status, reason }` — `reason` is mandatory and always written to the "
        "audit trail.  \n"
        "Authorization: **SUPER_ADMIN only**."
    ),
)
async def admin_force_status(
    order_id: str,
    req: ForceStatusRequest,
    current_user: UserModel = Depends(get_current_super_admin),
    db: AsyncSession = Depends(get_db),
):
    service = OrderService(db)
    order = await service.force_status(order_id, req, actor_id=current_user.id)
    return AdminSingleOrderResponse(order=order)
