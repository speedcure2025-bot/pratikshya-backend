from datetime import date
from typing import Optional
from sqlalchemy import Date, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class StylingAppointmentModel(Base):
    """Personal styling consultation appointment record."""

    __tablename__ = "employee_styling_appointment"

    customer_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    assigned_employee_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    appointment_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    appointment_time: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SCHEDULED", index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    preference_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
