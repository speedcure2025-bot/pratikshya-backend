import asyncio
from sqlalchemy import text
from app.core.database import engine


async def main():
    async with engine.connect() as conn:
        result = await conn.execute(
            text("""
                SELECT
                    u.id,
                    u.email,
                    r.id AS role_id,
                    r.name AS role_name
                FROM pratikshya.users u
                JOIN pratikshya.user_roles ur
                    ON ur.user_id = u.id
                JOIN pratikshya.roles r
                    ON r.id = ur.role_id
                ORDER BY u.id
            """)
        )

        rows = result.fetchall()

        print("\nUSER → ROLE")
        print("-" * 60)

        for row in rows:
            print(f"USER ID : {row.id}")
            print(f"EMAIL   : {row.email}")
            print(f"ROLE ID : {row.role_id}")
            print(f"ROLE    : {row.role_name}")
            print("-" * 60)

    await engine.dispose()


asyncio.run(main())