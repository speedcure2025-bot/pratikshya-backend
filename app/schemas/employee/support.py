from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SupportCaseCreateRequest(BaseModel):
    customerId: Optional[str] = None
    subject: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    priority: str = Field(default="MEDIUM", description="LOW | MEDIUM | HIGH | URGENT")

    # Alternative snake_case
    customer_id: Optional[str] = None

    @model_validator(mode="after")
    def resolve_fields(self) -> "SupportCaseCreateRequest":
        if not self.customerId and self.customer_id:
            self.customerId = self.customer_id
        return self


class SupportCaseUpdateRequest(BaseModel):
    status: Optional[str] = Field(None, description="OPEN | IN_PROGRESS | RESOLVED | CLOSED")
    resolutionNotes: Optional[str] = None
    assignedEmployeeId: Optional[str] = None

    # Alternative snake_case
    resolution_notes: Optional[str] = None
    assigned_employee_id: Optional[str] = None

    @model_validator(mode="after")
    def resolve_fields(self) -> "SupportCaseUpdateRequest":
        if not self.resolutionNotes and self.resolution_notes:
            self.resolutionNotes = self.resolution_notes
        if not self.assignedEmployeeId and self.assigned_employee_id:
            self.assignedEmployeeId = self.assigned_employee_id
        return self


class SupportCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: Optional[str] = None
    subject: str
    description: str
    priority: str
    status: str
    assigned_employee_id: Optional[str] = None
    resolution_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SupportCaseListResponse(BaseModel):
    ok: bool = True
    items: List[SupportCaseResponse]
    total: int
