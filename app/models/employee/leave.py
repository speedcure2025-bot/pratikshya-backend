"""
LeaveModel — one row per leave REQUEST (not a balance ledger: the product
has no balance rules; inventing them here would be a new HR system).

Statuses and their transitions are enforced in `workforce_rules` —
PENDING → APPROVED / REJECTED / CANCELLED; reviewers may withdraw an
APPROVED request as CANCELLED; REJECTED/CANCELLED are terminal.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.auth.user import UserModel
    from app.models.employee.employee import EmployeeProfileModel


class LeaveModel(Base):
    __tablename__ = "employee_leave"

    employee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("employee_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # CASUAL, SICK, EARNED, EMERGENCY, OTHER (attendanceConfig LEAVE_TYPE)
    leave_type: Mapped[str] = mapped_column(String(20), nullable=False, default="OTHER")
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Inclusive calendar days, stored for bounded report queries.
    days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # PENDING | APPROVED | REJECTED | CANCELLED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    profile: Mapped["EmployeeProfileModel"] = relationship(
        "EmployeeProfileModel", back_populates="leave_requests"
    )
    reviewer: Mapped[Optional["UserModel"]] = relationship("UserModel")
