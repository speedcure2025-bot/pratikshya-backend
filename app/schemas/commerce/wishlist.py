from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class WishlistBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WishlistCreate(WishlistBase):
    pass


class WishlistResponse(WishlistBase):
    id: str
