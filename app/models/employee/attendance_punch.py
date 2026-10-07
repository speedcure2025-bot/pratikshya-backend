from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AttendancePunchModel(Base):
    """One raw punch exactly as the machine reported it.

    Append-only: nothing edits or deletes these rows. Daily attendance is
    derived from them (`attendance_ingest_service`). `employee_id` is NULL when
    the machine PIN is not mapped to an employee yet — the punch is kept and
    attached later, never dropped.

    `punched_at` is stored as an absolute instant (UTC). The machine reports
    store wall-clock time (IST); the ingest layer converts it.
    """

    __tablename__ = "attendance_punches"
    __table_args__ = (
        # Machines re-send logs after a reconnect; the same punch must land once.
        UniqueConstraint(
            "device_id", "device_pin", "punched_at", name="uq_attendance_punch_device_pin_time"
        ),
        # Recompute / payroll read one employee's punches for a day range.
        Index("ix_attendance_punches_employee_punched_at", "employee_id", "punched_at"),
    )

    device_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("attendance_devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_pin: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    employee_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("employee_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    punched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # IN | OUT | UNKNOWN — what the machine claimed. Daily processing pairs by
    # time order, not by this value (most mall setups use a single "check" key).
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="UNKNOWN")
    verify_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="DEVICE")
    raw_line: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
