"""
Admin — Settings (read-only), Activity log, Roles, Capabilities, Dashboard.

URL mapping (API_CONTRACT.md § ADMIN → implementation):

  Settings (read — both Admin and Super Admin)
  ─────────────────────────────────────────────────────────────────────────────
  GET    /admin/settings                  ← all sections (deep-merged defaults)
  GET    /admin/settings/{section}        ← single section

  Write / reset operations are Super-Admin-only and live at:
    PATCH  /super-admin/settings/{section}
    POST   /super-admin/settings/{section}/reset
    POST   /super-admin/settings/reset
  See app/api/v1/super_admin.py.

  Activity log
  ─────────────────────────────────────────────────────────────────────────────
  GET    /admin/activity                  ← shared audit diary (latest 200)

  Roles
  ─────────────────────────────────────────────────────────────────────────────
  GET    /admin/roles                     ← list 8 built-in roles
  GET    /admin/roles/{roleId}            ← single role with default permissions

  Capabilities
  ─────────────────────────────────────────────────────────────────────────────
  GET    /admin/capabilities              ← account hierarchy + capability groups

  Dashboard
  ─────────────────────────────────────────────────────────────────────────────
  GET    /admin/dashboard/summary         ← consolidated dashboard aggregates

Notes:
  - Settings are deep-merged against SETTINGS_DEFAULTS on every read.
  - Unknown section names return { error: "Unknown settings section" } per spec.
  - Roles are static in this release; the canonical permission vocabulary is
    fixed at startup (roles-permissions.json equivalent).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.core.logging import get_logger
from app.core.rbac import ACCOUNT_LEVEL_SUPER_ADMIN
from app.dependencies import (
    get_current_admin,
    get_db,
    require_admin_permission,
    require_permission_for_user,
    resolve_account_level,
)
from app.models.admin.setting import SettingModel
from app.models.auth.user import UserModel
from app.models.employee.employee import EmployeeProfileModel

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin Business Config"])


# Settings catalogue: ONE source for sections + defaults (shared with the
# workforce services — see app/core/settings_catalog.py).
from app.core.settings_catalog import KNOWN_SECTIONS, SETTINGS_DEFAULTS, merge_defaults  # noqa: E402


def _merge_defaults(section: str, stored: dict) -> dict:
    return merge_defaults(section, stored)


# ---------------------------------------------------------------------------
# STATIC ROLES (mirrors roles-permissions.json)
#
# Re-exported here so every existing
# `from app.api.v1.admin import BUILT_IN_ROLES` import site keeps working.
# ---------------------------------------------------------------------------

from app.core.rbac import BUILT_IN_ROLES  # noqa: E402  (re-export — compat seam)


# ===========================================================================
# SETTINGS — Read (Admin + Super Admin)
# ===========================================================================

@router.get(
    "/settings",
    summary="Get all settings sections (merged with defaults)",
    description=(
        "Returns all sections deep-merged against `SETTINGS_DEFAULTS`.  \n"
        "Authorization: Admin (both ADMIN and SUPER_ADMIN levels)."
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
    description=(
        "Authorization: Admin (both ADMIN and SUPER_ADMIN levels)."
    ),
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


# ===========================================================================
# ACTIVITY LOG
# ===========================================================================

@router.get(
    "/activity",
    summary="Admin — shared activity diary (latest 200 entries)",
    description=(
        "Returns the most recent 200 audit log entries across all domains.  \n"
        "ADMIN level sees all entries except those authored by SUPER_ADMIN accounts.  \n"
        "SUPER_ADMIN level sees the full unfiltered diary.  \n"
        "Authorization: Admin."
    ),
)
async def get_activity_log(
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "audit.view")
    from app.models.audit.activity_log import ActivityLogModel
    from sqlalchemy import select as sa_select

    try:
        stmt = sa_select(ActivityLogModel)
        actor_level = resolve_account_level(current_user)
        if actor_level != ACCOUNT_LEVEL_SUPER_ADMIN:
            super_admin_codes_subq = (
                sa_select(EmployeeProfileModel.employee_code)
                .join(UserModel, UserModel.id == EmployeeProfileModel.user_id)
                .where(UserModel.account_level == ACCOUNT_LEVEL_SUPER_ADMIN)
                .scalar_subquery()
            )
            from sqlalchemy import or_
            stmt = stmt.where(
                or_(
                    ActivityLogModel.actor_employee_id.is_(None),
                    ActivityLogModel.actor_employee_id.not_in(super_admin_codes_subq),
                )
            )
        stmt = stmt.order_by(ActivityLogModel.created_at.desc()).limit(200)
        result = await db.execute(stmt)
        logs = result.scalars().all()

        return {
            "ok": True,
            "activity": [
                {
                    "id":                 log.id,
                    "at":                 log.created_at.isoformat(),
                    "actorEmployeeId":    getattr(log, "actor_employee_id", None),
                    "actorName":          getattr(log, "actor_name", None),
                    "targetProductId":    getattr(log, "target_product_id", None),
                    "targetOfferId":      getattr(log, "target_offer_id", None),
                    "targetCategoryId":   getattr(log, "target_category_id", None),
                    "targetCollectionId": getattr(log, "target_collection_id", None),
                    "targetOrderId":      getattr(log, "target_order_id", None),
                    "action":             getattr(log, "action", None),
                    "summary":            getattr(log, "summary", None),
                }
                for log in logs
            ],
        }
    except Exception:
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
        "canonical business-role entries and are not listed twice.\n\n"
        "SUPER_ADMIN and ADMIN account-level pseudo-roles are only included "
        "when the requesting user is a SUPER_ADMIN."
    ),
)
async def list_roles(
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "roles.view")
    caller_level = resolve_account_level(current_user)
    # Account-level pseudo-roles (SUPER_ADMIN / ADMIN) are not business roles
    # that can be assigned during employee creation. Only the system owner
    # (SUPER_ADMIN) should ever see them — and only for reference, since the
    # creation endpoint enforces the hierarchy server-side as well.
    account_level_role_ids = {"SUPER_ADMIN", "ADMIN"}
    seen = set()
    roles = []
    for role in BUILT_IN_ROLES.values():
        if role["id"] in seen:
            continue
        seen.add(role["id"])
        # Hide account-level pseudo-roles from non-super-admin callers so the
        # frontend "add employee" form never presents them as selectable options.
        if role["id"] in account_level_role_ids and caller_level != ACCOUNT_LEVEL_SUPER_ADMIN:
            continue
        roles.append(role)
    return {"ok": True, "roles": roles}


@router.get(
    "/roles/{role_id}",
    summary="Get a single role",
)
async def get_role(
    role_id: str,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "roles.view")
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
        "defaults). Read-only; any authenticated admin surface account.\n\n"
        "`creatableLevels` is scoped to the requesting user's own account level "
        "so the frontend receives only the levels they are authorized to create."
    ),
)
async def list_capabilities(
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "roles.view")
    from app.core.rbac import ACCOUNT_LEVELS, ALL_CAPABILITIES, CAPABILITY_GROUPS, CREATABLE_LEVELS
    caller_level = resolve_account_level(current_user)
    # Return only the levels this caller is allowed to create, not the full
    # matrix. This prevents the frontend from displaying account levels the
    # current user has no authority to assign (e.g. ADMIN showing ADMIN option).
    caller_creatable = sorted(CREATABLE_LEVELS.get(caller_level or "", set()))
    return {
        "ok": True,
        "accountLevels": list(ACCOUNT_LEVELS),
        "creatableLevels": {caller_level: caller_creatable} if caller_level else {},
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
        recent_limit=recent_limit,
        db=db,
        current_user=current_user,
    )
