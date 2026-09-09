"""
Employee management API.

Admin-side routes:    /admin/employees/...   (requires admin or employees.* permission)
Employee self-service: /employee/me/...      (requires employee JWT)

Spec source: API_CONTRACT.md §EMPLOYEE, AUTHORIZATION_MATRIX.md
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_admin, get_current_employee, get_user_roles_and_permissions, require_permission_for_user
from app.models.auth.user import UserModel
from app.core.pagination import PaginatedResponse, PaginationParams
from app.schemas.common import DataResponse, BaseResponse
from app.schemas.employee.employee import (
    EmployeeCreateRequest,
    EmployeeUpdateRequest,
    EmployeeStatusRequest,
    ResetEmployeePasswordRequest,
    EmployeePermissionsRequest,
    EmployeeResponse,
    EmployeeProfileDTO,
)
from app.schemas.employee.attendance import (
    AttendanceCreateRequest,
    AttendanceUpdateRequest,
    AttendanceResponse,
)
from app.schemas.employee.performance import (
    PerformanceCreateRequest,
    PerformanceUpdateRequest,
    PerformanceResponse,
    TargetCreateRequest,
    TargetUpdateRequest,
    TargetResponse,
)
from app.schemas.employee.department import (
    DepartmentCreateRequest,
    DepartmentUpdateRequest,
    DepartmentResponse,
    SectionCreateRequest,
    SectionUpdateRequest,
    SectionResponse,
)
from app.services.employee.employee_service import EmployeeService

router = APIRouter(tags=["Employee Operations"])


# =========================================================================== #
#  Helper — build EmployeeResponse from UserModel                               #
# =========================================================================== #

def _build_employee_response(user: UserModel, roles: Optional[List[str]] = None, permissions: Optional[List[str]] = None) -> EmployeeResponse:
    profile_dto = None
    if user.employee_profile:
        p = user.employee_profile
        profile_dto = EmployeeProfileDTO(
            id=p.id,
            employee_code=p.employee_code,
            designation=p.designation,
            department=p.department,
            department_id=p.department_id,
            section_id=p.section_id,
        )
    return EmployeeResponse(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        status=user.status,
        is_verified=user.is_verified,
        force_password_change=user.force_password_change,
        mustChangePassword=user.force_password_change,
        created_at=user.created_at,
        updated_at=user.updated_at,
        profile=profile_dto,
        roles=roles or [],
        permissions=permissions or [],
    )


# =========================================================================== #
#  ADMIN — Employee CRUD                                                        #
#  Prefix: /admin/employees                                                     #
# =========================================================================== #

@router.post(
    "/admin/employees",
    response_model=DataResponse[EmployeeResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Onboard a new employee (admin)",
    description=(
        "Body (spec): `{ firstName, lastName, email, phone, role, department, section?, "
        "store, joiningDate, shift?, permissionMode?, permissions? }`  \n"
        "Generates a unique Employee ID in format `PF-<ROLEPREFIX>-#####` and sets "
        "`mustChangePassword = true`. Activity: `EMPLOYEE_CREATED`."
    ),
)
async def create_employee(
    req: EmployeeCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    user = await service.create_employee(req, creator_id=admin.id)
    return DataResponse(data=_build_employee_response(user), message="Employee created successfully.")


@router.get(
    "/admin/employees",
    response_model=PaginatedResponse[EmployeeResponse],
    summary="List all employees (admin)",
    description="Requires `employees.view` permission.",
)
async def list_employees(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None, description="Search by name, email, code, or designation"),
    status: Optional[str] = Query(default=None, description="Filter by status: ACTIVE | PENDING | ON_LEAVE | SUSPENDED | INACTIVE"),
    department_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    items, total = await service.list_employees(
        page=page, page_size=page_size, search=search, status=status, department_id=department_id
    )
    params = PaginationParams(page=page, page_size=page_size)
    return PaginatedResponse.create(
        items=[_build_employee_response(u) for u in items],
        total=total,
        params=params,
    )


@router.get(
    "/admin/employees/{employee_id}",
    response_model=DataResponse[EmployeeResponse],
    summary="Get employee by ID (admin)",
    description=(
        "Returns `PublicEmployee` — never exposes hashed_password. "
        "Requires `employees.view` permission."
    ),
)
async def get_employee(
    employee_id: str,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    user = await service.get_employee(employee_id)
    return DataResponse(data=_build_employee_response(user))


@router.patch(
    "/admin/employees/{employee_id}",
    response_model=DataResponse[EmployeeResponse],
    summary="Update employee profile (admin)",
    description="Requires `employees.edit` permission. Activities: EMPLOYEE_UPDATED, ROLE_CHANGED, DEPARTMENT_CHANGED.",
)
async def update_employee(
    employee_id: str,
    req: EmployeeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    user = await service.update_employee(employee_id, req)
    return DataResponse(data=_build_employee_response(user), message="Employee updated.")


@router.post(
    "/admin/employees/{employee_id}/status",
    response_model=DataResponse[EmployeeResponse],
    summary="Change employee account status (admin)",
    description=(
        "Body: `{ status: ACTIVE | PENDING | ON_LEAVE | SUSPENDED | INACTIVE }`  \n"
        "**SUSPENDED** and **INACTIVE** immediately deny all permissions on the next request, "
        "not just the next login. Requires `employees.suspend` permission.  \n"
        "Activities: STATUS_CHANGED, EMPLOYEE_SUSPENDED / EMPLOYEE_ACTIVATED / EMPLOYEE_DEACTIVATED."
    ),
)
async def update_employee_status(
    employee_id: str,
    req: EmployeeStatusRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    user = await service.update_employee_status(employee_id, req)
    return DataResponse(
        data=_build_employee_response(user),
        message=f"Employee status set to {req.status}.",
    )


# Keep backward-compat PATCH alias (hidden from docs)
@router.patch(
    "/admin/employees/{employee_id}/status",
    response_model=DataResponse[EmployeeResponse],
    include_in_schema=False,
    summary="Change employee status (legacy PATCH alias)",
)
async def update_employee_status_patch(
    employee_id: str,
    req: EmployeeStatusRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    user = await service.update_employee_status(employee_id, req)
    return DataResponse(data=_build_employee_response(user), message=f"Employee status set to {req.status}.")


@router.post(
    "/admin/employees/{employee_id}/reset-password",
    response_model=BaseResponse,
    summary="Admin reset of employee password",
    description=(
        "Sets `mustChangePassword = true`. Generates a secure temp password if none is supplied.  \n"
        "Requires `employees.resetPassword` permission. Activity: PASSWORD_RESET."
    ),
)
async def reset_employee_password(
    employee_id: str,
    req: ResetEmployeePasswordRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    await service.reset_employee_password(employee_id, req)
    return BaseResponse(message="Password reset successfully.")


@router.put(
    "/admin/employees/{employee_id}/permissions",
    response_model=DataResponse[EmployeeResponse],
    summary="Update employee permission mode and custom permissions (admin)",
    description=(
        "Body: `{ permissionMode: 'role'|'custom', permissions: string[] }`  \n"
        "**SUPER_ADMIN always resolves to full access regardless of stored overrides.**  \n"
        "Requires `employees.managePermissions` permission. Activity: PERMISSIONS_CHANGED."
    ),
)
async def update_employee_permissions(
    employee_id: str,
    req: EmployeePermissionsRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    user = await service.update_employee_permissions(employee_id, req)
    return DataResponse(data=_build_employee_response(user), message="Permissions updated.")


@router.delete(
    "/admin/employees/{employee_id}",
    response_model=BaseResponse,
    summary="Hard-delete an employee account (admin)",
    description="Prefer `POST /admin/employees/{id}/status` with `status=INACTIVE` for soft removal.",
)
async def delete_employee(
    employee_id: str,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    await service.delete_employee(employee_id)
    return BaseResponse(message="Employee deleted.")


# =========================================================================== #
#  Backward-compat routes under /employees (legacy prefix)                      #
# =========================================================================== #

@router.post(
    "/employees",
    response_model=DataResponse[EmployeeResponse],
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
    summary="Onboard employee (legacy prefix)",
)
async def create_employee_legacy(
    req: EmployeeCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    user = await service.create_employee(req, creator_id=admin.id)
    return DataResponse(data=_build_employee_response(user), message="Employee created successfully.")


@router.get("/employees", response_model=PaginatedResponse[EmployeeResponse], include_in_schema=False)
async def list_employees_legacy(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    department_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    items, total = await service.list_employees(page=page, page_size=page_size, search=search, status=status, department_id=department_id)
    params = PaginationParams(page=page, page_size=page_size)
    return PaginatedResponse.create(items=[_build_employee_response(u) for u in items], total=total, params=params)


@router.get("/employees/{employee_id}", response_model=DataResponse[EmployeeResponse], include_in_schema=False)
async def get_employee_legacy(employee_id: str, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    user = await service.get_employee(employee_id)
    return DataResponse(data=_build_employee_response(user))


@router.patch("/employees/{employee_id}", response_model=DataResponse[EmployeeResponse], include_in_schema=False)
async def update_employee_legacy(employee_id: str, req: EmployeeUpdateRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    user = await service.update_employee(employee_id, req)
    return DataResponse(data=_build_employee_response(user), message="Employee updated.")


@router.patch("/employees/{employee_id}/status", response_model=DataResponse[EmployeeResponse], include_in_schema=False)
async def update_employee_status_legacy(employee_id: str, req: EmployeeStatusRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    user = await service.update_employee_status(employee_id, req)
    return DataResponse(data=_build_employee_response(user))


@router.post("/employees/{employee_id}/reset-password", response_model=BaseResponse, include_in_schema=False)
async def reset_password_legacy(employee_id: str, req: ResetEmployeePasswordRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    await service.reset_employee_password(employee_id, req)
    return BaseResponse(message="Password reset successfully.")


@router.delete("/employees/{employee_id}", response_model=BaseResponse, include_in_schema=False)
async def delete_employee_legacy(employee_id: str, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    await service.delete_employee(employee_id)
    return BaseResponse(message="Employee deleted.")


# =========================================================================== #
#  EMPLOYEE SELF-SERVICE                                                        #
#  Prefix: /employee/me                                                         #
# =========================================================================== #

@router.get(
    "/employee/me",
    response_model=DataResponse[EmployeeResponse],
    summary="Employee — get own profile",
    description="Returns the current employee's own PublicEmployee record.",
)
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    current_employee: UserModel = Depends(get_current_employee),
):
    service = EmployeeService(db)
    user = await service.get_employee(current_employee.id)
    if not user.employee_profile:
        from app.core.exceptions import NotFoundException
        raise NotFoundException("Employee profile not found.")
    roles, permissions = await get_user_roles_and_permissions(current_employee, db)
    return DataResponse(data=_build_employee_response(user, roles=roles, permissions=permissions))


@router.get(
    "/employee/me/assigned-products",
    summary="Employee — list products assigned to me",
    description=(
        "Returns the product IDs and basic info for products where "
        "`product.assignedEmployeeId === self.employeeId`.  \n"
        "Requires `products.view` permission."
    ),
)
async def get_assigned_products(
    db: AsyncSession = Depends(get_db),
    current_employee: UserModel = Depends(get_current_employee),
):
    # TODO: implement once ProductService is wired up.
    # Returns a placeholder list until the products module is implemented.
    return {"ok": True, "data": [], "message": "Assigned products endpoint — implementation pending product service."}


@router.get(
    "/employee/me/workflow",
    summary="Employee — product workflow view",
    description=(
        "Returns the employee's active product workflow items "
        "(drafts, pending reviews, etc.). Source: `getProductWorkflowView`."
    ),
)
async def get_workflow_view(
    db: AsyncSession = Depends(get_db),
    current_employee: UserModel = Depends(get_current_employee),
):
    # TODO: implement once ProductService is wired up.
    return {"ok": True, "data": {}, "message": "Workflow view endpoint — implementation pending product service."}


@router.get(
    "/employee/desk",
    summary="Employee — operations desk metrics",
    description=(
        "Returns `defaultDashboardMetrics` for the current employee's role: "
        "orders, tasks, attendance summary, etc."
    ),
)
async def get_employee_desk(
    db: AsyncSession = Depends(get_db),
    current_employee: UserModel = Depends(get_current_employee),
):
    # TODO: implement with AnalyticsService / OperationsService
    return {"ok": True, "data": {}, "message": "Desk metrics endpoint — implementation pending analytics service."}


# =========================================================================== #
#  DEPARTMENTS                                                                  #
# =========================================================================== #

@router.post(
    "/admin/employees/departments",
    response_model=DataResponse[DepartmentResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a department (admin)",
)
async def create_department(
    req: DepartmentCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    dept = await service.create_department(req)
    return DataResponse(data=DepartmentResponse.model_validate(dept), message="Department created.")


@router.get(
    "/admin/employees/departments",
    response_model=DataResponse[List[DepartmentResponse]],
    summary="List all departments (admin)",
)
async def list_departments(
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    depts = await service.list_departments()
    return DataResponse(data=[DepartmentResponse.model_validate(d) for d in depts])


@router.get(
    "/admin/employees/departments/{department_id}",
    response_model=DataResponse[DepartmentResponse],
    summary="Get a department (admin)",
)
async def get_department(
    department_id: str,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    dept = await service.get_department(department_id)
    return DataResponse(data=DepartmentResponse.model_validate(dept))


@router.patch(
    "/admin/employees/departments/{department_id}",
    response_model=DataResponse[DepartmentResponse],
    summary="Update a department (admin)",
)
async def update_department(
    department_id: str,
    req: DepartmentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    dept = await service.update_department(department_id, req)
    return DataResponse(data=DepartmentResponse.model_validate(dept), message="Department updated.")


@router.delete(
    "/admin/employees/departments/{department_id}",
    response_model=BaseResponse,
    summary="Delete a department (admin)",
)
async def delete_department(
    department_id: str,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    await service.delete_department(department_id)
    return BaseResponse(message="Department deleted.")


# Legacy /employees/departments routes (hidden)
@router.post("/employees/departments/", response_model=DataResponse[DepartmentResponse], status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_department_legacy(req: DepartmentCreateRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    dept = await service.create_department(req)
    return DataResponse(data=DepartmentResponse.model_validate(dept), message="Department created.")


@router.get("/employees/departments/", response_model=DataResponse[List[DepartmentResponse]], include_in_schema=False)
async def list_departments_legacy(db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    depts = await service.list_departments()
    return DataResponse(data=[DepartmentResponse.model_validate(d) for d in depts])


@router.get("/employees/departments/{department_id}", response_model=DataResponse[DepartmentResponse], include_in_schema=False)
async def get_department_legacy(department_id: str, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    dept = await service.get_department(department_id)
    return DataResponse(data=DepartmentResponse.model_validate(dept))


@router.patch("/employees/departments/{department_id}", response_model=DataResponse[DepartmentResponse], include_in_schema=False)
async def update_department_legacy(department_id: str, req: DepartmentUpdateRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    dept = await service.update_department(department_id, req)
    return DataResponse(data=DepartmentResponse.model_validate(dept))


@router.delete("/employees/departments/{department_id}", response_model=BaseResponse, include_in_schema=False)
async def delete_department_legacy(department_id: str, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    await service.delete_department(department_id)
    return BaseResponse(message="Department deleted.")


# =========================================================================== #
#  SECTIONS                                                                     #
# =========================================================================== #

@router.post(
    "/admin/employees/sections",
    response_model=DataResponse[SectionResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a section within a department (admin)",
)
async def create_section(
    req: SectionCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    section = await service.create_section(req)
    return DataResponse(data=SectionResponse.model_validate(section), message="Section created.")


@router.get(
    "/admin/employees/sections",
    response_model=DataResponse[List[SectionResponse]],
    summary="List sections, optionally filtered by department (admin)",
)
async def list_sections(
    department_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    sections = await service.list_sections(department_id)
    return DataResponse(data=[SectionResponse.model_validate(s) for s in sections])


@router.patch(
    "/admin/employees/sections/{section_id}",
    response_model=DataResponse[SectionResponse],
    summary="Update a section (admin)",
)
async def update_section(
    section_id: str,
    req: SectionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    section = await service.update_section(section_id, req)
    return DataResponse(data=SectionResponse.model_validate(section), message="Section updated.")


@router.delete(
    "/admin/employees/sections/{section_id}",
    response_model=BaseResponse,
    summary="Delete a section (admin)",
)
async def delete_section(
    section_id: str,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    await service.delete_section(section_id)
    return BaseResponse(message="Section deleted.")


# Legacy /employees/sections routes (hidden)
@router.post("/employees/sections/", response_model=DataResponse[SectionResponse], status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_section_legacy(req: SectionCreateRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    section = await service.create_section(req)
    return DataResponse(data=SectionResponse.model_validate(section), message="Section created.")


@router.get("/employees/sections/", response_model=DataResponse[List[SectionResponse]], include_in_schema=False)
async def list_sections_legacy(department_id: Optional[str] = Query(default=None), db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    sections = await service.list_sections(department_id)
    return DataResponse(data=[SectionResponse.model_validate(s) for s in sections])


@router.patch("/employees/sections/{section_id}", response_model=DataResponse[SectionResponse], include_in_schema=False)
async def update_section_legacy(section_id: str, req: SectionUpdateRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    section = await service.update_section(section_id, req)
    return DataResponse(data=SectionResponse.model_validate(section))


@router.delete("/employees/sections/{section_id}", response_model=BaseResponse, include_in_schema=False)
async def delete_section_legacy(section_id: str, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    await service.delete_section(section_id)
    return BaseResponse(message="Section deleted.")


# =========================================================================== #
#  ATTENDANCE                                                                   #
# =========================================================================== #

@router.post(
    "/admin/employees/{employee_id}/attendance",
    response_model=DataResponse[AttendanceResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Admin — record or correct attendance for an employee",
    description=(
        "Requires `attendance.correct` permission for correcting another employee's record. "
        "Activity: ATTENDANCE_CORRECTED."
    ),
)
async def create_attendance(
    employee_id: str,
    req: AttendanceCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    req.employee_id = employee_id
    service = EmployeeService(db)
    record = await service.create_attendance(req)
    return DataResponse(data=AttendanceResponse.model_validate(record), message="Attendance recorded.")


@router.get(
    "/admin/employees/{employee_id}/attendance",
    response_model=PaginatedResponse[AttendanceResponse],
    summary="Admin — list attendance records for an employee",
)
async def list_attendance(
    employee_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    items, total = await service.list_attendance(employee_id, page, page_size)
    params = PaginationParams(page=page, page_size=page_size)
    return PaginatedResponse.create(
        items=[AttendanceResponse.model_validate(r) for r in items],
        total=total,
        params=params,
    )


@router.patch(
    "/admin/employees/attendance/{attendance_id}",
    response_model=DataResponse[AttendanceResponse],
    summary="Admin — update (correct) an attendance record",
    description="Corrections are append-only with actor + reason + before/after per spec.",
)
async def update_attendance(
    attendance_id: str,
    req: AttendanceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    record = await service.update_attendance(attendance_id, req)
    return DataResponse(data=AttendanceResponse.model_validate(record), message="Attendance updated.")


@router.delete(
    "/admin/employees/attendance/{attendance_id}",
    response_model=BaseResponse,
    summary="Admin — delete an attendance record",
)
async def delete_attendance(
    attendance_id: str,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    await service.delete_attendance(attendance_id)
    return BaseResponse(message="Attendance record deleted.")


# Legacy nested attendance routes under /employees (hidden)
@router.post("/employees/{employee_id}/attendance", response_model=DataResponse[AttendanceResponse], status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_attendance_legacy(employee_id: str, req: AttendanceCreateRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    req.employee_id = employee_id
    service = EmployeeService(db)
    record = await service.create_attendance(req)
    return DataResponse(data=AttendanceResponse.model_validate(record))


@router.get("/employees/{employee_id}/attendance", response_model=PaginatedResponse[AttendanceResponse], include_in_schema=False)
async def list_attendance_legacy(employee_id: str, page: int = Query(default=1, ge=1), page_size: int = Query(default=30, ge=1, le=100), db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    items, total = await service.list_attendance(employee_id, page, page_size)
    params = PaginationParams(page=page, page_size=page_size)
    return PaginatedResponse.create(items=[AttendanceResponse.model_validate(r) for r in items], total=total, params=params)


@router.patch("/employees/attendance/{attendance_id}", response_model=DataResponse[AttendanceResponse], include_in_schema=False)
async def update_attendance_legacy(attendance_id: str, req: AttendanceUpdateRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    record = await service.update_attendance(attendance_id, req)
    return DataResponse(data=AttendanceResponse.model_validate(record))


@router.delete("/employees/attendance/{attendance_id}", response_model=BaseResponse, include_in_schema=False)
async def delete_attendance_legacy(attendance_id: str, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    await service.delete_attendance(attendance_id)
    return BaseResponse(message="Attendance record deleted.")


# =========================================================================== #
#  TARGETS                                                                      #
# =========================================================================== #

@router.post(
    "/admin/employees/{employee_id}/targets",
    response_model=DataResponse[TargetResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Admin — assign a sales target to an employee",
)
async def create_target(
    employee_id: str,
    req: TargetCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    req.employee_id = employee_id
    service = EmployeeService(db)
    target = await service.create_target(req)
    return DataResponse(data=TargetResponse.model_validate(target), message="Target assigned.")


@router.get(
    "/admin/employees/{employee_id}/targets",
    response_model=PaginatedResponse[TargetResponse],
    summary="Admin — list targets for an employee",
)
async def list_targets(
    employee_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    items, total = await service.list_targets(employee_id, page, page_size)
    params = PaginationParams(page=page, page_size=page_size)
    return PaginatedResponse.create(
        items=[TargetResponse.model_validate(t) for t in items],
        total=total,
        params=params,
    )


@router.patch(
    "/admin/employees/targets/{target_id}",
    response_model=DataResponse[TargetResponse],
    summary="Admin — update a target (e.g. record achieved amount)",
)
async def update_target(
    target_id: str,
    req: TargetUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    target = await service.update_target(target_id, req)
    return DataResponse(data=TargetResponse.model_validate(target), message="Target updated.")


@router.delete(
    "/admin/employees/targets/{target_id}",
    response_model=BaseResponse,
    summary="Admin — delete a target",
)
async def delete_target(
    target_id: str,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    await service.delete_target(target_id)
    return BaseResponse(message="Target deleted.")


# Legacy targets routes (hidden)
@router.post("/employees/{employee_id}/targets", response_model=DataResponse[TargetResponse], status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_target_legacy(employee_id: str, req: TargetCreateRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    req.employee_id = employee_id
    service = EmployeeService(db)
    target = await service.create_target(req)
    return DataResponse(data=TargetResponse.model_validate(target))


@router.get("/employees/{employee_id}/targets", response_model=PaginatedResponse[TargetResponse], include_in_schema=False)
async def list_targets_legacy(employee_id: str, page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100), db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    items, total = await service.list_targets(employee_id, page, page_size)
    params = PaginationParams(page=page, page_size=page_size)
    return PaginatedResponse.create(items=[TargetResponse.model_validate(t) for t in items], total=total, params=params)


@router.patch("/employees/targets/{target_id}", response_model=DataResponse[TargetResponse], include_in_schema=False)
async def update_target_legacy(target_id: str, req: TargetUpdateRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    target = await service.update_target(target_id, req)
    return DataResponse(data=TargetResponse.model_validate(target))


@router.delete("/employees/targets/{target_id}", response_model=BaseResponse, include_in_schema=False)
async def delete_target_legacy(target_id: str, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    await service.delete_target(target_id)
    return BaseResponse(message="Target deleted.")


# =========================================================================== #
#  PERFORMANCE REVIEWS                                                          #
# =========================================================================== #

@router.post(
    "/admin/employees/{employee_id}/performance",
    response_model=DataResponse[PerformanceResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Admin — submit a performance review",
    description="Requires `performance.review` permission. Activity: PERFORMANCE_REVIEWED.",
)
async def create_performance(
    employee_id: str,
    req: PerformanceCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    req.employee_id = employee_id
    service = EmployeeService(db)
    review = await service.create_performance(req, reviewer_id=admin.id)
    return DataResponse(data=PerformanceResponse.model_validate(review), message="Performance review submitted.")


@router.get(
    "/admin/employees/{employee_id}/performance",
    response_model=PaginatedResponse[PerformanceResponse],
    summary="Admin — list performance reviews for an employee",
)
async def list_performance(
    employee_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    items, total = await service.list_performance(employee_id, page, page_size)
    params = PaginationParams(page=page, page_size=page_size)
    return PaginatedResponse.create(
        items=[PerformanceResponse.model_validate(r) for r in items],
        total=total,
        params=params,
    )


@router.patch(
    "/admin/employees/performance/{performance_id}",
    response_model=DataResponse[PerformanceResponse],
    summary="Admin — update a performance review",
)
async def update_performance(
    performance_id: str,
    req: PerformanceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    review = await service.update_performance(performance_id, req)
    return DataResponse(data=PerformanceResponse.model_validate(review), message="Performance review updated.")


@router.delete(
    "/admin/employees/performance/{performance_id}",
    response_model=BaseResponse,
    summary="Admin — delete a performance review",
)
async def delete_performance(
    performance_id: str,
    db: AsyncSession = Depends(get_db),
    admin: UserModel = Depends(get_current_admin),
):
    service = EmployeeService(db)
    await service.delete_performance(performance_id)
    return BaseResponse(message="Performance review deleted.")


# Legacy performance routes (hidden)
@router.post("/employees/{employee_id}/performance", response_model=DataResponse[PerformanceResponse], status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_performance_legacy(employee_id: str, req: PerformanceCreateRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    req.employee_id = employee_id
    service = EmployeeService(db)
    review = await service.create_performance(req, reviewer_id=admin.id)
    return DataResponse(data=PerformanceResponse.model_validate(review))


@router.get("/employees/{employee_id}/performance", response_model=PaginatedResponse[PerformanceResponse], include_in_schema=False)
async def list_performance_legacy(employee_id: str, page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100), db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    items, total = await service.list_performance(employee_id, page, page_size)
    params = PaginationParams(page=page, page_size=page_size)
    return PaginatedResponse.create(items=[PerformanceResponse.model_validate(r) for r in items], total=total, params=params)


@router.patch("/employees/performance/{performance_id}", response_model=DataResponse[PerformanceResponse], include_in_schema=False)
async def update_performance_legacy(performance_id: str, req: PerformanceUpdateRequest, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    review = await service.update_performance(performance_id, req)
    return DataResponse(data=PerformanceResponse.model_validate(review))


@router.delete("/employees/performance/{performance_id}", response_model=BaseResponse, include_in_schema=False)
async def delete_performance_legacy(performance_id: str, db: AsyncSession = Depends(get_db), admin: UserModel = Depends(get_current_admin)):
    service = EmployeeService(db)
    await service.delete_performance(performance_id)
    return BaseResponse(message="Performance review deleted.")
