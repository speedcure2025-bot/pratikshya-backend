"""
Database seeding script.

Populates predefined system roles, permissions, and role-permission mappings into
`pratikshya.roles`, `pratikshya.permissions`, `pratikshya.role_permissions`,
and assigns the `SUPER_ADMIN` role to active admin users.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

# Ensure project root is on PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("scripts.seed_database")


async def main() -> None:
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.core.constants import PredefinedRole
    from app.api.v1.admin import BUILT_IN_ROLES
    from app.models.rbac.role import RoleModel
    from app.models.rbac.permission import PermissionModel
    from app.models.rbac.role_permission import RolePermissionModel
    from app.models.auth.user import UserModel
    from app.models.rbac.user_role import UserRoleModel

    logger.info("Seeding database with predefined system roles and permissions...")

    async with AsyncSessionLocal() as db:
        # 1. Seed Roles
        role_map: dict[str, RoleModel] = {}
        for role_enum in PredefinedRole:
            role_name = role_enum.value
            stmt = select(RoleModel).where(RoleModel.name == role_name)
            res = await db.execute(stmt)
            role = res.scalars().first()

            if not role:
                role = RoleModel(
                    name=role_name,
                    description=f"System role: {role_name.replace('_', ' ').title()}",
                    is_system=True,
                )
                db.add(role)
                await db.flush()
                logger.info("  + Created role: %s", role_name)
            role_map[role_name] = role

        await db.commit()

        # 2. Extract and Seed Permissions
        all_perms: set[str] = set()
        for rdata in BUILT_IN_ROLES.values():
            for pcode in rdata.get("permissions", []):
                all_perms.add(pcode)

        perm_map: dict[str, PermissionModel] = {}
        for pcode in sorted(all_perms):
            stmt = select(PermissionModel).where(PermissionModel.code == pcode)
            res = await db.execute(stmt)
            perm = res.scalars().first()

            if not perm:
                category = pcode.split(".")[0] if "." in pcode else "system"
                perm = PermissionModel(
                    code=pcode,
                    name=pcode.replace(".", " ").replace("_", " ").title(),
                    category=category,
                    description=f"Permission for {pcode}",
                )
                db.add(perm)
                await db.flush()
                logger.info("  + Created permission: %s", pcode)
            perm_map[pcode] = perm

        await db.commit()

        # 3. Seed Role-Permissions mappings
        for rcode, rdata in BUILT_IN_ROLES.items():
            db_role = role_map.get(rcode) or role_map.get(rcode.upper())
            if not db_role:
                continue

            for pcode in rdata.get("permissions", []):
                db_perm = perm_map.get(pcode)
                if not db_perm:
                    continue

                rp_stmt = select(RolePermissionModel).where(
                    RolePermissionModel.role_id == db_role.id,
                    RolePermissionModel.permission_id == db_perm.id,
                )
                rp_res = await db.execute(rp_stmt)
                if not rp_res.scalars().first():
                    db.add(RolePermissionModel(role_id=db_role.id, permission_id=db_perm.id))
                    logger.info("  + Mapped permission '%s' -> role '%s'", pcode, db_role.name)

        await db.commit()

        # 4. Link SUPER_ADMIN role to any admin user missing it
        super_admin_role = role_map.get(PredefinedRole.SUPER_ADMIN.value)
        if super_admin_role:
            admin_users_stmt = select(UserModel).where(UserModel.user_type == "admin")
            admin_users_res = await db.execute(admin_users_stmt)
            admin_users = admin_users_res.scalars().all()

            for admin_user in admin_users:
                ur_stmt = select(UserRoleModel).where(
                    UserRoleModel.user_id == admin_user.id,
                    UserRoleModel.role_id == super_admin_role.id,
                )
                ur_res = await db.execute(ur_stmt)
                if not ur_res.scalars().first():
                    db.add(UserRoleModel(user_id=admin_user.id, role_id=super_admin_role.id))
                    logger.info("  + Assigned SUPER_ADMIN role to user: %s (%s)", admin_user.email, admin_user.id)

            await db.commit()

    logger.info("✅ Database seeding completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())


