from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class ConversationBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ConversationCreate(ConversationBase):
    pass


class ConversationResponse(ConversationBase):
    id: str
