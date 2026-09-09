from datetime import date, time, datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class AttendanceCreateRequest(BaseModel):
    employee_id: str
    attendance_date: date
    check_in: Optional[time] = None
    check_out: Optional[time] = None
    status: str = Field(default="PRESENT", description="PRESENT | ABSENT | LATE | HALF_DAY | LEAVE")
    notes: Optional[str] = None


class AttendanceUpdateRequest(BaseModel):
    check_in: Optional[time] = None
    check_out: Optional[time] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class AttendanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str
    attendance_date: date
    check_in: Optional[time]
    check_out: Optional[time]
    status: str
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
