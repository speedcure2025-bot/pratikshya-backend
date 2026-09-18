from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List
from datetime import datetime


class StockBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class StockCreate(StockBase):
    pass


class StockResponse(StockBase):
    id: str


class StockAdjustRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    product_id: Optional[str] = Field(None, alias="productId")
    sku: str
    warehouse_id: Optional[str] = Field(None, alias="warehouseId")
    delta: int
    reason: Optional[str] = None


class StockItemResponse(StockBase):
    id: str
    product_id: Optional[str] = None
    sku: str
    warehouse_id: Optional[str] = None
    quantity_on_hand: int = 0
    quantity_reserved: int = 0
    quantity_available: int = 0


class MovementResponse(StockBase):
    id: str
    stock_id: Optional[str] = None
    movement_type: str
    quantity_change: int
    reason: Optional[str] = None
    created_at: Optional[datetime] = None

