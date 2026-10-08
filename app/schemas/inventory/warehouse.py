from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class WarehouseBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    code: str
    address: Optional[str] = None
    type: Optional[str] = "WAREHOUSE"


class WarehouseCreate(WarehouseBase):
    pass


class WarehouseUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None


class WarehouseResponse(WarehouseBase):
    id: str
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WarehouseListResponse(BaseModel):
    warehouses: List[WarehouseResponse]
    total: int
    page: int
    page_size: int


class SingleWarehouseResponse(BaseModel):
    warehouse: WarehouseResponse
