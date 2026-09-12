"""
Admin — Settings, Analytics snapshot, Activity log, Roles.

URL mapping (API_CONTRACT.md § ADMIN → implementation):

  Settings
  ─────────────────────────────────────────────────────────────────────────────
  GET    /admin/settings                  ← all sections (deep-merged defaults)
  GET    /admin/settings/{section}        ← single section
  PATCH  /admin/settings/{section}        ← update section (Super Admin)
  POST   /admin/settings/{section}/reset  ← reset section to defaults
  POST   /admin/settings/reset            ← reset ALL sections to defaults

  Activity log
  ─────────────────────────────────────────────────────────────────────────────
  GET    /admin/activity                  ← shared audit diary (latest 200)

  Roles
  ─────────────────────────────────────────────────────────────────────────────
  GET    /admin/roles                     ← list 8 built-in roles
  GET    /admin/roles/{roleId}            ← single role with default permissions

  Dashboard
  ─────────────────────────────────────────────────────────────────────────────
  GET    /admin/dashboard/summary         ← consolidated dashboard aggregates
                                          (same payload as the analytics
                                          implementation; this is the path
                                          the Admin portal actually calls)

Notes:
  - Settings are deep-merged against SETTINGS_DEFAULTS on every read.
  - Unknown section names return { error: "Unknown settings section" } per spec.
  - Roles are static in this release; the canonical permission vocabulary is
    fixed at startup (roles-permissions.json equivalent).
"""

import copy
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.core.logging import get_logger
from app.dependencies import get_current_admin, get_db, require_permission_for_user, require_super_admin_user
from app.models.admin.setting import SettingModel
from app.models.auth.user import UserModel
from app.services.media.media_validation import allowed_image_extensions

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin Business Config"])


# Settings catalogue: ONE source for sections + defaults (shared with the
# workforce services — see app/core/settings_catalog.py; extracted 2026-09 so
# punch rules can never drift from what the Admin settings surface serves).
from app.core.settings_catalog import KNOWN_SECTIONS, SETTINGS_DEFAULTS, merge_defaults

def _merge_defaults(section: str, stored: dict) -> dict:
    return merge_defaults(section, stored)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SettingsPatchRequest(BaseModel):
    data: Dict[str, Any]


# ---------------------------------------------------------------------------
# STATIC ROLES (mirrors roles-permissions.json)
#
# CONSOLIDATION (2026-09): the catalog moved to `app.core.rbac` — the single
# canonical role vocabulary shared by the RBAC fallback, the auth service and
# the seed script. Business roles are keyed by their persisted canonical
# names (STORE_MANAGER, SALES_EXECUTIVE, …); the former Admin-portal keys
# (MANAGER, SALES, INVENTORY, WAREHOUSE, CS, STYLIST) remain as alias entries
# pointing at the same definitions. Re-exported here so every existing
# `from app.api.v1.admin import BUILT_IN_ROLES` import site keeps working.
# ---------------------------------------------------------------------------

from app.core.rbac import BUILT_IN_ROLES  # noqa: E402  (re-export — compat seam)


# ===========================================================================
# SETTINGS
# ===========================================================================

@router.get(
    "/settings",
    summary="Get all settings sections (merged with defaults)",
    description=(
        "Returns all 19 sections deep-merged against `SETTINGS_DEFAULTS`.  \n"
        "Authorization: Admin."
    ),
)
async def get_all_settings(
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_permission_for_user(current_user, db, "settings.view")
    stmt = select(SettingModel)
    result = await db.execute(stmt)
    rows = {row.id: row.value for row in result.scalars().all()}

    merged = {}
    for section in KNOWN_SECTIONS:
        merged[section] = _merge_defaults(section, rows.get(section, {}))

    return {"ok": True, "settings": merged}


@router.get(
    "/settings/{section}",
    summary="Get a single settings section",
)
async def get_settings_section(
    section: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_permission_for_user(current_user, db, "settings.view")
    if section not in KNOWN_SECTIONS:
        raise NotFoundException(message=f"Unknown settings section '{section}'.")

    stmt = select(SettingModel).where(SettingModel.id == section)
    result = await db.execute(stmt)
    row = result.scalars().first()
    stored = row.value if row else {}

    return {"ok": True, "section": section, "data": _merge_defaults(section, stored)}


@router.patch(
    "/settings/{section}",
    summary="Update a settings section (Super Admin)",
    description=(
        "Body: `{ data: { ... } }` — partial patch, deep-merged against current value.  \n"
        "Authorization: Super Admin only."
    ),
)
async def update_settings_section(
    section: str,
    req: SettingsPatchRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_super_admin_user(current_user, db)
    if section not in KNOWN_SECTIONS:
        raise NotFoundException(message=f"Unknown settings section '{section}'.")

    stmt = select(SettingModel).where(SettingModel.id == section)
    result = await db.execute(stmt)
    row = result.scalars().first()

    if row:
        # Deep-merge incoming patch on top of current stored value
        current_value = dict(row.value or {})
        current_value.update(req.data)
        row.value = current_value
        row.updated_by = current_user.id
    else:
        row = SettingModel(id=section, value=req.data, updated_by=current_user.id)
        db.add(row)

    await db.flush()
    return {"ok": True, "section": section, "data": _merge_defaults(section, row.value)}


@router.post(
    "/settings/{section}/reset",
    summary="Reset a settings section to defaults",
)
async def reset_settings_section(
    section: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_super_admin_user(current_user, db)
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
    summary="Reset ALL settings sections to defaults",
    description="Authorization: Super Admin only. Removes all stored overrides.",
)
async def reset_all_settings(
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_super_admin_user(current_user, db)
    stmt = select(SettingModel)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    for row in rows:
        row.value = {}
        row.updated_by = current_user.id
    await db.flush()
    return {"ok": True, "message": "All settings reset to defaults."}


# ===========================================================================
# ACTIVITY LOG
# ===========================================================================

@router.get(
    "/activity",
    summary="Admin — shared activity diary (latest 200 entries)",
    description=(
        "Returns the most recent 200 audit log entries across all domains.  \n"
        "Authorization: Admin."
    ),
)
async def get_activity_log(
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.models.audit.activity_log import ActivityLogModel
    from sqlalchemy import select as sa_select, inspect as sa_inspect
    from sqlalchemy import text

    # Gracefully handle the case where new columns haven't been migrated yet
    try:
        stmt = (
            sa_select(ActivityLogModel)
            .order_by(ActivityLogModel.created_at.desc())
            .limit(200)
        )
        result = await db.execute(stmt)
        logs = result.scalars().all()

        return {
            "ok": True,
            "activity": [
                {
                    "id":                log.id,
                    "at":                log.created_at.isoformat(),
                    "actorEmployeeId":   getattr(log, "actor_employee_id", None),
                    "actorName":         getattr(log, "actor_name", None),
                    "targetProductId":   getattr(log, "target_product_id", None),
                    "targetOfferId":     getattr(log, "target_offer_id", None),
                    "targetCategoryId":  getattr(log, "target_category_id", None),
                    "targetCollectionId": getattr(log, "target_collection_id", None),
                    "targetOrderId":     getattr(log, "target_order_id", None),
                    "action":            getattr(log, "action", None),
                    "summary":           getattr(log, "summary", None),
                }
                for log in logs
            ],
        }
    except Exception:
        # Columns don't exist yet (pending migration) — return empty diary
        logger.warning("Activity log query failed — likely a pending migration", exc_info=True)
        return {"ok": True, "activity": []}


# ===========================================================================
# ROLES / CAPABILITIES
# ===========================================================================

@router.get(
    "/roles",
    summary="List built-in roles",
    description=(
        "Returns the consolidated role catalogue with its default permission "
        "sets. Legacy Admin-portal aliases (MANAGER, SALES, …) resolve to the "
        "canonical business-role entries and are not listed twice."
    ),
)
async def list_roles(
    current_user: UserModel = Depends(get_current_admin),
):
    seen = set()
    roles = []
    for role in BUILT_IN_ROLES.values():
        if role["id"] in seen:
            continue
        seen.add(role["id"])
        roles.append(role)
    return {"ok": True, "roles": roles}


@router.get(
    "/roles/{role_id}",
    summary="Get a single role",
)
async def get_role(
    role_id: str,
    current_user: UserModel = Depends(get_current_admin),
):
    role = BUILT_IN_ROLES.get(role_id.upper())
    if not role:
        raise NotFoundException(f"Role '{role_id}' not found.")
    return {"ok": True, "role": role}


@router.get(
    "/capabilities",
    summary="List capability groups + account hierarchy contract",
    description=(
        "The canonical authorization vocabulary shared with the frontend "
        "(account levels, creation matrix, capability groups, business-role "
        "defaults). Read-only; any authenticated admin surface account."
    ),
)
async def list_capabilities(
    current_user: UserModel = Depends(get_current_admin),
):
    from app.core.rbac import ACCOUNT_LEVELS, ALL_CAPABILITIES, CAPABILITY_GROUPS, CREATABLE_LEVELS
    return {
        "ok": True,
        "accountLevels": list(ACCOUNT_LEVELS),
        "creatableLevels": {level: sorted(levels) for level, levels in CREATABLE_LEVELS.items()},
        "capabilities": list(ALL_CAPABILITIES),
        "capabilityGroups": CAPABILITY_GROUPS,
    }


# ===========================================================================
# DASHBOARD — canonical portal path
# ===========================================================================

@router.get(
    "/dashboard/summary",
    summary="One consolidated admin-dashboard read (admin)",
    description=(
        "Single request feeding the Admin dashboard. Delegates to the existing "
        "analytics summary so metric definitions stay in one place.  \n"
        "Authorization: `analytics.view`."
    ),
)
async def get_admin_dashboard_summary(
    days: int = Query(default=7, ge=1, le=90),
    recent_limit: int = Query(default=5, ge=1, le=20),
    recentLimit: Optional[int] = Query(default=None, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    """Expose the dashboard summary at GET /admin/dashboard/summary.

    The implementation lives under analytics so aggregates are not duplicated.
    The handler is imported lazily to avoid a circular import at module load.
    """
    from app.api.v1.analytics import admin_dashboard_summary

    return await admin_dashboard_summary(
        days=days,
        recent_limit=recentLimit if recentLimit is not None else recent_limit,
        db=db,
        current_user=current_user,
    )
