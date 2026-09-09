from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class RegisterBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterCreate(RegisterBase):
    pass


class RegisterResponse(RegisterBase):
    id: str
