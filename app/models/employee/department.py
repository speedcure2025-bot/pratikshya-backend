from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.employee.section import SectionModel
    from app.models.employee.employee import EmployeeProfileModel


class DepartmentModel(Base):
    """Organisational department that groups employees (e.g. Sales, Warehouse, HR)."""

    __tablename__ = "employee_department"

    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    sections: Mapped[List["SectionModel"]] = relationship(
        "SectionModel", back_populates="department", cascade="all, delete-orphan"
    )
