from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class PasswordBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PasswordCreate(PasswordBase):
    pass


class PasswordResponse(PasswordBase):
    id: str
