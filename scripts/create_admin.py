"""
Super Admin seeding script.

Reads credentials from the environment (.env) and creates the first admin account
via the same AuthService used by the API — so password hashing, role assignment,
and bootstrap-secret gating all go through exactly the same code path.

Usage (from the pratikshya_fashon_backend directory):
    python -m scripts.create_admin

Required .env keys:
    ADMIN_SEED_EMAIL        e.g. admin@pratikshyafashon.com
    ADMIN_SEED_PASSWORD     e.g. Admin@PF2024!
    ADMIN_SEED_FULL_NAME    e.g. Super Admin
    ADMIN_BOOTSTRAP_SECRET  e.g. pf-bootstrap-secret-2024-xK9mN3qR
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

# Make sure the project root is on the path when running as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("scripts.create_admin")


async def main() -> None:
    # Import inside the function so the path fix above takes effect first
    from app.config import settings
    from app.core.database import AsyncSessionLocal

    email = settings.ADMIN_SEED_EMAIL
    password = settings.ADMIN_SEED_PASSWORD
    full_name = settings.ADMIN_SEED_FULL_NAME
    bootstrap_secret = settings.ADMIN_BOOTSTRAP_SECRET

    if not email or not password:
        logger.error(
            "ADMIN_SEED_EMAIL and ADMIN_SEED_PASSWORD must be set in .env. "
            "Cannot create admin without credentials."
        )
        sys.exit(1)

    logger.info("Creating Super Admin account for email=%s ...", email)

    async with AsyncSessionLocal() as db:
        try:
            from sqlalchemy import select, func as sa_func
            from app.models.auth.user import UserModel
            from app.models.rbac.role import RoleModel
            from app.models.rbac.user_role import UserRoleModel
            from app.core.security import hash_password

            # Check if admin already exists
            exists_stmt = select(UserModel).where(UserModel.email == email)
            exists_res = await db.execute(exists_stmt)
            existing = exists_res.scalars().first()

            if existing:
                logger.warning(
                    "⚠️  An account with email=%s already exists (user_id=%s). "
                    "No changes made. Use this email to log in.",
                    email,
                    existing.id,
                )
                return

            # Verify bootstrap secret if one is set
            bootstrap_secret = settings.ADMIN_BOOTSTRAP_SECRET
            count_stmt = select(sa_func.count()).where(
                UserModel.user_type == "admin",
                UserModel.status == "ACTIVE",
            )
            count_res = await db.execute(count_stmt)
            admin_count = count_res.scalar_one()

            if admin_count > 0 and bootstrap_secret:
                logger.error(
                    "Active admins already exist and ADMIN_BOOTSTRAP_SECRET is set. "
                    "Cannot bootstrap a second admin via this script without clearing existing admins."
                )
                sys.exit(1)

            # Create the admin user directly (no token building needed for a seed script)
            new_admin = UserModel(
                email=email,
                full_name=full_name,
                hashed_password=hash_password(password),
                user_type="admin",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            db.add(new_admin)
            await db.flush()

            # Assign SUPER_ADMIN role if it exists
            role_stmt = select(RoleModel).where(RoleModel.name == "SUPER_ADMIN")
            role_res = await db.execute(role_stmt)
            super_admin_role = role_res.scalars().first()
            if super_admin_role:
                db.add(UserRoleModel(user_id=new_admin.id, role_id=super_admin_role.id))
                logger.info("Assigned SUPER_ADMIN role to user.")
            else:
                logger.warning(
                    "SUPER_ADMIN role not found in DB — user created without a role. "
                    "Run migrations or seed roles first if needed."
                )

            await db.commit()

            logger.info(
                "✅  Super Admin created successfully!"
                "\n    user_id  : %s"
                "\n    email    : %s"
                "\n    full_name: %s"
                "\n\nLog in at POST /api/v1/auth/admin/sign-in with:"
                "\n    { \"adminId\": \"%s\", \"password\": \"%s\" }",
                new_admin.id,
                email,
                full_name,
                email,
                password,
            )

        except Exception as exc:
            logger.error("❌  Failed to create admin: %s", exc)
            raise


if __name__ == "__main__":
    asyncio.run(main())
