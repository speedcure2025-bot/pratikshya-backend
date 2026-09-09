from typing import Optional
from datetime import date
from sqlalchemy import String, ForeignKey, Date, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.employee.employee import EmployeeProfileModel


class PerformanceModel(Base):
    """Periodic performance review record for an employee."""

    __tablename__ = "employee_performance"

    employee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("employee_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    review_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # 1-5 rating scale
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    # MONTHLY, QUARTERLY, ANNUAL
    review_period: Mapped[str] = mapped_column(String(20), nullable=False, default="MONTHLY")
    reviewer_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    employee: Mapped["EmployeeProfileModel"] = relationship("EmployeeProfileModel", back_populates="performance_reviews")
