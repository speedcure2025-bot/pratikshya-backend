from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.rbac.role_permission import RolePermissionModel
    from app.models.rbac.user_role import UserRoleModel


class RoleModel(Base):
    """Role definition entity (e.g. SUPER_ADMIN, STORE_MANAGER, CUSTOMER)."""

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    permissions: Mapped[List["RolePermissionModel"]] = relationship("RolePermissionModel", back_populates="role", cascade="all, delete-orphan")
    users: Mapped[List["UserRoleModel"]] = relationship("UserRoleModel", back_populates="role", cascade="all, delete-orphan")
