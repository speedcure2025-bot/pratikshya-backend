from datetime import date
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.auth.user import UserModel
    from app.models.customer.address import AddressModel
    from app.models.customer.preferences import CustomerPreferencesModel


class CustomerProfileModel(Base):
    """Customer profile information — one-to-one with UserModel."""

    __tablename__ = "customer_profiles"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Display name split (first/last) kept on the profile for personalisation
    first_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    avatar: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    loyalty_tier: Mapped[str] = mapped_column(String(50), default="BRONZE", nullable=False)
    loyalty_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    user: Mapped["UserModel"] = relationship("UserModel", back_populates="customer_profile")
    addresses: Mapped[List["AddressModel"]] = relationship(
        "AddressModel",
        back_populates="customer",
        cascade="all, delete-orphan",
        order_by="AddressModel.created_at",
    )
    preferences: Mapped[Optional["CustomerPreferencesModel"]] = relationship(
        "CustomerPreferencesModel",
        back_populates="customer",
        uselist=False,
        cascade="all, delete-orphan",
    )
