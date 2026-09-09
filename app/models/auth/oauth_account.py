from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.auth.user import UserModel


class OAuthAccountModel(Base):
    """Stores OAuth provider account linkages for a user (Google, Facebook, etc.)."""

    __tablename__ = "oauth_accounts"

    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_user"),
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="OAuth provider name: google | facebook",
    )

    provider_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Unique user ID issued by the OAuth provider",
    )

    email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Email returned by the OAuth provider",
    )

    access_token: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
        comment="Provider access token (stored for potential API calls)",
    )

    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped["UserModel"] = relationship("UserModel", back_populates="oauth_accounts")
