from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.auth.user import UserModel
    from app.models.employee.attendance import AttendanceModel
    from app.models.employee.target import TargetModel
    from app.models.employee.performance import PerformanceModel


class EmployeeProfileModel(Base):
    """Employee profile and operational staff identification model."""

    __tablename__ = "employee_profiles"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    employee_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    designation: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Optional FK to structured department/section tables
    department_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("employee_department.id", ondelete="SET NULL"), nullable=True, index=True
    )
    section_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("employee_section.id", ondelete="SET NULL"), nullable=True, index=True
    )

    user: Mapped["UserModel"] = relationship("UserModel", back_populates="employee_profile")
    attendance_records: Mapped[List["AttendanceModel"]] = relationship(
        "AttendanceModel", back_populates="employee", cascade="all, delete-orphan"
    )
    targets: Mapped[List["TargetModel"]] = relationship(
        "TargetModel", back_populates="employee", cascade="all, delete-orphan"
    )
    performance_reviews: Mapped[List["PerformanceModel"]] = relationship(
        "PerformanceModel", back_populates="employee", cascade="all, delete-orphan"
    )
