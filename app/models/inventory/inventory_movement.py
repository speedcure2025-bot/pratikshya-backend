from typing import Optional

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InventoryMovementModel(Base):
    """Database model for InventoryMovement."""

    __tablename__ = "inventory_inventory_movement"

    stock_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    type: Mapped[str] = mapped_column(String(50), nullable=False, default="ADJUST")
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    actor_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
