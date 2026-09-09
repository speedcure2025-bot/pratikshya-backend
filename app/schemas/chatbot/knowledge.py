from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class KnowledgeBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class KnowledgeCreate(KnowledgeBase):
    pass


class KnowledgeResponse(KnowledgeBase):
    id: str
