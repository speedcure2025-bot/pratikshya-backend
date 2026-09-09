from typing import TYPE_CHECKING
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.auth.user import UserModel
    from app.models.rbac.role import RoleModel


class UserRoleModel(Base):
    """Junction table associating users with roles."""

    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)

    user: Mapped["UserModel"] = relationship("UserModel", back_populates="roles")
    role: Mapped["RoleModel"] = relationship("RoleModel", back_populates="users")
