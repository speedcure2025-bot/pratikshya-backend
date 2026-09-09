from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class TagBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TagCreate(TagBase):
    pass


class TagResponse(TagBase):
    id: str
