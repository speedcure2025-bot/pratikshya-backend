from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class CheckoutBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CheckoutCreate(CheckoutBase):
    pass


class CheckoutResponse(CheckoutBase):
    id: str
