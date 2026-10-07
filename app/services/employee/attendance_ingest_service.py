"""
AttendanceIngestService — machine punches → raw log → daily attendance.

Flow (see `app/api/v1/iclock.py` for the device-facing HTTP side):

    parsed ADMS lines ──► attendance_punches (append-only, de-duplicated)
                              │
                              ▼
                   recompute_day(employee, day)
                              │   first/last punch, paired hours, late,
                              ▼   exceptions  (pure rules: workforce_rules)
                    employee_attendance (one row per employee per day)

Rules that protect the numbers:
  • A punch is NEVER dropped: unknown PINs are stored unmapped and attached the
    moment an admin maps the PIN (`set_device_pin`).
  • An ADMIN-corrected daily row is never overwritten by later machine punches.
  • A machine punch on an approved-leave day does not erase the leave; it flags
    PUNCH_ON_LEAVE for an admin to review.
  • No employee-facing endpoint accepts a timestamp; only the machine does.
"""

from __future__ import annotations

import re
from datetime import date as date_t, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    NotFoundException,
)
from app.models.employee.attendance import AttendanceModel
from app.models.employee.attendance_device import AttendanceDeviceModel
from app.models.employee.attendance_punch import AttendancePunchModel
from app.models.employee.employee import EmployeeProfileModel
from app.services.employee import workforce_rules as rules
from app.services.employee.adms import ParsedPunch
from app.services.employee.workforce_service import WorkforceService

#: A punch stamped further ahead than this is a machine with a wrong clock.
FUTURE_TOLERANCE = timedelta(hours=1)
#: Recompute at most this many days when a PIN is mapped (bounded work).
MAX_BACKFILL_DAYS = 62

_PIN_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")
_SERIAL_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


class AttendanceIngestService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.wf = WorkforceService(db)

    # ------------------------------------------------------------------ #
    #  Devices                                                            #
    # ------------------------------------------------------------------ #

    async def device_by_serial(self, serial: str) -> Optional[AttendanceDeviceModel]:
        if not serial:
            return None
        return (
            await self.db.execute(
                select(AttendanceDeviceModel).where(AttendanceDeviceModel.serial_number == serial)
            )
        ).scalars().first()

    async def touch(self, device: AttendanceDeviceModel) -> None:
        device.last_seen_at = datetime.now(timezone.utc)
        await self.db.commit()

    async def list_devices(self) -> List[dict]:
        rows = (
            await self.db.execute(
                select(AttendanceDeviceModel).order_by(AttendanceDeviceModel.created_at.asc())
            )
        ).scalars().all()
        return [self._device_dto(d) for d in rows]

    async def create_device(self, serial: str, label: str, actor_id: Optional[str]) -> dict:
        serial = (serial or "").strip()
        if not _SERIAL_RE.match(serial):
            raise BusinessLogicException("Serial number must be 3-64 letters, digits, '.', '_' or '-'.")
        if await self.device_by_serial(serial):
            raise ConflictException("A machine with this serial number is already registered.")
        device = AttendanceDeviceModel(serial_number=serial, label=(label or "").strip()[:100])
        self.db.add(device)
        await self.wf._audit("ATTENDANCE_DEVICE_ADDED", actor_id, None, serial=serial)
        await self.db.commit()
        await self.db.refresh(device)
        return self._device_dto(device)

    async def update_device(
        self, device_id: str, label: Optional[str], is_active: Optional[bool], actor_id: Optional[str]
    ) -> dict:
        device = await self.db.get(AttendanceDeviceModel, device_id)
        if not device:
            raise NotFoundException("Machine not found.")
        if label is not None:
            device.label = label.strip()[:100]
        if is_active is not None:
            device.is_active = is_active
        await self.wf._audit(
            "ATTENDANCE_DEVICE_UPDATED", actor_id, None, serial=device.serial_number, active=device.is_active
        )
        await self.db.commit()
        await self.db.refresh(device)
        return self._device_dto(device)

    @staticmethod
    def _device_dto(device: AttendanceDeviceModel) -> dict:
        return {
            "deviceId": device.id,
            "serialNumber": device.serial_number,
            "label": device.label,
            "isActive": device.is_active,
            "lastSeenAt": device.last_seen_at,
        }

    # ------------------------------------------------------------------ #
    #  Ingest                                                             #
    # ------------------------------------------------------------------ #

    async def _pin_map(self, pins: Sequence[str]) -> Dict[str, str]:
        if not pins:
            return {}
        rows = (
            await self.db.execute(
                select(EmployeeProfileModel.device_pin, EmployeeProfileModel.id).where(
                    EmployeeProfileModel.device_pin.in_(list(set(pins)))
                )
            )
        ).all()
        return {pin: profile_id for pin, profile_id in rows}

    async def ingest(self, device: AttendanceDeviceModel, punches: Sequence[ParsedPunch]) -> Dict[str, int]:
        """Store punches and refresh the affected daily rows. Idempotent: the
        same log re-sent by the machine inserts nothing and changes nothing."""
        counts = {"accepted": 0, "duplicates": 0, "unmapped": 0, "futureRejected": 0}
        pin_map = await self._pin_map([p.pin for p in punches])
        limit = datetime.now(timezone.utc) + FUTURE_TOLERANCE
        touched: set = set()

        for punch in punches:
            if punch.punched_at.astimezone(timezone.utc) > limit:
                counts["futureRejected"] += 1
                continue
            profile_id = pin_map.get(punch.pin)
            inserted = (
                await self.db.execute(
                    pg_insert(AttendancePunchModel)
                    .values(
                        device_id=device.id,
                        device_pin=punch.pin,
                        employee_id=profile_id,
                        punched_at=punch.punched_at,
                        direction=punch.direction,
                        verify_type=punch.verify_type,
                        source="DEVICE",
                        raw_line=punch.raw_line,
                    )
                    .on_conflict_do_nothing(constraint="uq_attendance_punch_device_pin_time")
                    .returning(AttendancePunchModel.id)
                )
            ).first()
            if inserted is None:
                counts["duplicates"] += 1
                continue
            counts["accepted"] += 1
            if profile_id is None:
                counts["unmapped"] += 1
            else:
                touched.add((profile_id, rules.day_of(rules.to_store_naive(punch.punched_at))))

        if touched:
            settings = await self.wf.settings()
            for profile_id, day in sorted(touched, key=lambda item: (item[1], item[0])):
                await self.recompute_day(profile_id, day, settings)
        device.last_seen_at = datetime.now(timezone.utc)
        await self.db.commit()
        return counts

    # ------------------------------------------------------------------ #
    #  Processing                                                         #
    # ------------------------------------------------------------------ #

    async def recompute_day(
        self, profile_id: str, day: date_t, settings: Optional[Dict[str, Any]] = None
    ) -> Optional[AttendanceModel]:
        """Rebuild one employee-day from raw punches. Does not commit."""
        settings = settings or await self.wf.settings()
        start, end = rules.store_day_bounds_utc(day)
        stamps = (
            await self.db.execute(
                select(AttendancePunchModel.punched_at)
                .where(
                    AttendancePunchModel.employee_id == profile_id,
                    AttendancePunchModel.punched_at >= start,
                    AttendancePunchModel.punched_at < end,
                )
                .order_by(AttendancePunchModel.punched_at.asc())
            )
        ).scalars().all()
        if not stamps:
            return None

        record = (
            await self.db.execute(
                select(AttendanceModel).where(
                    AttendanceModel.employee_id == profile_id,
                    AttendanceModel.attendance_date == day,
                )
            )
        ).scalars().first()

        if record is not None and record.source == "ADMIN":
            return record  # a manual correction is final; raw punches stay as evidence

        # Approved leave materialised a LEAVE row with no punches: keep it, flag it.
        if record is not None and record.status == "LEAVE" and record.check_in is None:
            record.exception = "PUNCH_ON_LEAVE"
            return record

        # Same day punched on the web page AND on a machine (transition period):
        # the web row is a different evidence trail, so do not merge or overwrite
        # it — flag it for an admin to settle.
        if record is not None and record.source == "WEB" and record.check_in is not None:
            record.exception = "WEB_AND_DEVICE"
            return record

        summary = rules.summarize_punches(
            day,
            [rules.to_store_naive(stamp) for stamp in stamps],
            settings,
            today=rules.day_of(rules.store_now()),
        )
        if summary is None:
            return record

        values = {
            "check_in": summary["check_in"].time(),
            "check_out": summary["check_out"].time() if summary["check_out"] else None,
            "status": summary["status"],
            "worked_minutes": summary["worked_minutes"],
            "late_minutes": summary["late_minutes"],
            "early_leave_minutes": summary["early_leave_minutes"],
            "punch_count": summary["punch_count"],
            "exception": summary["exception"],
            "source": "DEVICE",
        }
        if record is None:
            record = AttendanceModel(employee_id=profile_id, attendance_date=day, **values)
            self.db.add(record)
        else:
            for field, value in values.items():
                setattr(record, field, value)
        return record

    async def sweep_open_days(self) -> int:
        """Flag finished days that still have a check-in and no check-out.

        Run lazily (day view, payroll) instead of by a scheduler so no new
        infrastructure is needed. ADMIN-corrected rows are never touched.
        """
        today = rules.day_of(rules.store_now())
        floor = today - timedelta(days=MAX_BACKFILL_DAYS)
        result = await self.db.execute(
            update(AttendanceModel)
            .where(
                AttendanceModel.attendance_date < today,
                AttendanceModel.attendance_date >= floor,
                AttendanceModel.check_in.is_not(None),
                AttendanceModel.check_out.is_(None),
                AttendanceModel.exception.is_(None),
                AttendanceModel.source != "ADMIN",
                AttendanceModel.status.in_(("PRESENT", "LATE", "ON_DUTY")),
            )
            .values(
                exception=case((AttendanceModel.punch_count > 1, "ODD_PUNCHES"), else_="MISSING_OUT"),
                status="PENDING_CORRECTION",
            )
        )
        await self.db.commit()
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------ #
    #  PIN mapping                                                        #
    # ------------------------------------------------------------------ #

    async def unmapped_pins(self) -> List[dict]:
        rows = (
            await self.db.execute(
                select(
                    AttendancePunchModel.device_pin,
                    AttendanceDeviceModel.serial_number,
                    func.count(AttendancePunchModel.id),
                    func.min(AttendancePunchModel.punched_at),
                    func.max(AttendancePunchModel.punched_at),
                )
                .outerjoin(AttendanceDeviceModel, AttendanceDeviceModel.id == AttendancePunchModel.device_id)
                .where(AttendancePunchModel.employee_id.is_(None))
                .group_by(AttendancePunchModel.device_pin, AttendanceDeviceModel.serial_number)
                .order_by(func.max(AttendancePunchModel.punched_at).desc())
                .limit(200)
            )
        ).all()
        return [
            {
                "devicePin": pin,
                "deviceSerial": serial,
                "punches": int(count),
                "firstAt": first,
                "lastAt": last,
            }
            for pin, serial, count, first, last in rows
        ]

    async def set_device_pin(self, employee_ref: str, pin: Optional[str], actor_id: Optional[str]) -> dict:
        user = await self.wf.repo.get_any_staff_by_id(employee_ref)
        profile = user.employee_profile if user else None
        if profile is None:
            raise NotFoundException("Employee not found.")

        pin = (pin or "").strip() or None
        if pin is not None:
            if not _PIN_RE.match(pin):
                raise BusinessLogicException("Machine ID must be 1-32 letters or digits.")
            clash = (
                await self.db.execute(
                    select(EmployeeProfileModel.employee_code).where(
                        EmployeeProfileModel.device_pin == pin, EmployeeProfileModel.id != profile.id
                    )
                )
            ).scalars().first()
            if clash:
                raise ConflictException(f"Machine ID {pin} is already assigned to {clash}.")

        profile.device_pin = pin
        await self.db.flush()

        attached = 0
        if pin is not None:
            result = await self.db.execute(
                update(AttendancePunchModel)
                .where(AttendancePunchModel.employee_id.is_(None), AttendancePunchModel.device_pin == pin)
                .values(employee_id=profile.id)
            )
            attached = int(result.rowcount or 0)
            if attached:
                stamps = (
                    await self.db.execute(
                        select(AttendancePunchModel.punched_at).where(
                            AttendancePunchModel.employee_id == profile.id,
                            AttendancePunchModel.device_pin == pin,
                        )
                    )
                ).scalars().all()
                days = sorted({rules.day_of(rules.to_store_naive(s)) for s in stamps}, reverse=True)
                settings = await self.wf.settings()
                for day in days[:MAX_BACKFILL_DAYS]:
                    await self.recompute_day(profile.id, day, settings)

        await self.wf._audit(
            "ATTENDANCE_PIN_MAPPED", actor_id, profile.employee_code, device_pin=pin or "cleared"
        )
        await self.db.commit()
        return {"employeeId": profile.employee_code, "devicePin": pin, "attachedPunches": attached}
