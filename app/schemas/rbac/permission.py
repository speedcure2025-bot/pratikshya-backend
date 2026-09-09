from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class PermissionBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PermissionCreate(PermissionBase):
    pass


class PermissionResponse(PermissionBase):
    id: str
