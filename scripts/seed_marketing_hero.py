"""
Seed canonical HOME_HERO marketing media — B-02.

Registers the 5 existing hero assets (hero001..hero005) into
media_marketing_media so GET /home returns database-managed hero,
not just hardcoded fallback.

Usage:
  python -m scripts.seed_marketing_hero
  or
  python scripts/seed_marketing_hero.py

Requires DATABASE_URL and existing pratikshya schema (after migration
c7d8e9f0a1b2). Idempotent — skips entries that already exist.

Does NOT create duplicate files, does NOT move files, does NOT touch
product media.
"""

import asyncio
import sys
import os

# Ensure app is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import AsyncSessionLocal
from app.services.media.marketing_media_service import MarketingMediaService
from app.schemas.media.marketing import MarketingMediaCreate

CANONICAL_HERO = [
    ("hero/hero001.avif", "Festive Elegance", "Handwoven stories for the season of celebration", "Explore Collection", "/shop", 0),
    ("hero/hero002.avif", "Bridal Couture", "Crafted for your most special day", "View Bridal", "/bridal", 1),
    ("hero/hero003.avif", "Heritage Weaves", "Six yards of timeless craft", "Shop Sarees", "/women/sarees", 2),
    ("hero/hero004.avif", "The Celebration Edit", "Dress up every moment", "Shop the Edit", "/shop", 3),
    ("hero/hero005.avif", "New Arrivals", "Fresh drapes, just landed", "Shop New", "/shop", 4),
]


async def seed():
    async with AsyncSessionLocal() as session:
        svc = MarketingMediaService(session)
        existing = await svc.list(placement="HOME_HERO", active_only=False)
        existing_keys = {r.object_key for r in existing}

        created = 0
        skipped = 0
        for object_key, title, subtitle, cta_label, cta_href, sort_order in CANONICAL_HERO:
            if object_key in existing_keys:
                print(f"SKIP {object_key} already exists")
                skipped += 1
                continue
            payload = MarketingMediaCreate(
                placement="HOME_HERO",
                objectKey=object_key,
                title=title,
                subtitle=subtitle,
                ctaLabel=cta_label,
                ctaHref=cta_href,
                sortOrder=sort_order,
                isActive=True,
            )
            try:
                row = await svc.create(payload)
                print(f"CREATED {row.object_key} id={row.id} order={row.sort_order}")
                created += 1
            except Exception as exc:
                print(f"FAILED {object_key}: {exc}")

        print(f"\nDone: created={created} skipped={skipped} total_existing={len(existing)}")


if __name__ == "__main__":
    asyncio.run(seed())
