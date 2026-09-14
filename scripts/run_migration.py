"""Run the idempotent SQL migration against the configured DATABASE_URL."""
import asyncio
import pathlib
import sys

# Add project root so app.config is importable
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import asyncpg


SQL_FILE = pathlib.Path(__file__).with_name("apply_remaining_migrations.sql")

DATABASE_URL = (
    "postgresql://MEdixo:llUNUFIiX1tk30CGmEYt"
    "@database-1-restored.cfck4skoe4h0.ap-south-2.rds.amazonaws.com:5432/postgres"
)


async def main():
    print(f"Connecting to RDS …")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        sql = SQL_FILE.read_text(encoding="utf-8")
        # asyncpg.execute can run multiple statements
        print("Applying migrations …")
        await conn.execute(sql)
        print("✅  All migrations applied successfully!")
    except Exception as e:
        print(f"❌  Error: {e}")
        raise
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
