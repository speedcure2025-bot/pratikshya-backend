"""
PRATIKSHYA FASHON — canonical account-level / business-role / capability model.

ONE module, imported by dependencies, services and the Admin API. This is the
consolidation layer for the authorization vocabulary — it is deliberately NOT
a second RBAC system: it maps the existing `roles` / `permissions` /
`user_roles` / `role_permissions` records onto a small capability model and
keeps every legacy granular permission code working through an explicit
bidirectional compatibility mapping.

    ACCOUNT LEVEL  — organizational authority (4 values, persisted on users)
    BUSINESS ROLE  — operational responsibility (existing PredefinedRole names)
    CAPABILITY     — what the user can actually do (small grouped set)

Separation rule (spec §5): capabilities decide WHO may invoke an operation;
they never decide WHETHER the operation is valid. Product lifecycle, media and
other business rules stay in their own services untouched.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

# ===========================================================================
# ACCOUNT LEVELS — exactly four staff levels (+ customers, who own none)
# ===========================================================================

ACCOUNT_LEVEL_SUPER_ADMIN = "SUPER_ADMIN"
ACCOUNT_LEVEL_ADMIN = "ADMIN"
ACCOUNT_LEVEL_SUPER_EMPLOYEE = "SUPER_EMPLOYEE"
ACCOUNT_LEVEL_EMPLOYEE = "EMPLOYEE"

ACCOUNT_LEVELS: tuple = (
    ACCOUNT_LEVEL_SUPER_ADMIN,
    ACCOUNT_LEVEL_ADMIN,
    ACCOUNT_LEVEL_SUPER_EMPLOYEE,
    ACCOUNT_LEVEL_EMPLOYEE,
)

# Lower rank == higher authority. Unknown/customer -> len(ACCOUNT_LEVELS).
ACCOUNT_LEVEL_RANK: Dict[str, int] = {level: i for i, level in enumerate(ACCOUNT_LEVELS)}

#: Levels that operate the Admin workspace vs the Employee workspace.
ADMIN_WORKSPACE_LEVELS = frozenset({ACCOUNT_LEVEL_SUPER_ADMIN, ACCOUNT_LEVEL_ADMIN})
EMPLOYEE_WORKSPACE_LEVELS = frozenset({ACCOUNT_LEVEL_SUPER_EMPLOYEE, ACCOUNT_LEVEL_EMPLOYEE})


def derive_account_level(user_type: Optional[str], roles: Optional[Iterable[str]] = None) -> Optional[str]:
    """
    Deterministic account level for rows that pre-date the ``users.account_level``
    column (and as the fallback whenever the column is NULL).

    • admin + SUPER_ADMIN role            → SUPER_ADMIN
    • admin                               → ADMIN
    • employee + SUPER_EMPLOYEE marker    → SUPER_EMPLOYEE
    • employee                            → EMPLOYEE
    • customer / anything else            → None (no staff level)
    """
    role_set = {str(r).upper() for r in (roles or [])}
    if user_type == "admin":
        return (
            ACCOUNT_LEVEL_SUPER_ADMIN
            if ACCOUNT_LEVEL_SUPER_ADMIN in role_set
            else ACCOUNT_LEVEL_ADMIN
        )
    if user_type == "employee":
        return (
            ACCOUNT_LEVEL_SUPER_EMPLOYEE
            if ACCOUNT_LEVEL_SUPER_EMPLOYEE in role_set
            else ACCOUNT_LEVEL_EMPLOYEE
        )
    return None


def is_staff_level(level: Optional[str]) -> bool:
    return level in ACCOUNT_LEVEL_RANK


def account_rank(level: Optional[str]) -> int:
    return ACCOUNT_LEVEL_RANK.get(level or "", len(ACCOUNT_LEVELS))


# Stored staff statuses. INACTIVE is a write/filter alias of DEACTIVATED.
STAFF_STATUS_ACTIVE = "ACTIVE"
STAFF_STATUS_SUSPENDED = "SUSPENDED"
STAFF_STATUS_DEACTIVATED = "DEACTIVATED"
STAFF_LOGIN_BLOCKED_STATUSES = frozenset({
    STAFF_STATUS_SUSPENDED,
    STAFF_STATUS_DEACTIVATED,
    "INACTIVE",
})


def is_staff_login_blocked(status: Optional[str]) -> bool:
    """True when a staff account must not receive a session."""
    return (status or "").upper() in STAFF_LOGIN_BLOCKED_STATUSES


def status_filter_values(status: Optional[str]) -> Optional[list]:
    """INACTIVE and DEACTIVATED are one deactivation state for list filters."""
    if not status:
        return None
    key = str(status).upper()
    if key in {"INACTIVE", STAFF_STATUS_DEACTIVATED}:
        return ["INACTIVE", STAFF_STATUS_DEACTIVATED]
    return [key]


# ===========================================================================
# ACCOUNT CREATION HIERARCHY — server-side matrix (spec §6)
# ===========================================================================

CREATABLE_LEVELS: Dict[str, Set[str]] = {
    ACCOUNT_LEVEL_SUPER_ADMIN: {ACCOUNT_LEVEL_SUPER_ADMIN, ACCOUNT_LEVEL_ADMIN, ACCOUNT_LEVEL_SUPER_EMPLOYEE, ACCOUNT_LEVEL_EMPLOYEE},
    ACCOUNT_LEVEL_ADMIN: {ACCOUNT_LEVEL_ADMIN, ACCOUNT_LEVEL_SUPER_EMPLOYEE, ACCOUNT_LEVEL_EMPLOYEE},
    ACCOUNT_LEVEL_SUPER_EMPLOYEE: {ACCOUNT_LEVEL_SUPER_EMPLOYEE, ACCOUNT_LEVEL_EMPLOYEE},
    ACCOUNT_LEVEL_EMPLOYEE: set(),
}

#: Which account level a user row of each ``user_type`` carries.
LEVEL_USER_TYPE: Dict[str, str] = {
    ACCOUNT_LEVEL_SUPER_ADMIN: "admin",
    ACCOUNT_LEVEL_ADMIN: "admin",
    ACCOUNT_LEVEL_SUPER_EMPLOYEE: "employee",
    ACCOUNT_LEVEL_EMPLOYEE: "employee",
}


def can_create(creator_level: Optional[str], target_level: str) -> bool:
    return target_level in CREATABLE_LEVELS.get(creator_level or "", set())


def can_manage(creator_level: Optional[str], target_level: Optional[str]) -> bool:
    """
    Manage = update/status/reset-password/permissions on an existing account.

    A creator may operate accounts they are allowed to create, plus their own
    profile (self-service never goes through this check). Nobody below
    SUPER_ADMIN may touch a SUPER_ADMIN account.
    """
    if not is_staff_level(creator_level) or not is_staff_level(target_level):
        return False
    if target_level == ACCOUNT_LEVEL_SUPER_ADMIN:
        return creator_level == ACCOUNT_LEVEL_SUPER_ADMIN
    return target_level in CREATABLE_LEVELS.get(creator_level, set())


# ===========================================================================
# BUSINESS ROLES — one authoritative catalogue (spec §12)
#
# Persisted names are PRESERVED (no destructive renames). The legacy Admin-side
# role keys (MANAGER/SALES/INVENTORY/WAREHOUSE/CS/STYLIST) are compatibility
# ALIASES onto the canonical business-role entries — one vocabulary, two door
# handles for pre-existing data and the pinned admin role definitions.
# ===========================================================================

from app.core.constants import PredefinedRole  # noqa: E402  (single source of role names)

BUSINESS_ROLE_NAMES: tuple = tuple(
    r.value for r in PredefinedRole if r is not PredefinedRole.SUPER_ADMIN
)

#: legacy admin-portal role key → canonical business role name
ROLE_NAME_ALIASES: Dict[str, str] = {
    "MANAGER": PredefinedRole.STORE_MANAGER.value,
    "SALES": PredefinedRole.SALES_EXECUTIVE.value,
    "INVENTORY": PredefinedRole.INVENTORY_MANAGER.value,
    "WAREHOUSE": PredefinedRole.WAREHOUSE_STAFF.value,
    "CS": PredefinedRole.CUSTOMER_SUPPORT.value,
    "STYLIST": PredefinedRole.FASHION_STYLIST.value,
}


def canonical_role_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    key = str(name).upper()
    return ROLE_NAME_ALIASES.get(key, key)


# ---------------------------------------------------------------------------
# ROLE CATALOG — the single BUILT_IN_ROLES definition.
#
# `app.api.v1.admin` re-exports this as `BUILT_IN_ROLES` (every existing
# import site — dependencies fallback, auth service, seed script — keeps
# working). The legacy Admin-portal business keys (MANAGER/SALES/INVENTORY/
# WAREHOUSE/CS/STYLIST) are ALIASES onto the canonical persisted business
# roles, so there is one vocabulary: STORE_MANAGER carries the consolidated
# operational set the old MANAGER entry defined, SALES_EXECUTIVE the old
# SALES set, and so on. Business-role permission payloads remain the legacy
# granular codes; the capability layer above maps them to groups.
# ---------------------------------------------------------------------------

_ROLE_DEFS: Dict[str, dict] = {
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
            "employees.view", "employees.create", "employees.edit", "employees.suspend", "employees.resetPassword", "employees.managePermissions", "employees.delete",
            "analytics.view", "offers.view", "offers.create", "offers.edit",
            "attendance.view", "leave.view", "leave.approve", "performance.view", "performance.review",
            "audit.view", "users.view", "users.manage", "roles.view", "roles.manage",
            "ai.view",
        ],
    },
    "STORE_MANAGER": {
        "id": "STORE_MANAGER",
        "name": "Store Manager",
        "description": "Operational store leadership across catalogue, orders, inventory and team.",
        "permissions": [
            "products.view", "products.manage", "categories.view", "collections.view",
            "orders.view", "orders.fulfill", "orders.pick", "orders.pack", "orders.dispatch", "orders.cancel",
            "returns.view", "returns.manage",
            "customers.view", "inventory.view", "inventory.receive", "inventory.adjust",
            "employees.view", "analytics.view", "offers.view",
            "audit.view", "users.view", "roles.view",
            "attendance.view", "leave.view", "performance.view",
        ],
    },
    "SALES_EXECUTIVE": {
        "id": "SALES_EXECUTIVE",
        "name": "Sales Executive",
        "description": "Sales-floor and customer-facing operations.",
        "permissions": ["products.view", "orders.view", "customers.view", "offers.view"],
    },
    "INVENTORY_MANAGER": {
        "id": "INVENTORY_MANAGER",
        "name": "Inventory Manager",
        "description": "Inventory management and stock operations.",
        "permissions": ["inventory.view", "inventory.manage", "inventory.receive", "inventory.adjust", "inventory.transfer", "products.view"],
    },
    "INVENTORY_STAFF": {
        "id": "INVENTORY_STAFF",
        "name": "Inventory Staff",
        "description": "Receive, adjust and transfer on the floor.",
        "permissions": ["inventory.view", "inventory.receive", "inventory.adjust", "inventory.transfer", "products.view"],
    },
    "WAREHOUSE_STAFF": {
        "id": "WAREHOUSE_STAFF",
        "name": "Warehouse Staff",
        "description": "Warehouse operations including pick, pack and dispatch.",
        "permissions": ["orders.view", "orders.fulfill", "orders.pick", "orders.pack", "orders.dispatch", "inventory.view"],
    },
    "CUSTOMER_SUPPORT": {
        "id": "CUSTOMER_SUPPORT",
        "name": "Customer Support",
        "description": "Customer support — orders, returns and customer queries.",
        "permissions": ["orders.view", "orders.manage", "returns.view", "returns.manage", "customers.view"],
    },
    "FASHION_STYLIST": {
        "id": "FASHION_STYLIST",
        "name": "Fashion Stylist",
        "description": "Product content and catalogue editing.",
        "permissions": ["products.view", "products.manage", "media.view", "media.upload"],
    },
}

#: Default capability grants per business role (canonical codes; used by the
#: capability UI and by seed-time capability rows — NOT a second permission
#: store, these expand to the granular codes above).
BUSINESS_ROLE_CAPABILITIES: Dict[str, List[str]] = {
    PredefinedRole.STORE_MANAGER.value: ["catalogue.view", "orders.view", "orders.manage", "returns.view", "returns.manage", "customers.view", "inventory.view", "people.view", "analytics.view", "offers.view"],
    PredefinedRole.SALES_EXECUTIVE.value: ["catalogue.view", "orders.view", "customers.view", "offers.view"],
    PredefinedRole.INVENTORY_MANAGER.value: ["catalogue.view", "inventory.view", "inventory.manage", "orders.view", "orders.manage"],
    PredefinedRole.INVENTORY_STAFF.value: ["catalogue.view", "inventory.view", "inventory.manage"],
    PredefinedRole.WAREHOUSE_STAFF.value: ["inventory.view", "orders.view", "orders.manage"],
    PredefinedRole.CUSTOMER_SUPPORT.value: ["orders.view", "orders.manage", "returns.view", "returns.manage", "customers.view"],
    PredefinedRole.FASHION_STYLIST.value: ["catalogue.view", "catalogue.manage", "media.view", "media.manage"],
}


def build_role_catalog() -> Dict[str, dict]:
    """The consolidated catalog + legacy alias keys (same dict objects)."""
    catalog: Dict[str, dict] = dict(_ROLE_DEFS)
    for legacy_key, canonical in ROLE_NAME_ALIASES.items():
        if canonical in catalog:
            catalog[legacy_key] = catalog[canonical]
    return catalog


BUILT_IN_ROLES: Dict[str, dict] = build_role_catalog()


def resolve_stored_grants(
    *,
    account_level: Optional[str],
    roles: Optional[Iterable[str]],
    permission_mode: Optional[str],
    custom_permissions: Optional[Iterable[str]],
    role_permission_codes: Optional[Iterable[str]] = None,
) -> Set[str]:
    """
    The grant list that authorization expands.

    ``permission_mode == "custom"`` REPLACES role defaults with the stored
    ``custom_permissions`` list (empty list = no extra grants). Role mode
    keeps role-row codes plus the BUILT_IN_ROLES fallback. SUPER_ADMIN
    always keeps the top-level ``*`` override — custom mode cannot shrink it.
    """
    role_list = [str(role) for role in (roles or []) if role]
    is_super_admin = (
        (account_level or "").upper() == ACCOUNT_LEVEL_SUPER_ADMIN
        or ACCOUNT_LEVEL_SUPER_ADMIN in {role.upper() for role in role_list}
    )
    if (permission_mode or "").lower() == "custom":
        grants = {str(code) for code in (custom_permissions or []) if code}
    else:
        grants = {str(code) for code in (role_permission_codes or []) if code}
        for role in role_list:
            entry = BUILT_IN_ROLES.get(canonical_role_name(role) or "")
            if entry:
                grants.update(entry.get("permissions") or [])
    if is_super_admin:
        grants.add("*")
    return grants


# ===========================================================================
# CAPABILITY GROUPS — the small canonical model (spec §4/§19)
#
# Each capability is the assignment unit; `implies` lists the legacy granular
# permission codes it covers, so handlers keep their precise per-operation
# checks while callers, UI and JWT-adjacent DTOs speak capabilities.
# ===========================================================================

CAPABILITY_GROUPS: List[dict] = [
    {
        "id": "CATALOGUE",
        "label": "Catalogue",
        "actions": [
            {"code": "catalogue.view", "label": "View",
             "implies": ["products.view", "categories.view", "collections.view"]},
            {"code": "catalogue.manage", "label": "Manage",
             "implies": ["products.manage", "categories.create", "categories.edit",
                         "categories.archive", "collections.create", "collections.edit",
                         "collections.assign", "collections.archive"]},
        ],
    },
    {
        "id": "PRODUCT_WORKFLOW",
        "label": "Product Workflow",
        "actions": [
            # Workflow VALIDATION lives in the product service — these only
            # decide who may reach the review/manage desks.
            {"code": "product_workflow.review", "label": "Review",
             "implies": ["products.view"]},
            {"code": "product_workflow.manage", "label": "Manage",
             "implies": ["products.manage"]},
        ],
    },
    {
        "id": "MEDIA",
        "label": "Media",
        "actions": [
            {"code": "media.view", "label": "View", "implies": ["media.view"]},
            {"code": "media.manage", "label": "Manage",
             "implies": ["media.upload", "media.assign", "media.edit", "media.manage"]},
            {"code": "media.delete", "label": "Delete", "implies": ["media.delete"]},
        ],
    },
    {
        "id": "ORDERS",
        "label": "Orders",
        "actions": [
            {"code": "orders.view", "label": "View", "implies": ["orders.view"]},
            {"code": "orders.manage", "label": "Manage",
             "implies": ["orders.manage", "orders.create", "orders.fulfill", "orders.pick",
                         "orders.pack", "orders.dispatch", "orders.cancel", "orders.return",
                         "orders.refund"]},
        ],
    },
    {
        "id": "RETURNS",
        "label": "Returns",
        "actions": [
            {"code": "returns.view", "label": "View", "implies": ["returns.view"]},
            {"code": "returns.manage", "label": "Manage", "implies": ["returns.manage"]},
        ],
    },
    {
        "id": "CUSTOMERS",
        "label": "Customers",
        "actions": [
            {"code": "customers.view", "label": "View", "implies": ["customers.view"]},
            {"code": "customers.manage", "label": "Manage", "implies": ["customers.manage"]},
        ],
    },
    {
        "id": "INVENTORY",
        "label": "Inventory",
        "actions": [
            {"code": "inventory.view", "label": "View",
             "implies": ["inventory.view", "warehouse.view"]},
            {"code": "inventory.manage", "label": "Manage",
             "implies": ["inventory.manage", "inventory.receive", "inventory.adjust",
                         "inventory.transfer", "inventory.audit", "warehouse.pick"]},
        ],
    },
    {
        "id": "PEOPLE",
        "label": "People",
        "actions": [
            # Deliberately NOT implied by attendance/leave/performance keys —
            # every business role holds those for its own records, and a
            # grant must not roll up into People-directory authority.
            {"code": "people.view", "label": "View",
             "implies": ["employees.view", "users.view", "roles.view"]},
            {"code": "people.manage", "label": "Manage",
             "implies": ["employees.create", "employees.edit", "employees.suspend",
                         "employees.delete", "employees.resetPassword", "employees.manage"]},
            {"code": "people.security", "label": "Security",
             "implies": ["employees.managePermissions", "users.manage", "roles.manage"]},
        ],
    },
    {
        "id": "MARKETING",
        "label": "Marketing",
        "actions": [
            {"code": "marketing.view", "label": "View", "implies": []},
            {"code": "marketing.manage", "label": "Manage", "implies": []},
        ],
    },
    {
        "id": "ANALYTICS",
        "label": "Analytics",
        "actions": [
            {"code": "analytics.view", "label": "View",
             "implies": ["analytics.view", "analytics.sales", "analytics.products",
                         "analytics.customers", "analytics.inventory", "analytics.returns",
                         "analytics.offers", "analytics.employees", "audit.view"]},
        ],
    },
    {
        "id": "OFFERS",
        "label": "Offers",
        "actions": [
            {"code": "offers.view", "label": "View", "implies": ["offers.view"]},
            {"code": "offers.manage", "label": "Manage",
             "implies": ["offers.create", "offers.edit", "offers.activate",
                         "offers.pause", "offers.archive", "offers.manage"]},
        ],
    },
    {
        "id": "SETTINGS",
        "label": "Settings",
        "actions": [
            {"code": "settings.view", "label": "View", "implies": ["settings.view"]},
            {"code": "settings.manage", "label": "Manage", "implies": ["settings.manage"]},
        ],
    },
    {
        "id": "AI_ASSISTANT",
        "label": "AI Assistant",
        "actions": [
            # The AI desk keeps its existing handler-level check (analytics.view,
            # real read-only business tools) — ai.view is the canonical
            # assignment unit and implies the underlying code, so granting the
            # capability satisfies the handler without duplicating the check.
            {"code": "ai.view", "label": "Use", "implies": ["analytics.view"]},
        ],
    },
]

ALL_CAPABILITIES: tuple = tuple(
    action["code"] for group in CAPABILITY_GROUPS for action in group["actions"]
)

CAPABILITY_CODES: frozenset = frozenset(ALL_CAPABILITIES)

#: capability code → legacy granular codes it implies
CAPABILITY_IMPLIES: Dict[str, List[str]] = {
    action["code"]: list(action["implies"])
    for group in CAPABILITY_GROUPS
    for action in group["actions"]
}

#: legacy granular code → canonical capability it rolls up into
LEGACY_TO_CAPABILITY: Dict[str, str] = {}
for _cap, _implies in CAPABILITY_IMPLIES.items():
    for _legacy in _implies:
        # First-writer wins only when a legacy code is unclaimed; canonical
        # codes map to themselves.
        if _legacy != _cap and _legacy not in LEGACY_TO_CAPABILITY:
            LEGACY_TO_CAPABILITY[_legacy] = _cap
LEGACY_TO_CAPABILITY.update({cap: cap for cap in ALL_CAPABILITIES})

#: Capabilities that must never be delegated to / by the employee domain.
#: Granting these is admin-domain authority (§7, §10).
ADMIN_ONLY_CAPABILITIES = frozenset({"settings.manage", "people.security"})

#: Everything an employee-domain creator (SUPER_EMPLOYEE) may hand out.
EMPLOYEE_DOMAIN_CAPABILITIES = frozenset(ALL_CAPABILITIES) - ADMIN_ONLY_CAPABILITIES


def expand_effective_permissions(granted: Iterable[str]) -> Set[str]:
    """
    Canonical authorization set for a user.

    Result = granted codes ∪ capabilities implied by granted legacy codes ∪
    legacy codes implied by granted capabilities. With ``*`` present the set is
    unbounded (SUPER_ADMIN top-level override — checks short-circuit).
    Both directions exist so an OLD grant (e.g. ``products.view`` from a
    seeded ADMIN role row) satisfies a NEW capability check
    (``catalogue.view``) and a NEW capability assignment (``orders.manage``)
    still satisfies the granular handler checks the product/orders routers
    keep — the compatibility mapping of spec §20, without rewriting callers.
    """
    effective: Set[str] = {str(code) for code in granted if code}
    if "*" in effective:
        return effective
    for code in list(effective):
        cap = LEGACY_TO_CAPABILITY.get(code)
        if cap:
            effective.add(cap)
    for code in list(effective):
        if code in CAPABILITY_IMPLIES:
            effective.update(CAPABILITY_IMPLIES[code])
    return effective


def normalize_grants(codes: Iterable[str]) -> List[str]:
    """Sanitize an incoming assignment list: known legacy codes and canonical
    capabilities are kept (deduped, stable order); unknown codes are dropped."""
    kept = []
    seen: Set[str] = set()
    for code in codes or []:
        code = str(code).strip()
        if not code or code in seen:
            continue
        if code in CAPABILITY_CODES or code in LEGACY_TO_CAPABILITY or code in _KNOWN_OPERATIONAL:
            seen.add(code)
            kept.append(code)
    return sorted(kept)


def check_delegation(
    creator_level: Optional[str],
    target_level: str,
    requested: List[str],
    creator_capabilities: Set[str],
) -> None:
    """
    Enforce the account hierarchy + least-privilege delegation ceiling (§6, §7, §28).

    Raises (ForbiddenException=403 / BusinessLogicException=422):
      • creator level may not create the target level at all
      • requested capabilities exceed what the creator can delegate
      • the target is employee-domain and the request includes
        admin-domain authority (settings.manage / people.security)
    SUPER_ADMIN is unrestricted for Admin-workspace targets, but those two
    codes still cannot be granted to Super Employee or Employee.
    """
    from app.core.exceptions import BusinessLogicException, ForbiddenException

    if target_level not in ACCOUNT_LEVELS:
        raise BusinessLogicException(
            f"Unknown account level '{target_level}'. Expected one of: {', '.join(ACCOUNT_LEVELS)}."
        )
    if not can_create(creator_level, target_level):
        raise ForbiddenException(
            f"Accounts at level {target_level} cannot be created by {creator_level or 'this'} account."
        )
    requested_effective = set(expand_effective_permissions(requested))
    if target_level in {ACCOUNT_LEVEL_SUPER_EMPLOYEE, ACCOUNT_LEVEL_EMPLOYEE}:
        forbidden = sorted(
            {
                cap
                for code in requested_effective
                for cap in {code, LEGACY_TO_CAPABILITY.get(code)}
                if cap in ADMIN_ONLY_CAPABILITIES
            }
        )
        if forbidden:
            raise ForbiddenException(
                "Employee-domain accounts cannot be granted admin-domain authority: "
                + ", ".join(forbidden)
            )
    if creator_level == ACCOUNT_LEVEL_SUPER_ADMIN:
        return  # system-owner: unrestricted for Admin-workspace targets
    creator_effective = set(expand_effective_permissions(creator_capabilities))
    if "*" in creator_effective:
        return  # wildcard holder behaves as top-level override
    over = sorted(requested_effective - creator_effective)
    if over:
        raise ForbiddenException(
            "You cannot grant capabilities you are not authorized to delegate: "
            + ", ".join(over)
        )


#: Operational granular codes that remain valid grants without belonging to a
#: capability group (employee workspace self-service keys — deliberately not
#: groupable: every employee holds them for their OWN records).
_KNOWN_OPERATIONAL: frozenset = frozenset({
    "dashboard.view", "profile.view", "profile.edit",
    "attendance.view", "attendance.checkIn", "attendance.checkOut", "attendance.manage", "attendance.correct",
    "leave.view", "leave.create", "leave.approve", "leave.reject", "leave.manage",
    "performance.view", "performance.manage", "performance.review",
    "team.view", "support.view", "support.manage", "styling.view", "styling.manage",
    "audit.view", "users.view", "users.manage", "roles.view", "roles.manage",
    "settings.view", "settings.manage",
})

#: Subset actually injected onto every employee-domain session (EMPLOYEE and
#: SUPER_EMPLOYEE). The capability UI has no Dashboard/Profile row, so without
#: this union a newly created account authenticates then cannot open /employee.
#: House-wide keys (attendance.manage, leave.approve, people.*, settings.*)
#: stay assignment-only and are NOT in this set.
EMPLOYEE_SELF_SERVICE_PERMISSIONS: frozenset = frozenset({
    "dashboard.view", "profile.view", "profile.edit",
    "attendance.view", "attendance.checkIn", "attendance.checkOut",
    "leave.view", "leave.create",
    "performance.view",
})


def with_employee_self_service(user_type: Optional[str], permissions: Set[str]) -> Set[str]:
    """Union own-record keys onto employee-domain sessions only.

    Admin-workspace accounts (SUPER_ADMIN / ADMIN) are unchanged — they do
    not use the employee portal home, and injecting dashboard.view there
    would not match how Admin authorization is evaluated.
    """
    if user_type != "employee":
        return permissions
    out = set(permissions)
    out.update(EMPLOYEE_SELF_SERVICE_PERMISSIONS)
    return out
