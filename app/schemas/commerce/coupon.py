from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class CouponBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CouponCreate(CouponBase):
    pass


class CouponResponse(CouponBase):
    id: str
