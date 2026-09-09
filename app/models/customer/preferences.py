from typing import TYPE_CHECKING
from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.customer.customer import CustomerProfileModel


class CustomerPreferencesModel(Base):
    """Communication and notification preferences for a customer."""

    __tablename__ = "customer_preferences"

    customer_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("customer_profiles.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sms_notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    promotional_updates: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    order_updates: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    styling_invitations: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    customer: Mapped["CustomerProfileModel"] = relationship(
        "CustomerProfileModel", back_populates="preferences", uselist=False
    )
