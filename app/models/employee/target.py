from typing import Optional
from decimal import Decimal
from datetime import date
from sqlalchemy import String, ForeignKey, Date, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.employee.employee import EmployeeProfileModel


class TargetModel(Base):
    """Sales/performance target assigned to an employee for a period."""

    __tablename__ = "employee_target"

    employee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("employee_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    achieved_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True, default=0)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False, default="SALES")  # SALES, UNITS, etc.
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    employee: Mapped["EmployeeProfileModel"] = relationship("EmployeeProfileModel", back_populates="targets")
