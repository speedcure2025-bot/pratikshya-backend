from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class WarehouseBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WarehouseCreate(WarehouseBase):
    pass


class WarehouseResponse(WarehouseBase):
    id: str
