from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class StockBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class StockAdjustRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    stock_id: str = Field(..., alias="stockId")
    delta: int
    type: str = Field("ADJUST", alias="type")          # ADJUST | RECEIVE | DAMAGE | RETURN
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class StockItemResponse(StockBase):
    id: str
    product_id: Optional[str] = None
    variant_id: Optional[str] = None
    sku: str
    warehouse_id: Optional[str] = None
    quantity_on_hand: int = 0
    quantity_reserved: int = 0
    quantity_available: int = 0
    low_threshold: int = 5
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class StockListResponse(BaseModel):
    items: List[StockItemResponse]
    total: int
    page: int
    page_size: int


class SingleStockResponse(BaseModel):
    item: StockItemResponse


class MovementResponse(StockBase):
    id: str
    stock_id: Optional[str] = None
    movement_type: str
    quantity_change: int
    reason: Optional[str] = None
    actor_id: Optional[str] = None
    actor_name: Optional[str] = None
    created_at: Optional[datetime] = None


class MovementListResponse(BaseModel):
    movements: List[MovementResponse]
    total: int
    page: int
    page_size: int
