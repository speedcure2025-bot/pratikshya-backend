from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List


class TransferLine(BaseModel):
    sku: str
    quantity: int


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

