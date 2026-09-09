from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class PaymentBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PaymentCreate(PaymentBase):
    pass


class PaymentResponse(PaymentBase):
    id: str
