from typing import Optional

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WarehouseModel(Base):
    """Database model for Warehouse."""

    __tablename__ = "inventory_warehouse"

    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    code: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False, default="WAREHOUSE")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
