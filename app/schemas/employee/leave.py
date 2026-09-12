from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class LeaveApplyRequest(BaseModel):
    startDate: Optional[date] = None
    endDate: Optional[date] = None
    type: Optional[str] = Field(None, description="CASUAL | SICK | ANNUAL | UNPAID")
    reason: Optional[str] = None

    # Alternative snake_case fields
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    leave_type: Optional[str] = None

    @model_validator(mode="after")
    def resolve_fields(self) -> "LeaveApplyRequest":
        if not self.startDate and self.start_date:
            self.startDate = self.start_date
        if not self.endDate and self.end_date:
            self.endDate = self.end_date
        if not self.type and self.leave_type:
            self.type = self.leave_type
        if not self.type:
            self.type = "CASUAL"
        if not self.startDate or not self.endDate:
            raise ValueError("Start date and end date are required.")
        return self


class LeaveDecisionRequest(BaseModel):
    status: str = Field(..., description="APPROVED | REJECTED")
    comments: Optional[str] = None
    rejection_reason: Optional[str] = None

    @model_validator(mode="after")
    def resolve_comments(self) -> "LeaveDecisionRequest":
        if not self.rejection_reason and self.comments:
            self.rejection_reason = self.comments
        return self


class LeaveResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str
    start_date: date
    end_date: date
    leave_type: str
    reason: Optional[str] = None
    status: str
    reviewed_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class LeaveBalance(BaseModel):
    casual: int = 12
    sick: int = 10
    annual: int = 15
    usedCasual: int = 0
    usedSick: int = 0
    usedAnnual: int = 0


class LeaveListResponse(BaseModel):
    ok: bool = True
    balance: Optional[LeaveBalance] = None
    items: List[LeaveResponse]
    total: int
