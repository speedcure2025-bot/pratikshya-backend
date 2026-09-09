import asyncio
from sqlalchemy import text
from app.core.database import engine


async def main():
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) FROM pratikshya.users")
        )
        print("USERS:", result.scalar())

        result = await conn.execute(
            text("SELECT COUNT(*) FROM pratikshya.roles")
        )
        print("ROLES:", result.scalar())

        result = await conn.execute(
            text("SELECT COUNT(*) FROM pratikshya.user_roles")
        )
        print("USER_ROLES:", result.scalar())

    await engine.dispose()


asyncio.run(main())