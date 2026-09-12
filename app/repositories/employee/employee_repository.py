from typing import List, Optional, Tuple
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.auth.user import UserModel
from app.models.employee.employee import EmployeeProfileModel
from app.models.employee.department import DepartmentModel
from app.models.employee.section import SectionModel
from app.models.employee.attendance import AttendanceModel
from app.models.employee.performance import PerformanceModel
from app.models.employee.target import TargetModel
from app.repositories.base import BaseRepository


class EmployeeRepository(BaseRepository[UserModel]):
    """Data-access layer for employee operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(UserModel, session)

    # ------------------------------------------------------------------ #
    #  Employee CRUD                                                       #
    # ------------------------------------------------------------------ #

    async def get_employee_by_id(self, user_id: str) -> Optional[UserModel]:
        """Resolve by users.id, falling back to the PF employee code.

        The Admin detail screens carry the human code in the URL
        (`/admin/employees/PF-SLS-00001`) and the workforce endpoints are
        addressed the same way; both identifiers must resolve to the same
        row. Exact-match only — never a prefix/ILIKE.
        """
        stmt = (
            select(UserModel)
            .outerjoin(EmployeeProfileModel, EmployeeProfileModel.user_id == UserModel.id)
            .where(
                UserModel.user_type == "employee",
                or_(UserModel.id == user_id, EmployeeProfileModel.employee_code == user_id),
            )
            .options(selectinload(UserModel.employee_profile))
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def get_any_staff_by_id(self, user_id: str) -> Optional[UserModel]:
        """
        Staff-scoped load for the account-management API: employee-domain AND
        admin-domain accounts (never customers). Resolves ``users.id`` or the
        PF employee code the Admin directory uses in URLs. The account level
        stored on the row decides what the caller may do with it (app.core.rbac).
        """
        stmt = (
            select(UserModel)
            .outerjoin(EmployeeProfileModel, EmployeeProfileModel.user_id == UserModel.id)
            .where(
                UserModel.user_type.in_(["employee", "admin"]),
                or_(UserModel.id == user_id, EmployeeProfileModel.employee_code == user_id),
            )
            .options(selectinload(UserModel.employee_profile))
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def get_employee_by_code(self, code: str) -> Optional[UserModel]:
        stmt = (
            select(UserModel)
            .join(EmployeeProfileModel, EmployeeProfileModel.user_id == UserModel.id)
            .where(EmployeeProfileModel.employee_code == code)
            .options(selectinload(UserModel.employee_profile))
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def email_exists(self, email: str) -> bool:
        res = await self.session.execute(select(UserModel).where(UserModel.email == email))
        return res.scalars().first() is not None

    async def phone_exists(self, phone: str) -> bool:
        res = await self.session.execute(select(UserModel).where(UserModel.phone == phone))
        return res.scalars().first() is not None

    async def employee_code_exists(self, code: str) -> bool:
        res = await self.session.execute(
            select(EmployeeProfileModel).where(EmployeeProfileModel.employee_code == code)
        )
        return res.scalars().first() is not None

    async def list_employees(
        self,
        skip: int = 0,
        limit: int = 50,
        search: Optional[str] = None,
        status: Optional[str] = None,
        department_id: Optional[str] = None,
        include_admins: bool = False,
        exclude_super_admins: bool = False,
    ) -> Tuple[List[UserModel], int]:
        # Employee-domain by default; the account-management screen passes
        # include_admins=True to see the whole staff roster (SUPER_ADMIN and
        # ADMIN accounts included) in one canonical list — no second API.
        user_types = ["employee", "admin"] if include_admins else ["employee"]
        base_query = (
            select(UserModel)
            .where(UserModel.user_type.in_(user_types))
            .options(selectinload(UserModel.employee_profile))
        )

        # Rosters for anyone below SUPER_ADMIN never enumerate system-owner
        # accounts: the ceiling that blocks managing them also hides them.
        if exclude_super_admins:
            from app.models.rbac.role import RoleModel
            from app.models.rbac.user_role import UserRoleModel

            super_admin_ids = (
                select(UserRoleModel.user_id)
                .join(RoleModel, RoleModel.id == UserRoleModel.role_id)
                .where(RoleModel.name == "SUPER_ADMIN")
                .scalar_subquery()
            )
            base_query = base_query.where(
                ~(
                    (UserModel.account_level == "SUPER_ADMIN")
                    | (UserModel.account_level.is_(None) & UserModel.id.in_(super_admin_ids))
                )
            )

        if status:
            from app.core.rbac import status_filter_values

            values = status_filter_values(status)
            if values:
                base_query = base_query.where(UserModel.status.in_(values))

        if search:
            like = f"%{search}%"
            base_query = base_query.outerjoin(
                EmployeeProfileModel, EmployeeProfileModel.user_id == UserModel.id
            ).where(
                or_(
                    UserModel.full_name.ilike(like),
                    UserModel.email.ilike(like),
                    EmployeeProfileModel.employee_code.ilike(like),
                    EmployeeProfileModel.designation.ilike(like),
                )
            )
        elif department_id:
            base_query = base_query.join(
                EmployeeProfileModel, EmployeeProfileModel.user_id == UserModel.id
            ).where(EmployeeProfileModel.department_id == department_id)

        count_stmt = select(func.count()).select_from(base_query.subquery())
        total_res = await self.session.execute(count_stmt)
        total = total_res.scalar_one()

        data_stmt = base_query.order_by(UserModel.created_at.desc()).offset(skip).limit(limit)
        data_res = await self.session.execute(data_stmt)
        items = list(data_res.scalars().unique().all())

        return items, total

    # ------------------------------------------------------------------ #
    #  Department / Section                                                #
    # ------------------------------------------------------------------ #

    async def get_department(self, dept_id: str) -> Optional[DepartmentModel]:
        res = await self.session.execute(
            select(DepartmentModel).where(DepartmentModel.id == dept_id)
        )
        return res.scalars().first()

    async def get_department_by_name(self, name: str) -> Optional[DepartmentModel]:
        res = await self.session.execute(
            select(DepartmentModel).where(DepartmentModel.name == name)
        )
        return res.scalars().first()

    async def list_departments(self) -> List[DepartmentModel]:
        res = await self.session.execute(
            select(DepartmentModel).order_by(DepartmentModel.name)
        )
        return list(res.scalars().all())

    async def get_section(self, section_id: str) -> Optional[SectionModel]:
        res = await self.session.execute(
            select(SectionModel).where(SectionModel.id == section_id)
        )
        return res.scalars().first()

    async def list_sections(self, department_id: Optional[str] = None) -> List[SectionModel]:
        stmt = select(SectionModel)
        if department_id:
            stmt = stmt.where(SectionModel.department_id == department_id)
        res = await self.session.execute(stmt.order_by(SectionModel.name))
        return list(res.scalars().all())

    # ------------------------------------------------------------------ #
    #  Attendance                                                          #
    # ------------------------------------------------------------------ #

    async def get_attendance(self, attendance_id: str) -> Optional[AttendanceModel]:
        res = await self.session.execute(
            select(AttendanceModel).where(AttendanceModel.id == attendance_id)
        )
        return res.scalars().first()

    async def list_attendance(
        self, employee_id: str, skip: int = 0, limit: int = 100
    ) -> Tuple[List[AttendanceModel], int]:
        count_stmt = select(func.count()).where(AttendanceModel.employee_id == employee_id)
        total = (await self.session.execute(count_stmt)).scalar_one()

        data_stmt = (
            select(AttendanceModel)
            .where(AttendanceModel.employee_id == employee_id)
            .order_by(AttendanceModel.attendance_date.desc())
            .offset(skip)
            .limit(limit)
        )
        items = list((await self.session.execute(data_stmt)).scalars().all())
        return items, total

    # ------------------------------------------------------------------ #
    #  Targets                                                             #
    # ------------------------------------------------------------------ #

    async def get_target(self, target_id: str) -> Optional[TargetModel]:
        res = await self.session.execute(
            select(TargetModel).where(TargetModel.id == target_id)
        )
        return res.scalars().first()

    async def list_targets(
        self, employee_id: str, skip: int = 0, limit: int = 50
    ) -> Tuple[List[TargetModel], int]:
        count_stmt = select(func.count()).where(TargetModel.employee_id == employee_id)
        total = (await self.session.execute(count_stmt)).scalar_one()

        data_stmt = (
            select(TargetModel)
            .where(TargetModel.employee_id == employee_id)
            .order_by(TargetModel.period_start.desc())
            .offset(skip)
            .limit(limit)
        )
        items = list((await self.session.execute(data_stmt)).scalars().all())
        return items, total

    # ------------------------------------------------------------------ #
    #  Performance                                                         #
    # ------------------------------------------------------------------ #

    async def get_performance(self, perf_id: str) -> Optional[PerformanceModel]:
        res = await self.session.execute(
            select(PerformanceModel).where(PerformanceModel.id == perf_id)
        )
        return res.scalars().first()

    async def list_performance(
        self, employee_id: str, skip: int = 0, limit: int = 50
    ) -> Tuple[List[PerformanceModel], int]:
        count_stmt = select(func.count()).where(PerformanceModel.employee_id == employee_id)
        total = (await self.session.execute(count_stmt)).scalar_one()

        data_stmt = (
            select(PerformanceModel)
            .where(PerformanceModel.employee_id == employee_id)
            .order_by(PerformanceModel.review_date.desc())
            .offset(skip)
            .limit(limit)
        )
        items = list((await self.session.execute(data_stmt)).scalars().all())
        return items, total
