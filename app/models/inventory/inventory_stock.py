from typing import Optional

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InventoryStockModel(Base):
    """Database model for InventoryStock."""

    __tablename__ = "inventory_inventory_stock"

    product_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    variant_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sku: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    warehouse_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
