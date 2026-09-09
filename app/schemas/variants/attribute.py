from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class AttributeBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AttributeCreate(AttributeBase):
    pass


class AttributeResponse(AttributeBase):
    id: str
