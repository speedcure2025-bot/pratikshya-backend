from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class VariantBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class VariantCreate(VariantBase):
    pass


class VariantResponse(VariantBase):
    id: str
