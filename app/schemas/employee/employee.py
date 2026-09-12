from datetime import date
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


# --------------------------------------------------------------------------- #
#  Employee create / update (admin-facing)                                      #
# --------------------------------------------------------------------------- #

class EmployeeCreateRequest(BaseModel):
    """
    Onboard a new employee.
    Spec body (API_CONTRACT.md §EMPLOYEE):
      { firstName, lastName, email, phone, role, department, section?,
        store, joiningDate, shift?, permissionMode?, permissions? }
    Also accepts legacy full_name for backward compat.
    """
    # Spec fields (camelCase)
    firstName: Optional[str] = Field(None, min_length=1, max_length=60)
    lastName: Optional[str] = Field(None, min_length=1, max_length=60)
    # backward-compat snake_case
    full_name: Optional[str] = Field(None, min_length=2, max_length=255)

    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=20)

    @field_validator("phone", "email", mode="before")
    @classmethod
    def _coerce_empty_contact(cls, value):
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # Role — required per spec; also stored on EmployeeProfile
    role: Optional[str] = Field(None, description="One of the 8 RBAC role names e.g. SALES_EXECUTIVE")

    # Department / section
    department: Optional[str] = Field(None, max_length=100)
    department_id: Optional[str] = None
    section: Optional[str] = Field(None, max_length=100)
    section_id: Optional[str] = None

    # Store / floor assignment
    # NOTE: accepted from the frontend form for UI completeness but NOT
    # persisted — EmployeeProfileModel has no `store` column. Will be
    # wired to the DB once the store-assignment feature is implemented.
    store: Optional[str] = Field(None, max_length=100, description="Store or floor assignment (UI-only; not persisted to DB)")

    # Joining / shift — intentionally optional: the employee profile table
    # carries no joining_date or shift column (see app/models/employee/employee.py);
    # these fields are UI-only and must not block SUPER_ADMIN/ADMIN account
    # creation where the employment block is hidden. Empty strings from the
    # UI are normalised to None so Optional[date] does not 422.
    joiningDate: Optional[date] = Field(None, description="ISO date YYYY-MM-DD — UI-only; not persisted to DB")
    shift: Optional[str] = Field(None, max_length=50, description="e.g. MORNING, EVENING — UI-only; not persisted to DB")

    @field_validator("joiningDate", mode="before")
    @classmethod
    def _coerce_empty_joining_date(cls, value):
        if value == "" or (isinstance(value, str) and not value.strip()):
            return None
        return value

    # Account level (unified four-level model). Omitted == EMPLOYEE.
    # The SERVER enforces the creation matrix and the delegation ceiling —
    # ADMIN/SUPER_ADMIN targets require an admin creator, SUPER_EMPLOYEE
    # targets require SUPER_ADMIN/ADMIN/SUPER_EMPLOYEE, and an EMPLOYEE
    # creator is always rejected.
    accountLevel: Optional[str] = Field(
        None, description="SUPER_ADMIN | ADMIN | SUPER_EMPLOYEE | EMPLOYEE (default EMPLOYEE)"
    )

    # Permission override
    permissionMode: Optional[str] = Field(
        None, description="role | custom — if custom, permissions[] is applied"
    )
    permissions: Optional[List[str]] = Field(
        None,
        description=(
            "Capability assignment at creation. Accepts canonical capability codes "
            "(catalogue.view, orders.manage, people.view …) or the legacy granular "
            "codes; the backend maps both onto the same authorization model."
        ),
    )

    # Designation (legacy / additional detail)
    designation: Optional[str] = Field(None, min_length=2, max_length=100)

    # Temp password — if not supplied the service will generate one
    password: Optional[str] = Field(None, min_length=6, description="Initial password; auto-generated if omitted")

    # Legacy employee_code override (auto-generated from role prefix if absent)
    employee_code: Optional[str] = Field(None, min_length=2, max_length=50)

    @model_validator(mode="after")
    def resolve_full_name(self) -> "EmployeeCreateRequest":
        if not self.full_name:
            first = (self.firstName or "").strip()
            last = (self.lastName or "").strip()
            combined = f"{first} {last}".strip()
            if not combined:
                raise ValueError("First name is required.")
            self.full_name = combined
        return self


class EmployeeUpdateRequest(BaseModel):
    """Partial update payload for an employee profile (all fields optional).

    NOTE: `email` is intentionally absent. Email is a login identifier and
    cannot be changed after account creation via this endpoint. To reassign
    an email, delete and recreate the account.
    """

    full_name: Optional[str] = Field(None, min_length=2, max_length=255)
    # also accept camelCase from spec
    firstName: Optional[str] = Field(None, min_length=1, max_length=60)
    lastName: Optional[str] = Field(None, min_length=1, max_length=60)

    phone: Optional[str] = Field(None, max_length=20)
    designation: Optional[str] = Field(None, min_length=2, max_length=100)

    @field_validator("phone", mode="before")
    @classmethod
    def _coerce_empty_phone_update(cls, value):
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value
    department: Optional[str] = Field(None, max_length=100)
    department_id: Optional[str] = None
    section: Optional[str] = Field(None, max_length=100)
    section_id: Optional[str] = None
    store: Optional[str] = Field(None, max_length=100)
    shift: Optional[str] = Field(None, max_length=50)
    role: Optional[str] = Field(None, description="Business role (canonical catalogue name or legacy alias)")
    accountLevel: Optional[str] = Field(
        None,
        description="SUPER_ADMIN | ADMIN | SUPER_EMPLOYEE | EMPLOYEE — SUPER_ADMIN creators only",
    )
    joiningDate: Optional[date] = None

    @field_validator("joiningDate", mode="before")
    @classmethod
    def _coerce_empty_joining_date_update(cls, value):
        if value == "" or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @model_validator(mode="after")
    def resolve_full_name(self) -> "EmployeeUpdateRequest":
        if not self.full_name and (self.firstName or self.lastName):
            first = (self.firstName or "").strip()
            last = (self.lastName or "").strip()
            self.full_name = f"{first} {last}".strip() or None
        return self


class EmployeeStatusRequest(BaseModel):
    """
    Change employee account status.
    Spec: POST /admin/employees/{id}/status
    Valid values per AUTHORIZATION_MATRIX.md: ACTIVE | PENDING | ON_LEAVE | SUSPENDED | INACTIVE
    """
    status: str = Field(
        ...,
        description="ACTIVE | PENDING | ON_LEAVE | SUSPENDED | INACTIVE",
    )


class ResetEmployeePasswordRequest(BaseModel):
    """Admin-initiated password reset for an employee."""

    new_password: Optional[str] = Field(None, min_length=6)
    force_change: bool = Field(
        default=True, description="Require employee to change password on next login (mustChangePassword)"
    )


class EmployeePermissionsRequest(BaseModel):
    """
    PUT /admin/employees/{id}/permissions
    Spec: { permissionMode: 'role'|'custom', permissions: string[] }
    """
    permissionMode: str = Field(..., description="role | custom")
    permissions: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Response DTOs                                                                #
# --------------------------------------------------------------------------- #

class EmployeeProfileDTO(BaseModel):
    """Employee profile fields embedded in responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_code: str
    designation: Optional[str]
    department: Optional[str]
    department_id: Optional[str]
    section_id: Optional[str]
    joining_date: Optional[date] = None
    # NOTE: store and shift are not persisted (no DB columns on EmployeeProfileModel).
    # They are managed as local UI state on the frontend. Excluded from the
    # response DTO to avoid implying the backend holds these values.


class EmployeeResponse(BaseModel):
    """
    Full employee response DTO combining user + profile data.
    Mirrors PublicEmployee — never exposes hashed_password.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str                   # user.id
    full_name: str
    email: Optional[str]
    phone: Optional[str]
    status: str
    is_verified: bool
    force_password_change: bool
    mustChangePassword: Optional[bool] = None   # spec alias
    created_at: datetime
    updated_at: datetime
    profile: Optional[EmployeeProfileDTO]
    roles: Optional[List[str]] = None
    permissions: Optional[List[str]] = None
    # ── Unified account model ─────────────────────────────────────────────
    # camelCase is the People-form contract. snake_case aliases are temporary
    # compat for older readers — do not add more pairs.
    account_level: Optional[str] = None
    accountLevel: Optional[str] = None
    business_role: Optional[str] = None
    businessRole: Optional[str] = None
    permission_mode: Optional[str] = None
    permissionMode: Optional[str] = None
    # Returned ONLY by the create call (one-time temporary credential);
    # never persisted, never present on list/get responses.
    temporaryPassword: Optional[str] = None
