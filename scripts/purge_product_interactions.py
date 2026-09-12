"""Schedule daily: python -m scripts.purge_product_interactions.

Deletes at most --batches * 5000 expired rows per run, committing each batch.
Safe to retry. No customer details printed. Schedule through existing operations
scheduler; no additional always-on worker or cache required.
"""
import argparse
import asyncio
from datetime import timedelta
from sqlalchemy import delete, select
from app.core.database import AsyncSessionLocal
from app.models.customer.product_interaction import UserProductInteractionModel as Interaction
from app.services.catalog.recommendation_service import RETENTION_DAYS, now_utc

async def purge(batches=20):
    total = 0
    async with AsyncSessionLocal() as db:
        for _ in range(batches):
            expired = select(Interaction.id).where(Interaction.created_at < now_utc() - timedelta(days=RETENTION_DAYS)).order_by(Interaction.created_at, Interaction.id).limit(5000)
            result = await db.execute(delete(Interaction).where(Interaction.id.in_(expired)))
            await db.commit()
            total += result.rowcount
            if result.rowcount < 5000:
                break
    print(f"Expired behavioral rows deleted: {total}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batches", type=int, default=20, choices=range(1, 101))
    asyncio.run(purge(parser.parse_args().batches))
