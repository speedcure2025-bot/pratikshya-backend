from typing import List, Optional, Tuple
import random
import secrets
import string

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    BusinessLogicException,
)
from app.core.security import hash_password
from app.models.auth.user import UserModel
from app.models.employee.employee import EmployeeProfileModel
from app.models.employee.department import DepartmentModel
from app.models.employee.section import SectionModel
from app.models.employee.attendance import AttendanceModel
from app.models.employee.performance import PerformanceModel
from app.models.employee.target import TargetModel
from app.models.rbac.role import RoleModel
from app.models.rbac.user_role import UserRoleModel
from app.repositories.employee.employee_repository import EmployeeRepository
from app.schemas.employee.employee import (
    EmployeeCreateRequest,
    EmployeeUpdateRequest,
    EmployeeStatusRequest,
    ResetEmployeePasswordRequest,
    EmployeePermissionsRequest,
)
from app.schemas.employee.attendance import AttendanceCreateRequest, AttendanceUpdateRequest
from app.schemas.employee.performance import (
    PerformanceCreateRequest,
    PerformanceUpdateRequest,
    TargetCreateRequest,
    TargetUpdateRequest,
)
from app.schemas.employee.department import (
    DepartmentCreateRequest,
    DepartmentUpdateRequest,
    SectionCreateRequest,
    SectionUpdateRequest,
)

# ---------------------------------------------------------------------------
# Role → employee-code prefix mapping (from spec AUTHORIZATION_MATRIX.md)
# ---------------------------------------------------------------------------
ROLE_CODE_PREFIX: dict[str, str] = {
    "SUPER_ADMIN":       "ADM",
    "STORE_MANAGER":     "MGR",
    "SALES_EXECUTIVE":   "SLS",
    "INVENTORY_MANAGER": "INV",
    "INVENTORY_STAFF":   "INV",
    "WAREHOUSE_STAFF":   "WHS",
    "CUSTOMER_SUPPORT":  "CS",
    "FASHION_STYLIST":   "STY",
}

# Employee statuses that permit login (from AUTHORIZATION_MATRIX.md §1)
LOGINABLE_STATUSES = {"ACTIVE", "PENDING", "ON_LEAVE"}


def _generate_temp_password(length: int = 12) -> str:
    """Generate a secure random temporary password."""
    alphabet = string.ascii_letters + string.digits + "!@#$"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def _next_employee_code(session: AsyncSession, role: Optional[str]) -> str:
    """
    Generate a unique employee code in format PF-<PREFIX>-#####.
    Prefix derived from the role per ROLE_CODE_PREFIX; falls back to 'EMP'.
    """
    prefix = ROLE_CODE_PREFIX.get((role or "").upper(), "EMP")
    for _ in range(20):  # retry up to 20 times on collision
        number = random.randint(10000, 99999)
        code = f"PF-{prefix}-{number:05d}"
        res = await session.execute(
            select(EmployeeProfileModel).where(EmployeeProfileModel.employee_code == code)
        )
        if res.scalars().first() is None:
            return code
    raise BusinessLogicException("Could not generate a unique employee ID.")


class EmployeeService:
    """Business logic for employee management (admin-only operations)."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = EmployeeRepository(db)

    # ------------------------------------------------------------------ #
    #  Employee CRUD                                                       #
    # ------------------------------------------------------------------ #

    async def create_employee(
        self, req: EmployeeCreateRequest, creator_id: str
    ) -> UserModel:
        # Validate required fields per spec
        if not req.full_name:
            raise BusinessLogicException("First name is required.")

        # Uniqueness checks
        if req.email and await self.repo.email_exists(req.email):
            raise ConflictException("An employee with this email already exists.")
        if req.phone and await self.repo.phone_exists(req.phone):
            raise ConflictException("An account with this phone number already exists.")

        # Resolve / auto-generate employee code
        if req.employee_code:
            if await self.repo.employee_code_exists(req.employee_code):
                raise ConflictException(f"Employee code '{req.employee_code}' is already in use.")
            employee_code = req.employee_code
        else:
            employee_code = await _next_employee_code(self.db, req.role)

        # Auto-generate password if not supplied
        raw_password = req.password if req.password else _generate_temp_password()

        # Validate department/section if provided
        if req.department_id:
            dept = await self.repo.get_department(req.department_id)
            if not dept:
                raise NotFoundException("Department not found.")
        if req.section_id:
            section = await self.repo.get_section(req.section_id)
            if not section:
                raise NotFoundException("Section not found.")

        user = UserModel(
            email=req.email,
            phone=req.phone,
            full_name=req.full_name,
            hashed_password=hash_password(raw_password),
            user_type="employee",
            status="ACTIVE",
            is_verified=True,
            force_password_change=True,   # must change on first login per spec
        )
        self.db.add(user)
        await self.db.flush()

        profile = EmployeeProfileModel(
            user_id=user.id,
            employee_code=employee_code,
            designation=req.designation or (req.role or "Staff"),
            department=req.department,
            department_id=req.department_id,
            section_id=req.section_id,
        )
        self.db.add(profile)

        # Assign the specified role if it exists; fall back to generic EMPLOYEE role
        role_name = (req.role or "").upper()
        role_to_assign = None
        if role_name:
            role_res = await self.db.execute(
                select(RoleModel).where(RoleModel.name == role_name)
            )
            role_to_assign = role_res.scalars().first()
        if not role_to_assign:
            role_res = await self.db.execute(
                select(RoleModel).where(RoleModel.name == "EMPLOYEE")
            )
            role_to_assign = role_res.scalars().first()
        if role_to_assign:
            self.db.add(UserRoleModel(user_id=user.id, role_id=role_to_assign.id))

        await self.db.commit()
        await self.db.refresh(user)
        return await self.repo.get_employee_by_id(user.id)

    async def get_employee(self, user_id: str) -> UserModel:
        user = await self.repo.get_employee_by_id(user_id)
        if not user:
            raise NotFoundException("Employee not found.")
        return user

    async def list_employees(
        self,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        status: Optional[str] = None,
        department_id: Optional[str] = None,
    ) -> Tuple[List[UserModel], int]:
        skip = (page - 1) * page_size
        return await self.repo.list_employees(
            skip=skip,
            limit=page_size,
            search=search,
            status=status,
            department_id=department_id,
        )

    async def update_employee(self, user_id: str, req: EmployeeUpdateRequest) -> UserModel:
        user = await self.repo.get_employee_by_id(user_id)
        if not user:
            raise NotFoundException("Employee not found.")

        if req.full_name is not None:
            user.full_name = req.full_name
        if req.phone is not None:
            if req.phone != user.phone and await self.repo.phone_exists(req.phone):
                raise ConflictException("This phone number is already in use.")
            user.phone = req.phone

        profile = user.employee_profile
        if profile:
            if req.designation is not None:
                profile.designation = req.designation
            if req.department is not None:
                profile.department = req.department
            if req.department_id is not None:
                dept = await self.repo.get_department(req.department_id)
                if not dept:
                    raise NotFoundException("Department not found.")
                profile.department_id = req.department_id
            if req.section_id is not None:
                section = await self.repo.get_section(req.section_id)
                if not section:
                    raise NotFoundException("Section not found.")
                profile.section_id = req.section_id

        await self.db.commit()
        await self.db.refresh(user)
        return await self.repo.get_employee_by_id(user_id)

    async def update_employee_status(self, user_id: str, req: EmployeeStatusRequest) -> UserModel:
        """
        Valid statuses per AUTHORIZATION_MATRIX.md §1:
        ACTIVE | PENDING | ON_LEAVE | SUSPENDED | INACTIVE
        SUSPENDED and INACTIVE immediately deny all permissions.
        """
        allowed = {"ACTIVE", "PENDING", "ON_LEAVE", "SUSPENDED", "INACTIVE", "DEACTIVATED"}
        if req.status not in allowed:
            raise BusinessLogicException(
                f"Invalid status. Must be one of: {', '.join(sorted(allowed))}"
            )

        user = await self.repo.get_employee_by_id(user_id)
        if not user:
            raise NotFoundException("Employee not found.")

        user.status = req.status
        await self.db.commit()
        await self.db.refresh(user)
        return await self.repo.get_employee_by_id(user_id)

    async def reset_employee_password(
        self, user_id: str, req: ResetEmployeePasswordRequest
    ) -> bool:
        user = await self.repo.get_employee_by_id(user_id)
        if not user:
            raise NotFoundException("Employee not found.")

        # If no password supplied, generate a temp one
        new_pw = req.new_password or _generate_temp_password()
        user.hashed_password = hash_password(new_pw)
        user.force_password_change = req.force_change
        await self.db.commit()
        return True

    async def update_employee_permissions(
        self, user_id: str, req: EmployeePermissionsRequest
    ) -> UserModel:
        """
        PUT /admin/employees/{id}/permissions
        Sets permissionMode and optionally a custom permission list.
        SUPER_ADMIN always resolves to full access regardless of stored overrides.
        """
        user = await self.repo.get_employee_by_id(user_id)
        if not user:
            raise NotFoundException("Employee not found.")

        if req.permissionMode not in ("role", "custom"):
            raise BusinessLogicException("permissionMode must be 'role' or 'custom'.")

        # TODO: persist permissionMode and custom permissions once those columns
        # are added to EmployeeProfileModel. For now store on profile notes/metadata.
        # This is a placeholder that commits the intent.
        await self.db.commit()
        return user

    async def delete_employee(self, user_id: str) -> bool:
        user = await self.repo.get_employee_by_id(user_id)
        if not user:
            raise NotFoundException("Employee not found.")
        await self.db.delete(user)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Department                                                          #
    # ------------------------------------------------------------------ #

    async def create_department(self, req: DepartmentCreateRequest) -> DepartmentModel:
        existing = await self.repo.get_department_by_name(req.name)
        if existing:
            raise ConflictException(f"Department '{req.name}' already exists.")
        dept = DepartmentModel(name=req.name, description=req.description)
        self.db.add(dept)
        await self.db.commit()
        await self.db.refresh(dept)
        return dept

    async def list_departments(self) -> List[DepartmentModel]:
        return await self.repo.list_departments()

    async def get_department(self, dept_id: str) -> DepartmentModel:
        dept = await self.repo.get_department(dept_id)
        if not dept:
            raise NotFoundException("Department not found.")
        return dept

    async def update_department(self, dept_id: str, req: DepartmentUpdateRequest) -> DepartmentModel:
        dept = await self.repo.get_department(dept_id)
        if not dept:
            raise NotFoundException("Department not found.")
        if req.name is not None:
            dept.name = req.name
        if req.description is not None:
            dept.description = req.description
        if req.is_active is not None:
            dept.is_active = req.is_active
        await self.db.commit()
        await self.db.refresh(dept)
        return dept

    async def delete_department(self, dept_id: str) -> bool:
        dept = await self.repo.get_department(dept_id)
        if not dept:
            raise NotFoundException("Department not found.")
        await self.db.delete(dept)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Section                                                             #
    # ------------------------------------------------------------------ #

    async def create_section(self, req: SectionCreateRequest) -> SectionModel:
        dept = await self.repo.get_department(req.department_id)
        if not dept:
            raise NotFoundException("Department not found.")
        section = SectionModel(
            department_id=req.department_id, name=req.name, description=req.description
        )
        self.db.add(section)
        await self.db.commit()
        await self.db.refresh(section)
        return section

    async def list_sections(self, department_id: Optional[str] = None) -> List[SectionModel]:
        return await self.repo.list_sections(department_id)

    async def get_section(self, section_id: str) -> SectionModel:
        section = await self.repo.get_section(section_id)
        if not section:
            raise NotFoundException("Section not found.")
        return section

    async def update_section(self, section_id: str, req) -> SectionModel:
        section = await self.repo.get_section(section_id)
        if not section:
            raise NotFoundException("Section not found.")
        if req.name is not None:
            section.name = req.name
        if req.description is not None:
            section.description = req.description
        if req.is_active is not None:
            section.is_active = req.is_active
        await self.db.commit()
        await self.db.refresh(section)
        return section

    async def delete_section(self, section_id: str) -> bool:
        section = await self.repo.get_section(section_id)
        if not section:
            raise NotFoundException("Section not found.")
        await self.db.delete(section)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Attendance                                                          #
    # ------------------------------------------------------------------ #

    async def create_attendance(self, req: AttendanceCreateRequest) -> AttendanceModel:
        emp_profile = await self._get_profile(req.employee_id)
        record = AttendanceModel(
            employee_id=emp_profile.id,
            attendance_date=req.attendance_date,
            check_in=req.check_in,
            check_out=req.check_out,
            status=req.status,
            notes=req.notes,
        )
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def list_attendance(
        self, employee_id: str, page: int = 1, page_size: int = 30
    ) -> Tuple[List[AttendanceModel], int]:
        profile = await self._get_profile(employee_id)
        skip = (page - 1) * page_size
        return await self.repo.list_attendance(profile.id, skip, page_size)

    async def update_attendance(
        self, attendance_id: str, req: AttendanceUpdateRequest
    ) -> AttendanceModel:
        record = await self.repo.get_attendance(attendance_id)
        if not record:
            raise NotFoundException("Attendance record not found.")
        if req.check_in is not None:
            record.check_in = req.check_in
        if req.check_out is not None:
            record.check_out = req.check_out
        if req.status is not None:
            record.status = req.status
        if req.notes is not None:
            record.notes = req.notes
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def delete_attendance(self, attendance_id: str) -> bool:
        record = await self.repo.get_attendance(attendance_id)
        if not record:
            raise NotFoundException("Attendance record not found.")
        await self.db.delete(record)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Targets                                                             #
    # ------------------------------------------------------------------ #

    async def create_target(self, req: TargetCreateRequest) -> TargetModel:
        profile = await self._get_profile(req.employee_id)
        if req.period_end <= req.period_start:
            raise BusinessLogicException("period_end must be after period_start.")
        target = TargetModel(
            employee_id=profile.id,
            period_start=req.period_start,
            period_end=req.period_end,
            target_amount=req.target_amount,
            target_type=req.target_type,
            notes=req.notes,
        )
        self.db.add(target)
        await self.db.commit()
        await self.db.refresh(target)
        return target

    async def list_targets(
        self, employee_id: str, page: int = 1, page_size: int = 20
    ) -> Tuple[List[TargetModel], int]:
        profile = await self._get_profile(employee_id)
        skip = (page - 1) * page_size
        return await self.repo.list_targets(profile.id, skip, page_size)

    async def update_target(self, target_id: str, req: TargetUpdateRequest) -> TargetModel:
        target = await self.repo.get_target(target_id)
        if not target:
            raise NotFoundException("Target not found.")
        if req.target_amount is not None:
            target.target_amount = req.target_amount
        if req.achieved_amount is not None:
            target.achieved_amount = req.achieved_amount
        if req.target_type is not None:
            target.target_type = req.target_type
        if req.notes is not None:
            target.notes = req.notes
        await self.db.commit()
        await self.db.refresh(target)
        return target

    async def delete_target(self, target_id: str) -> bool:
        target = await self.repo.get_target(target_id)
        if not target:
            raise NotFoundException("Target not found.")
        await self.db.delete(target)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Performance                                                         #
    # ------------------------------------------------------------------ #

    async def create_performance(
        self, req: PerformanceCreateRequest, reviewer_id: str
    ) -> PerformanceModel:
        profile = await self._get_profile(req.employee_id)
        review = PerformanceModel(
            employee_id=profile.id,
            review_date=req.review_date,
            rating=req.rating,
            review_period=req.review_period,
            reviewer_id=reviewer_id,
            comments=req.comments,
        )
        self.db.add(review)
        await self.db.commit()
        await self.db.refresh(review)
        return review

    async def list_performance(
        self, employee_id: str, page: int = 1, page_size: int = 20
    ) -> Tuple[List[PerformanceModel], int]:
        profile = await self._get_profile(employee_id)
        skip = (page - 1) * page_size
        return await self.repo.list_performance(profile.id, skip, page_size)

    async def update_performance(
        self, perf_id: str, req: PerformanceUpdateRequest
    ) -> PerformanceModel:
        review = await self.repo.get_performance(perf_id)
        if not review:
            raise NotFoundException("Performance review not found.")
        if req.rating is not None:
            review.rating = req.rating
        if req.review_period is not None:
            review.review_period = req.review_period
        if req.comments is not None:
            review.comments = req.comments
        await self.db.commit()
        await self.db.refresh(review)
        return review

    async def delete_performance(self, perf_id: str) -> bool:
        review = await self.repo.get_performance(perf_id)
        if not review:
            raise NotFoundException("Performance review not found.")
        await self.db.delete(review)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    async def _get_profile(self, employee_user_id: str) -> EmployeeProfileModel:
        """Resolve employee user_id → EmployeeProfileModel, raising 404 if not found."""
        user = await self.repo.get_employee_by_id(employee_user_id)
        if not user or not user.employee_profile:
            raise NotFoundException("Employee not found.")
        return user.employee_profile


class EmployeeService:
    """Business logic for employee management (admin-only operations)."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = EmployeeRepository(db)

    # ------------------------------------------------------------------ #
    #  Employee CRUD                                                       #
    # ------------------------------------------------------------------ #

    async def create_employee(
        self, req: EmployeeCreateRequest, creator_id: str
    ) -> UserModel:
        # Uniqueness checks
        if req.email and await self.repo.email_exists(req.email):
            raise ConflictException("An account with this email already exists.")
        if req.phone and await self.repo.phone_exists(req.phone):
            raise ConflictException("An account with this phone number already exists.")
        if await self.repo.employee_code_exists(req.employee_code):
            raise ConflictException(f"Employee code '{req.employee_code}' is already in use.")

        # Validate department/section if provided
        if req.department_id:
            dept = await self.repo.get_department(req.department_id)
            if not dept:
                raise NotFoundException("Department not found.")
        if req.section_id:
            section = await self.repo.get_section(req.section_id)
            if not section:
                raise NotFoundException("Section not found.")

        user = UserModel(
            email=req.email,
            phone=req.phone,
            full_name=req.full_name,
            hashed_password=hash_password(req.password),
            user_type="employee",
            status="ACTIVE",
            is_verified=True,
            force_password_change=True,   # employee must change password on first login
        )
        self.db.add(user)
        await self.db.flush()

        profile = EmployeeProfileModel(
            user_id=user.id,
            employee_code=req.employee_code,
            designation=req.designation,
            department=req.department,
            department_id=req.department_id,
            section_id=req.section_id,
        )
        self.db.add(profile)

        # Assign EMPLOYEE role if it exists
        from sqlalchemy import select
        role_res = await self.db.execute(select(RoleModel).where(RoleModel.name == "EMPLOYEE"))
        emp_role = role_res.scalars().first()
        if emp_role:
            self.db.add(UserRoleModel(user_id=user.id, role_id=emp_role.id))

        await self.db.commit()
        await self.db.refresh(user)
        return await self.repo.get_employee_by_id(user.id)

    async def get_employee(self, user_id: str) -> UserModel:
        user = await self.repo.get_employee_by_id(user_id)
        if not user:
            raise NotFoundException("Employee not found.")
        return user

    async def list_employees(
        self,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        status: Optional[str] = None,
        department_id: Optional[str] = None,
    ) -> Tuple[List[UserModel], int]:
        skip = (page - 1) * page_size
        return await self.repo.list_employees(
            skip=skip,
            limit=page_size,
            search=search,
            status=status,
            department_id=department_id,
        )

    async def update_employee(self, user_id: str, req: EmployeeUpdateRequest) -> UserModel:
        user = await self.repo.get_employee_by_id(user_id)
        if not user:
            raise NotFoundException("Employee not found.")

        if req.full_name is not None:
            user.full_name = req.full_name
        if req.phone is not None:
            if req.phone != user.phone and await self.repo.phone_exists(req.phone):
                raise ConflictException("This phone number is already in use.")
            user.phone = req.phone

        profile = user.employee_profile
        if profile:
            if req.designation is not None:
                profile.designation = req.designation
            if req.department is not None:
                profile.department = req.department
            if req.department_id is not None:
                dept = await self.repo.get_department(req.department_id)
                if not dept:
                    raise NotFoundException("Department not found.")
                profile.department_id = req.department_id
            if req.section_id is not None:
                section = await self.repo.get_section(req.section_id)
                if not section:
                    raise NotFoundException("Section not found.")
                profile.section_id = req.section_id

        await self.db.commit()
        await self.db.refresh(user)
        return await self.repo.get_employee_by_id(user_id)

    async def update_employee_status(self, user_id: str, req: EmployeeStatusRequest) -> UserModel:
        allowed = {"ACTIVE", "SUSPENDED", "DEACTIVATED"}
        if req.status not in allowed:
            raise BusinessLogicException(f"Invalid status. Must be one of: {', '.join(allowed)}")

        user = await self.repo.get_employee_by_id(user_id)
        if not user:
            raise NotFoundException("Employee not found.")

        user.status = req.status
        await self.db.commit()
        await self.db.refresh(user)
        return await self.repo.get_employee_by_id(user_id)

    async def reset_employee_password(
        self, user_id: str, req: ResetEmployeePasswordRequest
    ) -> bool:
        user = await self.repo.get_employee_by_id(user_id)
        if not user:
            raise NotFoundException("Employee not found.")

        user.hashed_password = hash_password(req.new_password)
        user.force_password_change = req.force_change
        await self.db.commit()
        return True

    async def delete_employee(self, user_id: str) -> bool:
        user = await self.repo.get_employee_by_id(user_id)
        if not user:
            raise NotFoundException("Employee not found.")
        await self.db.delete(user)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Department                                                          #
    # ------------------------------------------------------------------ #

    async def create_department(self, req: DepartmentCreateRequest) -> DepartmentModel:
        existing = await self.repo.get_department_by_name(req.name)
        if existing:
            raise ConflictException(f"Department '{req.name}' already exists.")
        dept = DepartmentModel(name=req.name, description=req.description)
        self.db.add(dept)
        await self.db.commit()
        await self.db.refresh(dept)
        return dept

    async def list_departments(self) -> List[DepartmentModel]:
        return await self.repo.list_departments()

    async def get_department(self, dept_id: str) -> DepartmentModel:
        dept = await self.repo.get_department(dept_id)
        if not dept:
            raise NotFoundException("Department not found.")
        return dept

    async def update_department(self, dept_id: str, req: DepartmentUpdateRequest) -> DepartmentModel:
        dept = await self.repo.get_department(dept_id)
        if not dept:
            raise NotFoundException("Department not found.")
        if req.name is not None:
            dept.name = req.name
        if req.description is not None:
            dept.description = req.description
        if req.is_active is not None:
            dept.is_active = req.is_active
        await self.db.commit()
        await self.db.refresh(dept)
        return dept

    async def delete_department(self, dept_id: str) -> bool:
        dept = await self.repo.get_department(dept_id)
        if not dept:
            raise NotFoundException("Department not found.")
        await self.db.delete(dept)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Section                                                             #
    # ------------------------------------------------------------------ #

    async def create_section(self, req: SectionCreateRequest) -> SectionModel:
        dept = await self.repo.get_department(req.department_id)
        if not dept:
            raise NotFoundException("Department not found.")
        section = SectionModel(
            department_id=req.department_id, name=req.name, description=req.description
        )
        self.db.add(section)
        await self.db.commit()
        await self.db.refresh(section)
        return section

    async def list_sections(self, department_id: Optional[str] = None) -> List[SectionModel]:
        return await self.repo.list_sections(department_id)

    async def get_section(self, section_id: str) -> SectionModel:
        section = await self.repo.get_section(section_id)
        if not section:
            raise NotFoundException("Section not found.")
        return section

    async def update_section(self, section_id: str, req: SectionUpdateRequest) -> SectionModel:
        section = await self.repo.get_section(section_id)
        if not section:
            raise NotFoundException("Section not found.")
        if req.name is not None:
            section.name = req.name
        if req.description is not None:
            section.description = req.description
        if req.is_active is not None:
            section.is_active = req.is_active
        await self.db.commit()
        await self.db.refresh(section)
        return section

    async def delete_section(self, section_id: str) -> bool:
        section = await self.repo.get_section(section_id)
        if not section:
            raise NotFoundException("Section not found.")
        await self.db.delete(section)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Attendance                                                          #
    # ------------------------------------------------------------------ #

    async def create_attendance(self, req: AttendanceCreateRequest) -> AttendanceModel:
        # Validate employee exists
        emp_profile = await self._get_profile(req.employee_id)
        record = AttendanceModel(
            employee_id=emp_profile.id,
            attendance_date=req.attendance_date,
            check_in=req.check_in,
            check_out=req.check_out,
            status=req.status,
            notes=req.notes,
        )
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def list_attendance(
        self, employee_id: str, page: int = 1, page_size: int = 30
    ) -> Tuple[List[AttendanceModel], int]:
        profile = await self._get_profile(employee_id)
        skip = (page - 1) * page_size
        return await self.repo.list_attendance(profile.id, skip, page_size)

    async def update_attendance(
        self, attendance_id: str, req: AttendanceUpdateRequest
    ) -> AttendanceModel:
        record = await self.repo.get_attendance(attendance_id)
        if not record:
            raise NotFoundException("Attendance record not found.")
        if req.check_in is not None:
            record.check_in = req.check_in
        if req.check_out is not None:
            record.check_out = req.check_out
        if req.status is not None:
            record.status = req.status
        if req.notes is not None:
            record.notes = req.notes
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def delete_attendance(self, attendance_id: str) -> bool:
        record = await self.repo.get_attendance(attendance_id)
        if not record:
            raise NotFoundException("Attendance record not found.")
        await self.db.delete(record)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Targets                                                             #
    # ------------------------------------------------------------------ #

    async def create_target(self, req: TargetCreateRequest) -> TargetModel:
        profile = await self._get_profile(req.employee_id)
        if req.period_end <= req.period_start:
            raise BusinessLogicException("period_end must be after period_start.")
        target = TargetModel(
            employee_id=profile.id,
            period_start=req.period_start,
            period_end=req.period_end,
            target_amount=req.target_amount,
            target_type=req.target_type,
            notes=req.notes,
        )
        self.db.add(target)
        await self.db.commit()
        await self.db.refresh(target)
        return target

    async def list_targets(
        self, employee_id: str, page: int = 1, page_size: int = 20
    ) -> Tuple[List[TargetModel], int]:
        profile = await self._get_profile(employee_id)
        skip = (page - 1) * page_size
        return await self.repo.list_targets(profile.id, skip, page_size)

    async def update_target(self, target_id: str, req: TargetUpdateRequest) -> TargetModel:
        target = await self.repo.get_target(target_id)
        if not target:
            raise NotFoundException("Target not found.")
        if req.target_amount is not None:
            target.target_amount = req.target_amount
        if req.achieved_amount is not None:
            target.achieved_amount = req.achieved_amount
        if req.target_type is not None:
            target.target_type = req.target_type
        if req.notes is not None:
            target.notes = req.notes
        await self.db.commit()
        await self.db.refresh(target)
        return target

    async def delete_target(self, target_id: str) -> bool:
        target = await self.repo.get_target(target_id)
        if not target:
            raise NotFoundException("Target not found.")
        await self.db.delete(target)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Performance                                                         #
    # ------------------------------------------------------------------ #

    async def create_performance(
        self, req: PerformanceCreateRequest, reviewer_id: str
    ) -> PerformanceModel:
        profile = await self._get_profile(req.employee_id)
        review = PerformanceModel(
            employee_id=profile.id,
            review_date=req.review_date,
            rating=req.rating,
            review_period=req.review_period,
            reviewer_id=reviewer_id,
            comments=req.comments,
        )
        self.db.add(review)
        await self.db.commit()
        await self.db.refresh(review)
        return review

    async def list_performance(
        self, employee_id: str, page: int = 1, page_size: int = 20
    ) -> Tuple[List[PerformanceModel], int]:
        profile = await self._get_profile(employee_id)
        skip = (page - 1) * page_size
        return await self.repo.list_performance(profile.id, skip, page_size)

    async def update_performance(
        self, perf_id: str, req: PerformanceUpdateRequest
    ) -> PerformanceModel:
        review = await self.repo.get_performance(perf_id)
        if not review:
            raise NotFoundException("Performance review not found.")
        if req.rating is not None:
            review.rating = req.rating
        if req.review_period is not None:
            review.review_period = req.review_period
        if req.comments is not None:
            review.comments = req.comments
        await self.db.commit()
        await self.db.refresh(review)
        return review

    async def delete_performance(self, perf_id: str) -> bool:
        review = await self.repo.get_performance(perf_id)
        if not review:
            raise NotFoundException("Performance review not found.")
        await self.db.delete(review)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    async def _get_profile(self, employee_user_id: str) -> EmployeeProfileModel:
        """Resolve employee user_id → EmployeeProfileModel, raising 404 if not found."""
        user = await self.repo.get_employee_by_id(employee_user_id)
        if not user or not user.employee_profile:
            raise NotFoundException("Employee not found.")
        return user.employee_profile
