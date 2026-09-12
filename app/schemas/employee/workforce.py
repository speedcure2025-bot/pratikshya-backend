"""
Workforce request/response schemas — attendance punches, leave, performance.

Field names follow the frontend workforce mirror (camelCase on the wire,
because `frontend/src/services/workforce/*Repository.js` normalise exactly
these keys; the PF `employeeId` code is the identity the diary and mirror
share). Server-side validation only decides legality — WHO may act is RBAC.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Attendance ─────────────────────────────────────────────────────────────


class PunchRequest(BaseModel):
    """Optional explicit timestamp (ISO). Absent → server store clock."""

    model_config = ConfigDict(populate_by_name=True)
    at: Optional[str] = None


class WorkforceAttendanceDto(BaseModel):
    """One attendance day, shaped for the frontend mirror."""

    model_config = ConfigDict(populate_by_name=True)

    attendanceId: str
    employeeId: str = Field(description="PF employee code")
    employeeName: Optional[str] = None
    date: date
    checkIn: Optional[datetime] = None
    checkOut: Optional[datetime] = None
    status: str
    workMinutes: int = 0
    lateMinutes: int = 0
    earlyLeaveMinutes: int = 0
    notes: Optional[str] = None
    updatedAt: Optional[datetime] = None


class PunchResult(BaseModel):
    record: WorkforceAttendanceDto
    lateMinutes: int = 0
    workMinutes: int = 0
    earlyLeaveMinutes: int = 0
    message: str


class AttendanceSummary(BaseModel):
    present: int = 0
    late: int = 0
    halfDay: int = 0
    onLeave: int = 0
    absent: int = 0
    other: int = 0
    totalWorkMinutes: int = 0


class MyAttendanceResponse(BaseModel):
    items: List[WorkforceAttendanceDto]
    summary: AttendanceSummary


class TodayAttendanceResponse(BaseModel):
    record: Optional[WorkforceAttendanceDto] = None
    message: Optional[str] = None


# ── Leave ───────────────────────────────────────────────────────────────────


class LeaveCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    leaveType: str = Field(default="OTHER")
    startDate: date
    endDate: Optional[date] = None
    reason: Optional[str] = None


class LeaveDecisionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    decision: str = Field(
        description="APPROVED | REJECTED (also accepts the contract spellings approve | reject)"
    )
    reviewNote: Optional[str] = None
    # Contract API-LEV-04 names the field `notes`; both are accepted and the
    # server treats them as one field (last explicit value wins).
    notes: Optional[str] = None


class LeaveDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    leaveId: str
    employeeId: str = Field(description="PF employee code")
    employeeName: Optional[str] = None
    leaveType: str
    startDate: date
    endDate: date
    days: int
    reason: Optional[str] = None
    status: str
    requestedAt: datetime
    reviewedAt: Optional[datetime] = None
    reviewedBy: Optional[str] = None
    reviewNote: Optional[str] = None


class LeaveListResponse(BaseModel):
    items: List[LeaveDto]
    total: int


# ── Performance ─────────────────────────────────────────────────────────────


class PerformanceCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    employeeId: Optional[str] = Field(
        default=None, description="PF code or user id; omitted on self routes"
    )
    reviewDate: Optional[date] = None
    rating: int
    reviewPeriod: str = Field(default="MONTHLY", description="MONTHLY | QUARTERLY | ANNUAL")
    comments: Optional[str] = None


class PerformanceUpdateRequest(BaseModel):
    rating: Optional[int] = None
    reviewPeriod: Optional[str] = None
    comments: Optional[str] = None
    reviewDate: Optional[date] = None


class PerformanceDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    performanceId: str
    employeeId: str = Field(description="PF employee code")
    employeeName: Optional[str] = None
    reviewDate: date
    rating: int
    reviewPeriod: str
    reviewerId: Optional[str] = None
    reviewerName: Optional[str] = None
    comments: Optional[str] = None
    createdAt: Optional[datetime] = None


class PerformanceListResponse(BaseModel):
    items: List[PerformanceDto]
    total: int


class PerformanceSelfResponse(BaseModel):
    reviews: List[PerformanceDto]
    summary: Dict[str, Any]
