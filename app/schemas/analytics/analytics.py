from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class AnalyticsBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AnalyticsCreate(AnalyticsBase):
    pass


class AnalyticsResponse(AnalyticsBase):
    id: str
