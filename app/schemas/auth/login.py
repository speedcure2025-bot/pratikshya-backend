from typing import Optional
from pydantic import BaseModel, EmailStr, Field, model_validator


class CustomerRegisterRequest(BaseModel):
    """
    Customer registration.
    Accepts both camelCase (frontend spec: firstName/lastName) and
    snake_case (full_name) so either convention works.
    """
    # camelCase fields from API_CONTRACT.md spec
    firstName: Optional[str] = Field(None, min_length=1, max_length=60)
    lastName: Optional[str] = Field(None, min_length=1, max_length=60)
    dateOfBirth: Optional[str] = Field(None, description="ISO date string YYYY-MM-DD")

    # snake_case alternative (kept for backward compatibility)
    full_name: Optional[str] = Field(None, min_length=2, max_length=100)

    email: EmailStr
    # Accept 10-digit Indian mobile or E.164
    phone: Optional[str] = Field(
        None,
        pattern=r"^(?:\+91|0)?[6-9]\d{9}$|^\+?[1-9]\d{1,14}$",
        description="10-digit Indian mobile or E.164 format",
    )
    password: str = Field(..., min_length=6, max_length=128)
    remember: Optional[bool] = True

    @model_validator(mode="after")
    def resolve_full_name(self) -> "CustomerRegisterRequest":
        if not self.full_name:
            first = (self.firstName or "").strip()
            last = (self.lastName or "").strip()
            combined = f"{first} {last}".strip()
            if not combined:
                raise ValueError("First name is required.")
            self.full_name = combined
        return self


class CustomerLoginRequest(BaseModel):
    """
    Customer sign-in.
    Spec body: { identifier: string (email OR 10-digit phone), password, remember? }
    Also accepts plain `email` field for backward compat.
    """
    # spec field
    identifier: Optional[str] = Field(None, description="Email address or 10-digit phone number")
    # backward-compat field
    email: Optional[EmailStr] = None
    password: str = Field(..., min_length=1)
    remember: Optional[bool] = True

    @model_validator(mode="after")
    def resolve_identifier(self) -> "CustomerLoginRequest":
        if not self.identifier and not self.email:
            raise ValueError("Please enter your email address or phone number.")
        if not self.identifier:
            self.identifier = self.email
        return self


class EmployeeLoginRequest(BaseModel):
    """
    Employee sign-in.
    Spec body: { employeeId: "PF-<PREFIX>-#####", password }
    Also accepts employee_code for backward compat.
    """
    # spec field
    employeeId: Optional[str] = Field(None, description="Employee ID in format PF-<PREFIX>-#####")
    # backward-compat field
    employee_code: Optional[str] = Field(None, description="Employee ID or Email (legacy)")
    password: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def resolve_employee_id(self) -> "EmployeeLoginRequest":
        if not self.employeeId and not self.employee_code:
            raise ValueError("Enter your employee ID and password.")
        if not self.employeeId:
            self.employeeId = self.employee_code
        # keep employee_code in sync for service layer
        if not self.employee_code:
            self.employee_code = self.employeeId
        return self


class AdminLoginRequest(BaseModel):
    """
    Admin sign-in.
    Spec body: { adminId, password }
    Also accepts plain email for backward compat.
    """
    # spec field
    adminId: Optional[str] = Field(None, description="Admin ID or email")
    # backward-compat field
    email: Optional[EmailStr] = None
    password: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def resolve_admin_id(self) -> "AdminLoginRequest":
        if not self.adminId and not self.email:
            raise ValueError("Admin ID or email is required.")
        if not self.adminId:
            self.adminId = self.email
        if not self.email:
            # try to treat adminId as email for DB lookup
            self.email = self.adminId  # type: ignore[assignment]
        return self


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    """Change password — used by both customers and employees."""
    old_password: str = Field(..., alias="currentPassword", description="Current password")
    new_password: str = Field(..., min_length=6, max_length=128, alias="newPassword")
    confirm_password: Optional[str] = Field(None, alias="confirmPassword")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def passwords_match(self) -> "ChangePasswordRequest":
        if self.confirm_password and self.confirm_password != self.new_password:
            raise ValueError("Passwords do not match.")
        return self


class ForgotPasswordRequest(BaseModel):
    """Request a password reset link."""
    identifier: str = Field(..., description="Registered email address or phone number")


class ResetPasswordRequest(BaseModel):
    """
    Submit a password reset using the token from the reset link.

    The reset URL must include the user ID so the backend can perform a
    direct O(1) Redis lookup rather than scanning all keys.
    Example URL: /reset-password?userId=<uuid>&token=<urlsafe_token>
    """
    userId: str = Field(..., description="User ID embedded in the reset URL")
    token: str = Field(..., description="Password reset token from the emailed link")
    newPassword: str = Field(..., min_length=8, max_length=128)
    confirmPassword: Optional[str] = None

    @model_validator(mode="after")
    def passwords_match(self) -> "ResetPasswordRequest":
        if self.confirmPassword and self.confirmPassword != self.newPassword:
            raise ValueError("Passwords do not match.")
        return self


class AdminRegisterRequest(BaseModel):
    """
    Admin account creation.
    The first admin is bootstrapped freely (no existing admin required).
    All subsequent admins require a valid SUPER_ADMIN JWT (enforced at the endpoint level).

    Body: { full_name, email, phone?, password, adminSecret? }
    `adminSecret` is an optional server-side bootstrap key (ADMIN_BOOTSTRAP_SECRET env var).
    """
    full_name: str = Field(..., min_length=2, max_length=255, description="Admin's full name")
    email: EmailStr
    phone: Optional[str] = Field(
        None,
        pattern=r"^(?:\+91|0)?[6-9]\d{9}$|^\+?[1-9]\d{1,14}$",
        description="10-digit Indian mobile or E.164 format",
    )
    password: str = Field(..., min_length=8, max_length=128, description="Min 8 characters")
    confirmPassword: Optional[str] = Field(None, description="Must match password")
    # Optional bootstrap secret — allows first-admin creation without a JWT
    adminSecret: Optional[str] = Field(
        None, description="Bootstrap secret key (ADMIN_BOOTSTRAP_SECRET env var) for initial setup"
    )

    @model_validator(mode="after")
    def passwords_match(self) -> "AdminRegisterRequest":
        if self.confirmPassword and self.confirmPassword != self.password:
            raise ValueError("Passwords do not match.")
        return self
