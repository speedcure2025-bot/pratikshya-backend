from typing import Optional
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class SupportCaseModel(Base):
    """Customer support case / ticketing record."""

    __tablename__ = "employee_support_case"

    customer_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="MEDIUM", index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN", index=True)
    assigned_employee_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
