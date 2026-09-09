from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.auth.session import UserSessionModel
    from app.models.auth.oauth_account import OAuthAccountModel
    from app.models.rbac.user_role import UserRoleModel
    from app.models.customer.customer import CustomerProfileModel
    from app.models.employee.employee import EmployeeProfileModel


class UserModel(Base):
    """Authoritative User entity for all 3 surfaces (Customer, Employee, Admin)."""

    __tablename__ = "users"

    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=True,
        comment="NULL for Facebook OAuth users who declined email permission"
        )

    phone: Mapped[Optional[str]] = mapped_column(
        String(20), 
        unique=True, 
        index=True, 
        nullable=True
        )

    full_name: Mapped[str] = mapped_column(
        String(255), 
        nullable=False
        )

    hashed_password: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="NULL for OAuth-only users who have no password"
        )

    user_type: Mapped[str] = mapped_column(
        String(50), 
        nullable=False, 
        default="customer"
        )                       # customer, employee, admin

    status: Mapped[str] = mapped_column(
        String(50), 
        nullable=False, 
        default="ACTIVE"
        )                           # ACTIVE, SUSPENDED, DEACTIVATED

    is_verified: Mapped[bool] = mapped_column(
        Boolean, 
        default=False, 
        nullable=False
        )

    force_password_change: Mapped[bool] = mapped_column(
        Boolean, 
        default=False, 
        nullable=False
        )

    # Relationships
    sessions: Mapped[List["UserSessionModel"]] = relationship("UserSessionModel", back_populates="user", cascade="all, delete-orphan")
    roles: Mapped[List["UserRoleModel"]] = relationship("UserRoleModel", back_populates="user", cascade="all, delete-orphan")
    oauth_accounts: Mapped[List["OAuthAccountModel"]] = relationship("OAuthAccountModel", back_populates="user", cascade="all, delete-orphan")
    customer_profile: Mapped[Optional["CustomerProfileModel"]] = relationship("CustomerProfileModel", back_populates="user", uselist=False)
    employee_profile: Mapped[Optional["EmployeeProfileModel"]] = relationship("EmployeeProfileModel", back_populates="user", uselist=False)
