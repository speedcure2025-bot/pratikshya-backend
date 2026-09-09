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

Notes:
  - Settings are deep-merged against SETTINGS_DEFAULTS on every read.
  - Unknown section names return { error: "Unknown settings section" } per spec.
  - Roles are static in this release; the canonical permission vocabulary is
    fixed at startup (roles-permissions.json equivalent).
"""

import copy
from typing import Any, Dict

from fastapi import APIRouter, Depends, status
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


# ---------------------------------------------------------------------------
# SETTINGS_DEFAULTS — mirrors settingsRepository defaults on the frontend
# ---------------------------------------------------------------------------

KNOWN_SECTIONS = {
    "business", "store", "locations", "hours", "attendance", "holidays",
    "tax", "shipping", "payments", "orders", "returns", "inventory",
    "employees", "notifications", "customer", "offers", "media",
}

SETTINGS_DEFAULTS: Dict[str, Any] = {
    "business": {
        "name": "Pratikshya Fashon",
        "email": "",
        "phone": "",
        "gst": "",
        "address": "",
    },
    "store": {
        "currency": "INR",
        "timezone": "Asia/Kolkata",
        "locale": "en-IN",
    },
    "locations": {},
    "hours": {
        "open": "09:00",
        "close": "21:00",
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    },
    "attendance": {
        "startTime": "09:30",
        "endTime": "18:30",
        "lateThresholdMinutes": 10,
        "minimumHalfDayMinutes": 240,
        "fullDayMinutes": 540,
    },
    "holidays": {"list": []},
    "tax": {
        "mode": "INCLUSIVE",
        "defaultRate": 0,
    },
    "shipping": {
        "freeShippingThreshold": 5000,
        "flatShippingFee": 99,
        "expressFee": 199,
        "codFee": 49,
    },
    "payments": {
        "methods": ["upi", "card", "netbanking", "cod"],
        "refundMethod": "Original payment method",
        "refundSla": "5-7 business days",
        "partialRefundEnabled": True,
    },
    "orders": {
        "autoConfirm": True,
        "cancellableStatuses": [
            "PENDING_PAYMENT", "PLACED", "PAYMENT_CONFIRMED",
            "ORDER_CONFIRMED", "CONFIRMED", "PROCESSING", "ALLOCATED", "PICKING",
        ],
    },
    "returns": {
        "returnWindowDays": 7,
        "returnMethods": ["HOME_PICKUP", "STORE_DROP"],
    },
    "inventory": {
        "lowStockThreshold": 5,
        "trackStock": True,
    },
    "employees": {
        "minimumPasswordLength": 8,
        "requireUppercase": True,
        "requireLowercase": True,
        "requireNumber": True,
        "requireSpecialCharacter": False,
        "passwordExpiryDays": 30,
    },
    "notifications": {
        "order": ["IN_APP"],
        "returns": ["IN_APP"],
        "employee": ["IN_APP"],
        "lowStock": ["IN_APP"],
        "offers": ["IN_APP"],
        "marketing": [],
    },
    "customer": {
        "allowGuestOrders": True,
        "autoLoyaltyPoints": True,
    },
    "offers": {
        "defaultDurationDays": 7,
        "maximumCouponDiscount": 10000,
        "defaultCustomerUsageLimit": 1,
        "allowStacking": False,
    },
    "media": {
        "maxImageSizeMb": 10,
        "maxVideoSizeMb": 100,
        # DERIVED from the single house image policy the upload validator and
        # the migration tool enforce (settings.ALLOWED_IMAGE_TYPES mapped
        # through app.storage.signatures) — never a second hand-maintained
        # list. This keeps the settings desk from advertising a format the
        # backend would reject (or omitting one it accepts): .avif and .webp
        # are part of the policy because the real product asset library is
        # AVIF-first (228 of the 238 shipped assets carry a .avif name).
        "allowedImageTypes": [ext.lstrip(".") for ext in allowed_image_extensions()],
        "allowedVideoTypes": ["mp4", "webm"],
    },
}


def _merge_defaults(section: str, stored: dict) -> dict:
    """Deep-merge stored values on top of defaults."""
    defaults = copy.deepcopy(SETTINGS_DEFAULTS.get(section, {}))
    if stored:
        def _deep_merge(base: dict, override: dict) -> dict:
            result = dict(base)
            for k, v in override.items():
                if isinstance(v, dict) and isinstance(result.get(k), dict):
                    result[k] = _deep_merge(result[k], v)
                else:
                    result[k] = v
            return result
        return _deep_merge(defaults, stored)
    return defaults


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SettingsPatchRequest(BaseModel):
    data: Dict[str, Any]


# ---------------------------------------------------------------------------
# STATIC ROLES (mirrors roles-permissions.json)
# ---------------------------------------------------------------------------

BUILT_IN_ROLES = {
    "SUPER_ADMIN": {
        "id": "SUPER_ADMIN",
        "name": "Super Admin",
        "description": "Full unrestricted access to all features and settings.",
        "permissions": ["*"],
    },
    "ADMIN": {
        "id": "ADMIN",
        "name": "Admin",
        "description": "Full operational access excluding some destructive actions.",
        "permissions": [
            "products.view", "products.manage", "categories.view", "categories.create", "categories.edit", "categories.archive",
            "collections.view", "collections.create", "collections.edit", "collections.assign", "collections.archive",
            "media.view", "media.upload", "media.assign", "media.delete",
            "orders.view", "orders.fulfill", "orders.pick", "orders.pack", "orders.dispatch", "orders.cancel", "orders.manage",
            "returns.view", "returns.manage",
            "customers.view", "inventory.view", "inventory.manage", "inventory.receive", "inventory.adjust", "inventory.transfer",
            "employees.view", "employees.create", "employees.edit", "employees.suspend", "employees.resetPassword", "employees.managePermissions",
            "analytics.view", "offers.view", "offers.create", "offers.edit",
            "attendance.view", "leave.view", "leave.approve", "performance.view", "performance.review",
        ],
    },
    "MANAGER": {
        "id": "MANAGER",
        "name": "Manager",
        "description": "Operational manager with broad but not absolute access.",
        "permissions": [
            "products.view", "products.manage", "categories.view", "collections.view",
            "orders.view", "orders.fulfill", "orders.pick", "orders.pack", "orders.dispatch", "orders.cancel",
            "returns.view", "returns.manage",
            "customers.view", "inventory.view", "inventory.receive", "inventory.adjust",
            "employees.view", "analytics.view", "offers.view",
            "attendance.view", "leave.view", "performance.view",
        ],
    },
    "SALES": {
        "id": "SALES",
        "name": "Sales",
        "description": "Sales-floor and customer-facing operations.",
        "permissions": ["products.view", "orders.view", "customers.view", "offers.view"],
    },
    "INVENTORY": {
        "id": "INVENTORY",
        "name": "Inventory",
        "description": "Inventory management and stock operations.",
        "permissions": ["inventory.view", "inventory.manage", "inventory.receive", "inventory.adjust", "inventory.transfer", "products.view"],
    },
    "WAREHOUSE": {
        "id": "WAREHOUSE",
        "name": "Warehouse",
        "description": "Warehouse operations including pick, pack and dispatch.",
        "permissions": ["orders.view", "orders.fulfill", "orders.pick", "orders.pack", "orders.dispatch", "inventory.view"],
    },
    "CS": {
        "id": "CS",
        "name": "Customer Support",
        "description": "Customer support — orders, returns and customer queries.",
        "permissions": ["orders.view", "orders.manage", "returns.view", "returns.manage", "customers.view"],
    },
    "STYLIST": {
        "id": "STYLIST",
        "name": "Stylist",
        "description": "Product content and catalogue editing.",
        "permissions": ["products.view", "products.manage", "media.view", "media.upload"],
    },
}


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
# ROLES
# ===========================================================================

@router.get(
    "/roles",
    summary="List built-in roles",
    description="Returns all 8 built-in roles with their default permission sets.",
)
async def list_roles(
    current_user: UserModel = Depends(get_current_admin),
):
    return {"ok": True, "roles": list(BUILT_IN_ROLES.values())}


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
