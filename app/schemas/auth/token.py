from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict


class UserDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: Optional[str] = None
    phone: Optional[str] = None
    full_name: str
    user_type: str  # customer, employee, admin
    status: str  # ACTIVE, SUSPENDED, DEACTIVATED
    is_verified: bool
    force_password_change: bool
    roles: List[str] = []
    permissions: List[str] = []
    # Employee/admin profile aliases already present in existing models.
    employee_code: Optional[str] = None
    employeeCode: Optional[str] = None
    designation: Optional[str] = None
    department: Optional[str] = None
    department_id: Optional[str] = None
    section_id: Optional[str] = None
    admin_code: Optional[str] = None
    adminId: Optional[str] = None


class TokenResponse(BaseModel):
    """
    JWT token response.
    Wraps the spec's { ok: true, user/employee/admin } envelope while also
    carrying the standard bearer-token fields the backend uses internally.
    """
    ok: bool = True
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    # force_password_change mirrors mustChangePassword in spec for employee surface
    force_password_change: bool = False
    mustChangePassword: bool = False
    # The authenticated entity — field name differs per surface but same DTO
    user: Optional[UserDTO] = None
    # Spec aliases: employee surface returns `employee`, admin returns `admin`
    employee: Optional[UserDTO] = None
    admin: Optional[UserDTO] = None


class ForgotPasswordResponse(BaseModel):
    ok: bool = True
    message: str


class ResetPasswordResponse(BaseModel):
    ok: bool = True
    message: str = "Your password has been successfully updated."


class SignOutResponse(BaseModel):
    ok: bool = True
