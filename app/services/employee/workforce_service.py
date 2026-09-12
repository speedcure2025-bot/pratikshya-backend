"""
WorkforceService — attendance punches, leave requests, performance reviews.

Kept separate from `EmployeeService` (the ACCOUNT domain) so neither file
grows a second responsibility; both share the same models, the shared audit
diary writer and the RBAC dependency checks — no parallel auth, no parallel
log. Business legality lives here; authority lives in `app/dependencies.py`.
"""

from __future__ import annotations

import logging
from datetime import date as date_t, datetime, time as time_t
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from app.dependencies import require_staff_permission
from app.models.admin.setting import SettingModel
from app.models.auth.user import UserModel
from app.models.employee.attendance import AttendanceModel
from app.models.employee.employee import EmployeeProfileModel
from app.models.employee.leave import LeaveModel
from app.models.employee.performance import PerformanceModel
from app.repositories.employee.employee_repository import EmployeeRepository
from app.services.employee import workforce_rules as rules

#: second join target for reviewer display names (performance lists)
ReviewerModel = aliased(UserModel)

logger = logging.getLogger("pfv.workforce")

#: Leave approval may only derive attendance for a bounded window.
MAX_LEAVE_DAYS = 60
#: Bounded reads everywhere (mandate §13).
MAX_PAGE_SIZE = 100


class WorkforceService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = EmployeeRepository(db)

    # ------------------------------------------------------------------ #
    #  Resolution + settings                                             #
    # ------------------------------------------------------------------ #

    async def _profile_for_user(self, user: UserModel) -> EmployeeProfileModel:
        """Resolve ``user``'s EmployeeProfileModel without a synchronous lazy-load.

        The auth dependency and ``EmployeeRepository`` eager-load
        ``UserModel.employee_profile`` (``selectinload``) for the paths that feed
        this method, so the relationship is normally already populated. When it
        is not (a bare ``UserModel`` from any other loader), we issue ONE bounded,
        indexed lookup through the async session instead of touching the
        relationship — direct relationship access in an async context would
        attempt IO via ``greenlet_spawn`` and raise ``MissingGreenlet``.
        """
        if "employee_profile" in sa_inspect(user).unloaded:
            profile = (
                await self.db.execute(
                    select(EmployeeProfileModel).where(EmployeeProfileModel.user_id == user.id)
                )
            ).scalars().first()
        else:
            profile = user.employee_profile
        if profile is None:
            # A missing profile is a data fault for an employee account, not a 403.
            raise NotFoundException("Employee profile not found for this account.")
        return profile

    async def _resolve_employee(self, ident: str) -> UserModel:
        user = await self.repo.get_employee_by_id(ident)
        if not user:
            raise NotFoundException("Employee not found.")
        return user

    async def settings(self) -> Dict[str, Any]:
        """`attendance` section merged over core defaults, plus `holidays`."""
        rows = (
            await self.db.execute(
                select(SettingModel).where(SettingModel.id.in_(["attendance", "holidays"]))
            )
        ).scalars().all()
        stored = {row.id: (row.value or {}) for row in rows}
        settings = rules.resolve_settings(stored.get("attendance"))
        holidays_block = stored.get("holidays") or {}
        entries = holidays_block.get("items") or holidays_block.get("list") or []
        active = [
            {"date": str(item.get("date")), "name": str(item.get("name") or "Holiday")}
            for item in entries
            if isinstance(item, dict) and item.get("date") and item.get("active", True) is not False
        ]
        settings["holidays"] = active + [
            h for h in settings["holidays"] if h["date"] not in {a["date"] for a in active}
        ]
        return settings

    # ------------------------------------------------------------------ #
    #  DTO mapping                                                       #
    # ------------------------------------------------------------------ #

    async def _attendance_dtos(self, rows: Sequence[Tuple[AttendanceModel, str, Optional[str]]]) -> List[dict]:
        settings = await self.settings()
        out = []
        for record, code, name in rows:
            day = record.attendance_date
            check_in = datetime.combine(day, record.check_in) if record.check_in else None
            check_out = datetime.combine(day, record.check_out) if record.check_out else None
            timing = rules.evaluate_timing(day, check_in, check_out, settings)
            out.append(
                {
                    "attendanceId": record.id,
                    "employeeId": code,
                    "employeeName": name,
                    "date": day,
                    "checkIn": check_in,
                    "checkOut": check_out,
                    "status": record.status,
                    "notes": record.notes,
                    "updatedAt": record.updated_at,
                    **timing,
                }
            )
        return out

    async def _leave_dtos(self, rows: Sequence[Tuple[LeaveModel, str, Optional[str]]]) -> List[dict]:
        return [
            {
                "leaveId": leave.id,
                "employeeId": code,
                "employeeName": name,
                "leaveType": leave.leave_type,
                "startDate": leave.start_date,
                "endDate": leave.end_date,
                "days": leave.days,
                "reason": leave.reason,
                "status": leave.status,
                "requestedAt": leave.requested_at,
                "reviewedAt": leave.reviewed_at,
                "reviewedBy": leave.reviewed_by,
                "reviewNote": leave.review_note,
            }
            for leave, code, name in rows
        ]

    async def _performance_dtos(self, rows) -> List[dict]:
        return [
            {
                "performanceId": perf.id,
                "employeeId": code,
                "employeeName": name,
                "reviewerName": reviewer_name,
                "reviewDate": perf.review_date,
                "rating": perf.rating,
                "reviewPeriod": perf.review_period,
                "reviewerId": perf.reviewer_id,
                "comments": perf.comments,
                "createdAt": perf.created_at,
            }
            for perf, code, name, reviewer_name in rows
        ]

    # The join used by every list: attendance/leave/perf → profile → user.
    @staticmethod
    def _base_select(entity):
        return (
            select(entity, EmployeeProfileModel.employee_code, UserModel.full_name)
            .join(EmployeeProfileModel, entity.employee_id == EmployeeProfileModel.id)
            .join(UserModel, UserModel.id == EmployeeProfileModel.user_id)
        )

    # ------------------------------------------------------------------ #
    #  Attendance — self punches                                         #
    # ------------------------------------------------------------------ #

    async def punch_in(self, user: UserModel, at: Optional[str]) -> dict:
        profile = await self._profile_for_user(user)
        settings = await self.settings()
        when = rules.to_store_wallclock(at) if at else rules.store_now()
        day = rules.day_of(when)

        if day > rules.day_of(rules.store_now()):
            raise BusinessLogicException("Cannot check in for a future day.")

        record = (
            await self.db.execute(
                select(AttendanceModel).where(
                    AttendanceModel.employee_id == profile.id,
                    AttendanceModel.attendance_date == day,
                )
            )
        ).scalars().first()
        if record is not None and record.check_in is not None:
            raise ConflictException("You are already checked in for today.")

        on_leave = await self._approved_leave_on(profile.id, day)
        if on_leave:
            raise ForbiddenException("You are on approved leave today.")

        check_in_time = when.time()
        prior_out = datetime.combine(day, record.check_out) if (record and record.check_out) else None
        status = rules.status_after_punch(
            day,
            datetime.combine(day, check_in_time),
            prior_out,
            settings,
        )
        timing = rules.evaluate_timing(
            day, datetime.combine(day, check_in_time), None, settings
        )

        if record is None:
            record = AttendanceModel(
                employee_id=profile.id,
                attendance_date=day,
                check_in=check_in_time,
                status=status,
            )
            self.db.add(record)
        else:
            record.check_in = check_in_time
            record.check_out = None
            record.status = status
        await self._audit("ATTENDANCE_CHECKED_IN", user.id, profile.employee_code)
        await self.db.commit()
        await self.db.refresh(record)
        return {
            "record": (await self._attendance_dtos([(record, profile.employee_code, user.full_name)]))[0],
            **timing,
            "message": (
                f"You checked in {timing['lateMinutes']} minute{'s' if timing['lateMinutes'] != 1 else ''} late."
                if timing["lateMinutes"] > 0
                else "Checked in."
            ),
        }

    async def punch_out(self, user: UserModel, at: Optional[str]) -> dict:
        profile = await self._profile_for_user(user)
        settings = await self.settings()
        when = rules.to_store_wallclock(at) if at else rules.store_now()
        day = rules.day_of(when)

        record = (
            await self.db.execute(
                select(AttendanceModel).where(
                    AttendanceModel.employee_id == profile.id,
                    AttendanceModel.attendance_date == day,
                )
            )
        ).scalars().first()
        if record is None or record.check_in is None:
            raise ConflictException("Check in before you check out.")
        if record.check_out is not None:
            raise ConflictException("You have already checked out today.")

        check_in_dt = datetime.combine(day, record.check_in)
        check_out_dt = when if rules.day_of(when) == day else datetime.combine(day, when.time())
        if check_out_dt < check_in_dt:
            raise BusinessLogicException("Check-out cannot be earlier than check-in.")

        status = rules.status_after_punch(day, check_in_dt, check_out_dt, settings)
        timing = rules.evaluate_timing(day, check_in_dt, check_out_dt, settings)
        record.check_out = check_out_dt.time()
        record.status = status
        await self._audit("ATTENDANCE_CHECKED_OUT", user.id, profile.employee_code)
        await self.db.commit()
        await self.db.refresh(record)
        return {
            "record": (await self._attendance_dtos([(record, profile.employee_code, user.full_name)]))[0],
            **timing,
            "message": f"Checked out · {timing['workMinutes']} minutes on the floor.",
        }

    async def today(self, user: UserModel) -> Optional[dict]:
        profile = await self._profile_for_user(user)
        day = rules.day_of(rules.store_now())
        record = (
            await self.db.execute(
                select(AttendanceModel).where(
                    AttendanceModel.employee_id == profile.id,
                    AttendanceModel.attendance_date == day,
                )
            )
        ).scalars().first()
        if record is None:
            return None
        rows = await self._attendance_dtos([(record, profile.employee_code, user.full_name)])
        return rows[0]

    async def my_month(self, user: UserModel, month: Optional[str]) -> Tuple[List[dict], dict]:
        profile = await self._profile_for_user(user)
        month = month or rules.day_of(rules.store_now()).strftime("%Y-%m")
        start, end = rules.month_bounds(month)
        rows = (
            await self.db.execute(
                self._base_select(AttendanceModel)
                .where(
                    AttendanceModel.employee_id == profile.id,
                    AttendanceModel.attendance_date >= start,
                    AttendanceModel.attendance_date < end,
                )
                .order_by(AttendanceModel.attendance_date.desc())
                .limit(MAX_PAGE_SIZE * 4)  # 31-day month, bounded
            )
        ).all()
        items = await self._attendance_dtos(rows)
        summary = {
            "present": sum(1 for r in items if r["status"] == "PRESENT"),
            "late": sum(1 for r in items if r["status"] == "LATE"),
            "halfDay": sum(1 for r in items if r["status"] == "HALF_DAY"),
            "onLeave": sum(1 for r in items if r["status"] == "LEAVE"),
            "absent": sum(1 for r in items if r["status"] == "ABSENT"),
            "other": sum(
                1 for r in items if r["status"] not in {"PRESENT", "LATE", "HALF_DAY", "LEAVE", "ABSENT"}
            ),
            "totalWorkMinutes": sum(r["workMinutes"] for r in items),
        }
        return items, summary

    async def _approved_leave_on(self, profile_id: str, day: date_t) -> bool:
        count = (
            await self.db.execute(
                select(func.count())
                .select_from(LeaveModel)
                .where(
                    LeaveModel.employee_id == profile_id,
                    LeaveModel.status == "APPROVED",
                    LeaveModel.start_date <= day,
                    LeaveModel.end_date >= day,
                )
            )
        ).scalar() or 0
        return count > 0

    # ------------------------------------------------------------------ #
    #  Leave                                                             #
    # ------------------------------------------------------------------ #

    async def create_leave(self, user: UserModel, req) -> dict:
        profile = await self._profile_for_user(user)
        if req.leaveType not in rules.LEAVE_TYPES:
            raise BusinessLogicException(
                f"Unknown leave type '{req.leaveType}'. Expected: {', '.join(sorted(rules.LEAVE_TYPES))}."
            )
        start = req.startDate
        end = req.endDate or req.startDate
        if end < start:
            start, end = end, start
        days = rules.inclusive_day_count(start, end)
        if days > MAX_LEAVE_DAYS:
            raise BusinessLogicException(f"A single leave request may not exceed {MAX_LEAVE_DAYS} days.")

        clash = (
            await self.db.execute(
                select(LeaveModel.id).where(
                    LeaveModel.employee_id == profile.id,
                    LeaveModel.status.notin_(("REJECTED", "CANCELLED")),
                    LeaveModel.start_date <= end,
                    LeaveModel.end_date >= start,
                ).limit(1)
            )
        ).scalars().first()
        if clash:
            raise BusinessLogicException("These dates overlap an open leave request.")

        leave = LeaveModel(
            employee_id=profile.id,
            leave_type=req.leaveType,
            start_date=start,
            end_date=end,
            days=days,
            reason=(req.reason or "").strip() or None,
            status="PENDING",
            requested_at=datetime.utcnow(),
        )
        self.db.add(leave)
        await self._audit("LEAVE_REQUESTED", user.id, profile.employee_code, days=days, type=req.leaveType)
        await self.db.commit()
        await self.db.refresh(leave)
        rows = await self._leave_dtos([(leave, profile.employee_code, user.full_name)])
        return rows[0]

    async def list_leave(
        self,
        *,
        employee_user: Optional[UserModel] = None,
        employee_id: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[dict], int]:
        stmt = self._base_select(LeaveModel)
        if employee_user is not None:
            profile = await self._profile_for_user(employee_user)
            stmt = stmt.where(LeaveModel.employee_id == profile.id)
        elif employee_id:
            stmt = stmt.where(EmployeeProfileModel.employee_code == employee_id)
        if status:
            if status not in rules.LEAVE_STATUSES:
                raise BusinessLogicException(f"Unknown status '{status}'.")
            stmt = stmt.where(LeaveModel.status == status)
        total = (
            await self.db.execute(
                select(func.count()).select_from(
                    stmt.with_only_columns(LeaveModel.id).order_by(None).subquery()
                )
            )
        ).scalar() or 0
        rows = (
            await self.db.execute(
                stmt.order_by(LeaveModel.requested_at.desc())
                .offset((page - 1) * page_size)
                .limit(min(page_size, MAX_PAGE_SIZE))
            )
        ).all()
        return await self._leave_dtos(rows), total

    async def _leave_or_404(self, leave_id: str) -> LeaveModel:
        leave = (
            await self.db.execute(
                select(LeaveModel)
                .where(LeaveModel.id == leave_id)
                .options(selectinload(LeaveModel.profile).selectinload(EmployeeProfileModel.user))
            )
        ).scalars().first()
        if not leave:
            raise NotFoundException("Leave request not found.")
        return leave

    async def cancel_leave(self, actor: UserModel, leave_id: str) -> dict:
        leave = await self._leave_or_404(leave_id)
        actor_profile = await self.db.get(EmployeeProfileModel, leave.employee_id)
        self_is_actor = bool(actor_profile and actor_profile.user_id == actor.id)
        reviewer = await self._is_leave_reviewer(actor, leave.employee_id)
        if not (self_is_actor or reviewer):
            raise ForbiddenException("You can only cancel your own request.")
        if leave.status == "CANCELLED":
            return await self._leave_dto(leave)  # idempotent
        if self_is_actor and not reviewer and leave.status != "PENDING":
            raise ConflictException("Only a pending request can be cancelled from your desk.")
        if leave.status == "REJECTED":
            raise ConflictException("A rejected request cannot be cancelled.")
        try:
            rules.validate_leave_transition(leave.status, "CANCELLED")
        except ValueError as exc:
            raise ConflictException(str(exc))

        leave.status = "CANCELLED"
        leave.reviewed_at = datetime.utcnow()
        leave.reviewed_by = actor.id
        await self._clear_derived_leave_attendance(leave)
        await self._audit("LEAVE_CANCELLED", actor.id, actor_profile.employee_code if actor_profile else None)
        await self.db.commit()
        return await self._leave_dto(leave)

    async def decide_leave(self, actor: UserModel, leave_id: str, req) -> dict:
        decision = (req.decision or "").upper()
        decision = {"APPROVE": "APPROVED", "REJECT": "REJECTED"}.get(decision, decision)
        if decision not in {"APPROVED", "REJECTED"}:
            raise BusinessLogicException("decision must be APPROVED or REJECTED.")
        note = req.reviewNote if req.reviewNote not in (None, "") else req.notes
        try:
            rules.validate_review_note(decision, note)
        except ValueError as exc:
            raise BusinessLogicException(str(exc))

        leave = await self._leave_or_404(leave_id)
        actor_profile_id = (
            await self.db.execute(
                select(EmployeeProfileModel.id).where(EmployeeProfileModel.user_id == actor.id)
            )
        ).scalars().first()
        if actor_profile_id and leave.employee_id == actor_profile_id:
            raise ForbiddenException("You cannot review your own leave request.")
        await self._require_leave_reviewer(actor, leave.employee_id)

        if rules.is_idempotent_decision(leave.status, decision):
            return await self._leave_dto(leave)
        try:
            rules.validate_leave_transition(leave.status, decision)
        except ValueError as exc:
            raise ConflictException(str(exc))

        leave.status = decision
        leave.reviewed_at = datetime.utcnow()
        leave.reviewed_by = actor.id
        leave.review_note = (note or "").strip() or leave.review_note
        if decision == "APPROVED":
            await self._derive_leave_attendance(leave)
        else:
            await self._clear_derived_leave_attendance(leave)

        profile = await self.db.get(EmployeeProfileModel, leave.employee_id)
        await self._audit(
            "LEAVE_APPROVED" if decision == "APPROVED" else "LEAVE_REJECTED",
            actor.id,
            profile.employee_code if profile else None,
            days=leave.days,
        )
        await self.db.commit()
        return await self._leave_dto(leave)

    async def _leave_dto(self, leave: LeaveModel) -> dict:
        profile = leave.profile or await self.db.get(EmployeeProfileModel, leave.employee_id)
        user = profile.user if profile else None
        rows = await self._leave_dtos([(leave, profile.employee_code if profile else "?", user.full_name if user else None)])
        return rows[0]

    # reviewer-or-self style checks delegate to the SAME RBAC surface — an
    # any-of over require_staff_permission (never a new permission engine).
    async def _is_leave_reviewer(self, actor: UserModel, profile_id: str) -> bool:
        for code in ("leave.approve", "leave.reject", "leave.manage"):
            try:
                await require_staff_permission(actor, self.db, code)
                return True
            except ForbiddenException:
                continue
        return False

    async def _require_leave_reviewer(self, actor: UserModel, profile_id: str) -> None:
        if not await self._is_leave_reviewer(actor, profile_id):
            await self._audit("ACCESS_DENIED", actor.id, None, attempt="leave.review")
            raise ForbiddenException("You are not allowed to review leave.")

    async def _derive_leave_attendance(self, leave: LeaveModel) -> None:
        """Approved leave materialises LEAVE attendance days (never touching a
        day that already has punches) — the frontend's applyLeaveToAttendance
        rule, executed where it cannot be bypassed."""
        day = leave.start_date
        while day <= leave.end_date:
            existing = (
                await self.db.execute(
                    select(AttendanceModel).where(
                        AttendanceModel.employee_id == leave.employee_id,
                        AttendanceModel.attendance_date == day,
                    )
                )
            ).scalars().first()
            if existing is None:
                self.db.add(
                    AttendanceModel(
                        employee_id=leave.employee_id,
                        attendance_date=day,
                        status="LEAVE",
                        notes=f"Derived from approved leave {leave.id}.",
                    )
                )
            day = date_t.fromordinal(day.toordinal() + 1)

    async def _clear_derived_leave_attendance(self, leave: LeaveModel) -> None:
        """Rejected/cancelled leave removes ONLY derived rows (no punches)."""
        rows = (
            await self.db.execute(
                select(AttendanceModel).where(
                    AttendanceModel.employee_id == leave.employee_id,
                    AttendanceModel.attendance_date >= leave.start_date,
                    AttendanceModel.attendance_date <= leave.end_date,
                    AttendanceModel.status == "LEAVE",
                    AttendanceModel.check_in.is_(None),
                )
            )
        ).scalars().all()
        for row in rows:
            await self.db.delete(row)

    # ------------------------------------------------------------------ #
    #  Performance                                                       #
    # ------------------------------------------------------------------ #

    async def list_performance(
        self,
        *,
        employee_user: Optional[UserModel] = None,
        employee_id: Optional[str] = None,
        period: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[dict], int]:
        stmt = (
            select(
                PerformanceModel,
                EmployeeProfileModel.employee_code,
                UserModel.full_name,
                ReviewerModel.full_name,
            )
            .join(EmployeeProfileModel, PerformanceModel.employee_id == EmployeeProfileModel.id)
            .join(UserModel, UserModel.id == EmployeeProfileModel.user_id)
            .outerjoin(ReviewerModel, ReviewerModel.id == PerformanceModel.reviewer_id)
        )
        if employee_user is not None:
            profile = await self._profile_for_user(employee_user)
            stmt = stmt.where(PerformanceModel.employee_id == profile.id)
        elif employee_id:
            stmt = stmt.where(EmployeeProfileModel.employee_code == employee_id)
        if period:
            stmt = stmt.where(PerformanceModel.review_period == period.upper())
        total = (
            await self.db.execute(
                select(func.count()).select_from(
                    stmt.with_only_columns(PerformanceModel.id).order_by(None).subquery()
                )
            )
        ).scalar() or 0
        rows = (
            await self.db.execute(
                stmt.order_by(PerformanceModel.review_date.desc())
                .offset((page - 1) * page_size)
                .limit(min(page_size, MAX_PAGE_SIZE))
            )
        ).all()
        items = [
            {
                "performanceId": perf.id,
                "employeeId": code,
                "employeeName": name,
                "reviewerName": reviewer_name,
                "reviewDate": perf.review_date,
                "rating": perf.rating,
                "reviewPeriod": perf.review_period,
                "reviewerId": perf.reviewer_id,
                "comments": perf.comments,
                "createdAt": perf.created_at,
            }
            for perf, code, name, reviewer_name in rows
        ]
        return items, total

    async def create_performance(self, actor: UserModel, req) -> dict:
        try:
            rating = rules.validate_rating(req.rating)
        except ValueError as exc:
            raise BusinessLogicException(str(exc))
        period = (req.reviewPeriod or "MONTHLY").upper()
        if period not in rules.REVIEW_PERIODS:
            raise BusinessLogicException(
                f"Unknown review period '{req.reviewPeriod}'. Expected: {', '.join(sorted(rules.REVIEW_PERIODS))}."
            )
        target_user = await self._resolve_employee(req.employeeId)
        profile = await self._profile_for_user(target_user)
        review_date = req.reviewDate or rules.day_of(rules.store_now())

        record = PerformanceModel(
            employee_id=profile.id,
            review_date=review_date,
            rating=rating,
            review_period=period,
            reviewer_id=actor.id,
            comments=(req.comments or "").strip() or None,
        )
        self.db.add(record)
        await self._audit(
            "PERFORMANCE_REVIEW_RECORDED", actor.id, profile.employee_code,
            period=period, rating=rating,
        )
        await self.db.commit()
        await self.db.refresh(record)
        return {
            "performanceId": record.id,
            "employeeId": profile.employee_code,
            "employeeName": target_user.full_name,
            "reviewerName": actor.full_name,
            "reviewDate": record.review_date,
            "rating": record.rating,
            "reviewPeriod": record.review_period,
            "reviewerId": record.reviewer_id,
            "comments": record.comments,
            "createdAt": record.created_at,
        }

    async def update_performance(self, actor: UserModel, performance_id: str, req) -> dict:
        record = (
            await self.db.execute(
                select(PerformanceModel)
                .where(PerformanceModel.id == performance_id)
                .options(selectinload(PerformanceModel.employee).selectinload(EmployeeProfileModel.user))
            )
        ).scalars().first()
        if not record:
            raise NotFoundException("Performance review not found.")
        if req.rating is not None:
            try:
                record.rating = rules.validate_rating(req.rating)
            except ValueError as exc:
                raise BusinessLogicException(str(exc))
        if req.reviewPeriod is not None:
            period = req.reviewPeriod.upper()
            if period not in rules.REVIEW_PERIODS:
                raise BusinessLogicException(f"Unknown review period '{req.reviewPeriod}'.")
            record.review_period = period
        if req.comments is not None:
            record.comments = req.comments.strip() or None
        if req.reviewDate is not None:
            record.review_date = req.reviewDate
        record.reviewer_id = actor.id
        profile = record.employee
        await self._audit(
            "PERFORMANCE_REVIEW_UPDATED", actor.id,
            profile.employee_code if profile else None,
            rating=record.rating,
        )
        await self.db.commit()
        await self.db.refresh(record)
        user = profile.user if profile else None
        return {
            "performanceId": record.id,
            "employeeId": profile.employee_code if profile else "?",
            "employeeName": user.full_name if user else None,
            "reviewerName": actor.full_name,
            "reviewDate": record.review_date,
            "rating": record.rating,
            "reviewPeriod": record.review_period,
            "reviewerId": record.reviewer_id,
            "comments": record.comments,
            "createdAt": record.created_at,
        }

    # ------------------------------------------------------------------ #
    #  Team roster (bounded) for admin-side day views                    #
    # ------------------------------------------------------------------ #

    async def day_roster(self, day: date_t, settings: Dict[str, Any]) -> List[dict]:
        rows = (
            await self.db.execute(
                self._base_select(AttendanceModel)
                .where(AttendanceModel.attendance_date == day)
                .order_by(EmployeeProfileModel.employee_code.asc())
                .limit(MAX_PAGE_SIZE * 4)
            )
        ).all()
        return await self._attendance_dtos(rows)

    # ------------------------------------------------------------------ #

    async def _audit(self, action: str, actor_id: Optional[str], target_code: Optional[str], **details) -> None:
        try:
            from app.services.audit.audit_service import AuditService, format_summary

            summary = format_summary(details)
            await AuditService(self.db).record(
                action=action,
                actor_id=actor_id,
                target_employee_id=target_code,
                summary=f"{action} · {summary}" if summary else action,
            )
        except Exception:  # noqa: BLE001 — an audit issue never breaks the punch
            logger.exception("workforce audit write failed for %s", action)
