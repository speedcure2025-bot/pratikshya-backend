from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class TransferLine(BaseModel):
    sku: Optional[str] = None
    stock_id: Optional[str] = Field(None, alias="stockId")
    quantity: int

    model_config = ConfigDict(populate_by_name=True)


class TransferBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class TransferCreate(TransferBase):
    from_warehouse_id: str = Field(..., alias="fromWarehouseId")
    to_warehouse_id: str = Field(..., alias="toWarehouseId")
    notes: Optional[str] = None
    lines: List[TransferLine] = []


class TransferResponse(TransferBase):
    id: str
    transfer_number: str
    from_warehouse_id: str
    to_warehouse_id: str
    status: str
    notes: Optional[str] = None
    lines: Optional[List[Dict[str, Any]]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TransferListResponse(BaseModel):
    transfers: List[TransferResponse]
    total: int
    page: int
    page_size: int


class SingleTransferResponse(BaseModel):
    transfer: TransferResponse
