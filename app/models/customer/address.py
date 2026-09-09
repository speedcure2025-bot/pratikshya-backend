from typing import TYPE_CHECKING, Optional
from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.customer.customer import CustomerProfileModel


class AddressModel(Base):
    """Customer delivery/billing address."""

    __tablename__ = "customer_address"

    # FK to the customer_profiles table (not users directly)
    customer_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("customer_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    address_line: Mapped[str] = mapped_column(String(500), nullable=False)
    landmark: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    pincode: Mapped[str] = mapped_column(String(10), nullable=False)
    # "Home", "Work", or any custom string
    address_type: Mapped[str] = mapped_column(String(50), default="Home", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    customer: Mapped["CustomerProfileModel"] = relationship(
        "CustomerProfileModel", back_populates="addresses"
    )
