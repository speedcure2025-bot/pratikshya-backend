from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class TransferBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TransferCreate(TransferBase):
    pass


class TransferResponse(TransferBase):
    id: str
