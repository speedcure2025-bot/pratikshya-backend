from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class ReturnOrderBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ReturnOrderCreate(ReturnOrderBase):
    pass


class ReturnOrderResponse(ReturnOrderBase):
    id: str
