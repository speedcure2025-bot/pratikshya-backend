from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class WarehouseBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    code: str
    address: Optional[str] = None
    type: Optional[str] = "WAREHOUSE"


class WarehouseCreate(WarehouseBase):
    pass


class WarehouseResponse(WarehouseBase):
    id: str
    is_active: bool = True

