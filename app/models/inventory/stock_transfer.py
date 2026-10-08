from typing import Any, Optional

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StockTransferModel(Base):
    """Database model for StockTransfer."""

    __tablename__ = "inventory_stock_transfer"

    from_warehouse_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    to_warehouse_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="DRAFT")
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    lines: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
