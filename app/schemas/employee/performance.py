from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class PerformanceCreateRequest(BaseModel):
    employee_id: str
    review_date: date
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 (poor) to 5 (excellent)")
    review_period: str = Field(default="MONTHLY", description="MONTHLY | QUARTERLY | ANNUAL")
    comments: Optional[str] = None


class PerformanceUpdateRequest(BaseModel):
    rating: Optional[int] = Field(None, ge=1, le=5)
    review_period: Optional[str] = None
    comments: Optional[str] = None


class PerformanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str
    review_date: date
    rating: int
    review_period: str
    reviewer_id: Optional[str]
    comments: Optional[str]
    created_at: datetime
    updated_at: datetime


class TargetCreateRequest(BaseModel):
    employee_id: str
    period_start: date
    period_end: date
    target_amount: float = Field(..., gt=0)
    target_type: str = Field(default="SALES", description="SALES | UNITS | etc.")
    notes: Optional[str] = None


class TargetUpdateRequest(BaseModel):
    target_amount: Optional[float] = Field(None, gt=0)
    achieved_amount: Optional[float] = Field(None, ge=0)
    target_type: Optional[str] = None
    notes: Optional[str] = None


class TargetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str
    period_start: date
    period_end: date
    target_amount: float
    achieved_amount: Optional[float]
    target_type: str
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
