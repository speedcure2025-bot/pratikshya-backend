"""Customer behavioral data, NOT the shared operational audit diary."""
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class UserProductInteractionModel(Base):
    __tablename__ = "user_product_interactions"

    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("catalog_product.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(100), nullable=False)
    event_bucket: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint("event_type IN ('VIEW','CLICK','WISHLIST','UNWISHLIST','CART_ADD','CART_REMOVE','PURCHASE')", name="ck_interaction_event_type"),
        UniqueConstraint("customer_id", "dedup_key", name="uq_interaction_retry"),
        UniqueConstraint("customer_id", "product_id", "event_type", "event_bucket", name="uq_interaction_bucket"),
        Index("ix_interaction_customer_recent", "customer_id", "created_at"),
        Index("ix_interaction_product_recent", "product_id", "created_at"),
        Index("ix_interaction_type_recent", "event_type", "created_at"),
        Index("ix_interaction_retention", "created_at"),
    )
