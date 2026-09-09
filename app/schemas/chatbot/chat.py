from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class ChatBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ChatCreate(ChatBase):
    pass


class ChatResponse(ChatBase):
    id: str
