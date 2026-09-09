import asyncio
from sqlalchemy import text
from app.core.database import engine

async def main():
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT current_database(), current_user")
        )
        row = result.fetchone()
        print("DATABASE:", row[0])
        print("USER:", row[1])

    await engine.dispose()

asyncio.run(main())