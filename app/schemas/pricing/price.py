from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class PriceBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PriceCreate(PriceBase):
    pass


class PriceResponse(PriceBase):
    id: str
