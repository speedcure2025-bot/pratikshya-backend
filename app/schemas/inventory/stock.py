from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class StockBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StockCreate(StockBase):
    pass


class StockResponse(StockBase):
    id: str
