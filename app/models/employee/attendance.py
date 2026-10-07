from typing import Optional
from datetime import date, time
from sqlalchemy import String, ForeignKey, Date, Time, Text, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.employee.employee import EmployeeProfileModel


class AttendanceModel(Base):
    """Daily attendance record for an employee."""

    __tablename__ = "employee_attendance"
    __table_args__ = (
        # One row per employee per day — the punch-duplicate guarantee.
        # Added with the dedupe migration `s2a3b4c5d6e7`; the model declares
        # the SAME index name so schema state and migrations never drift.
        Index(
            "uq_employee_attendance_employee_date",
            "employee_id",
            "attendance_date",
            unique=True,
        ),
    )

    employee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("employee_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    check_in: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    check_out: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    # PRESENT, ABSENT, LATE, HALF_DAY, LEAVE, PENDING_CORRECTION, ...
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PRESENT")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Processed results, stored so payroll reads one stable number. NULL on
    # legacy rows (readers fall back to computing from check_in/check_out).
    worked_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    late_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    early_leave_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    punch_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # MISSING_OUT | ODD_PUNCHES | PUNCH_ON_LEAVE | WEB_AND_DEVICE | NULL (no exception)
    exception: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # DEVICE (derived from machine punches) | WEB | ADMIN (manually corrected —
    # machine punches never overwrite an ADMIN row).
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="WEB", server_default="WEB")

    employee: Mapped["EmployeeProfileModel"] = relationship("EmployeeProfileModel", back_populates="attendance_records")
