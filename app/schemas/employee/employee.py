from datetime import date
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


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

    # Role — required per spec; also stored on EmployeeProfile
    role: Optional[str] = Field(None, description="One of the 8 RBAC role names e.g. SALES_EXECUTIVE")

    # Department / section
    department: Optional[str] = Field(None, max_length=100)
    department_id: Optional[str] = None
    section: Optional[str] = Field(None, max_length=100)
    section_id: Optional[str] = None

    # Store / floor assignment
    store: Optional[str] = Field(None, max_length=100, description="Store or floor assignment")

    # Joining / shift
    joiningDate: Optional[date] = Field(None, description="ISO date YYYY-MM-DD")
    shift: Optional[str] = Field(None, max_length=50, description="e.g. MORNING, EVENING")

    # Permission override
    permissionMode: Optional[str] = Field(
        None, description="role | custom — if custom, permissions[] is applied"
    )
    permissions: Optional[List[str]] = Field(
        None, description="Custom permission list (used when permissionMode=custom)"
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
    """Partial update payload for an employee profile (all fields optional)."""

    full_name: Optional[str] = Field(None, min_length=2, max_length=255)
    # also accept camelCase from spec
    firstName: Optional[str] = Field(None, min_length=1, max_length=60)
    lastName: Optional[str] = Field(None, min_length=1, max_length=60)

    phone: Optional[str] = Field(None, max_length=20)
    designation: Optional[str] = Field(None, min_length=2, max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    department_id: Optional[str] = None
    section: Optional[str] = Field(None, max_length=100)
    section_id: Optional[str] = None
    store: Optional[str] = Field(None, max_length=100)
    shift: Optional[str] = Field(None, max_length=50)
    role: Optional[str] = None
    joiningDate: Optional[date] = None

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
    store: Optional[str] = None
    shift: Optional[str] = None
    joining_date: Optional[date] = None


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
