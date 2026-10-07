from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AttendanceDeviceModel(Base):
    """A registered punching machine.

    Only devices on this list may push punches (`/iclock/*`). The serial number
    is the device identity; machines are registered by an admin and are never
    auto-created from an incoming request.
    """

    __tablename__ = "attendance_devices"

    serial_number: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
