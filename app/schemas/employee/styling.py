from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StylingAppointmentCreateRequest(BaseModel):
    customerId: Optional[str] = None
    assignedEmployeeId: Optional[str] = None
    appointmentDate: date
    appointmentTime: Optional[str] = None
    notes: Optional[str] = None
    preferenceSummary: Optional[str] = None

    # Alternative snake_case
    customer_id: Optional[str] = None
    assigned_employee_id: Optional[str] = None
    appointment_date: Optional[date] = None
    appointment_time: Optional[str] = None
    preference_summary: Optional[str] = None

    @model_validator(mode="after")
    def resolve_fields(self) -> "StylingAppointmentCreateRequest":
        if not self.customerId and self.customer_id:
            self.customerId = self.customer_id
        if not self.assignedEmployeeId and self.assigned_employee_id:
            self.assignedEmployeeId = self.assigned_employee_id
        if not self.appointmentDate and self.appointment_date:
            self.appointmentDate = self.appointment_date
        if not self.appointmentTime and self.appointment_time:
            self.appointmentTime = self.appointment_time
        if not self.preferenceSummary and self.preference_summary:
            self.preferenceSummary = self.preference_summary
        return self


class StylingAppointmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: Optional[str] = None
    assigned_employee_id: Optional[str] = None
    appointment_date: date
    appointment_time: Optional[str] = None
    status: str
    notes: Optional[str] = None
    preference_summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class StylingAppointmentListResponse(BaseModel):
    ok: bool = True
    items: List[StylingAppointmentResponse]
    total: int
