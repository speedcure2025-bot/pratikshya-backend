from typing import Optional
from datetime import date, time
from sqlalchemy import String, ForeignKey, Date, Time, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.employee.employee import EmployeeProfileModel


class AttendanceModel(Base):
    """Daily attendance record for an employee."""

    __tablename__ = "employee_attendance"

    employee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("employee_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    check_in: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    check_out: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    # PRESENT, ABSENT, LATE, HALF_DAY, LEAVE
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PRESENT")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    employee: Mapped["EmployeeProfileModel"] = relationship("EmployeeProfileModel", back_populates="attendance_records")
