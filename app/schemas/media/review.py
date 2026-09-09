from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class ReviewBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ReviewCreate(ReviewBase):
    pass


class ReviewResponse(ReviewBase):
    id: str
