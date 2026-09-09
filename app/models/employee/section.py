from typing import TYPE_CHECKING, Optional
from sqlalchemy import String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.employee.department import DepartmentModel


class SectionModel(Base):
    """A sub-unit within a department (e.g. Men's Floor, Accounts Receivable)."""

    __tablename__ = "employee_section"

    department_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("employee_department.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    department: Mapped["DepartmentModel"] = relationship("DepartmentModel", back_populates="sections")
