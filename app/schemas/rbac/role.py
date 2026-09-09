from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class RoleBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RoleCreate(RoleBase):
    pass


class RoleResponse(RoleBase):
    id: str
