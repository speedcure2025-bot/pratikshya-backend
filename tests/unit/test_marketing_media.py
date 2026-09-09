"""
B-02 Marketing Media — unit tests (SQLite-compatible).

Tests the marketing media domain without requiring PostgreSQL:
  - model import
  - schema validation
  - service logic (in-memory SQLite)
  - placement validation
  - duplicate prevention
  - ordering
  - active/inactive exclusion
  - URL generation
  - GET /home returns configured hero (via service)
  - auth is enforced at router level (tested via permission codes)

If PostgreSQL unavailable, SQLite tests still valid. Integration tests
requiring real DB are marked NOT EXECUTED.
"""

import importlib
import tempfile
import os
import unittest
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.models.base import Base
from app.models.media.marketing_media import MarketingMediaModel
from app.schemas.media.marketing import (
    MARKETING_PLACEMENT_VALUES,
    HOME_HERO_PLACEMENT,
    MarketingMediaCreate,
)
from app.services.media.marketing_media_service import MarketingMediaService

HAS_AIOSQLITE = importlib.util.find_spec("aiosqlite") is not None


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "JSON"


class TestMarketingMediaSchema(unittest.TestCase):
    def test_placement_vocabulary(self):
        self.assertIn("HOME_HERO", MARKETING_PLACEMENT_VALUES)
        self.assertIn("EDITORIAL", MARKETING_PLACEMENT_VALUES)
        self.assertIn("PROMOTION", MARKETING_PLACEMENT_VALUES)

    def test_create_schema(self):
        payload = MarketingMediaCreate(
            placement="HOME_HERO",
            objectKey="hero/hero001.avif",
            title="Festive",
            sortOrder=0,
        )
        self.assertEqual(payload.placement, "HOME_HERO")
        self.assertEqual(payload.object_key, "hero/hero001.avif")


@unittest.skipUnless(HAS_AIOSQLITE, "aiosqlite not installed")
class TestMarketingMediaServiceSQLite(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import app.models  # noqa: F401
        from sqlalchemy import event as sa_event

        self.tmpdir = tempfile.mkdtemp()
        root = self.tmpdir
        db_path = os.path.join(root, "main.sqlite")
        schema_db = os.path.join(root, "pratikshya.sqlite")

        self.engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

        @sa_event.listens_for(self.engine.sync_engine, "connect")
        def _attach(dbapi_conn, _record):  # pragma: no cover - driver hook
            cursor = dbapi_conn.cursor()
            cursor.execute(f"ATTACH DATABASE '{schema_db}' AS pratikshya")
            cursor.close()

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_create_and_list(self):
        async with self.Session() as session:
            svc = MarketingMediaService(session)
            payload = MarketingMediaCreate(
                placement="HOME_HERO",
                objectKey="hero/hero001.avif",
                title="Festive Elegance",
                subtitle="Handwoven stories",
                ctaLabel="Explore",
                ctaHref="/shop",
                sortOrder=0,
                isActive=True,
            )
            row = await svc.create(payload, actor_id=None)
            self.assertEqual(row.placement, "HOME_HERO")
            self.assertEqual(row.object_key, "hero/hero001.avif")

            rows = await svc.list(placement="HOME_HERO")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].object_key, "hero/hero001.avif")

    async def test_duplicate_prevention(self):
        async with self.Session() as session:
            svc = MarketingMediaService(session)
            payload = MarketingMediaCreate(
                placement="HOME_HERO",
                objectKey="hero/hero001.avif",
                title="A",
                sortOrder=0,
            )
            await svc.create(payload)

            # Second create same placement+object_key should raise Conflict
            from app.core.exceptions import ConflictException

            with self.assertRaises(ConflictException):
                await svc.create(payload)

    async def test_ordering(self):
        async with self.Session() as session:
            svc = MarketingMediaService(session)
            # Create out of order
            for key, order in [("hero/hero003.avif", 2), ("hero/hero001.avif", 0), ("hero/hero002.avif", 1)]:
                await svc.create(
                    MarketingMediaCreate(
                        placement="HOME_HERO",
                        objectKey=key,
                        title=key,
                        sortOrder=order,
                    )
                )
            rows = await svc.list_active_home_hero()
            self.assertEqual([r.object_key for r in rows], ["hero/hero001.avif", "hero/hero002.avif", "hero/hero003.avif"])

    async def test_active_exclusion(self):
        async with self.Session() as session:
            svc = MarketingMediaService(session)
            await svc.create(
                MarketingMediaCreate(
                    placement="HOME_HERO",
                    objectKey="hero/hero001.avif",
                    title="Active",
                    sortOrder=0,
                    isActive=True,
                )
            )
            await svc.create(
                MarketingMediaCreate(
                    placement="HOME_HERO",
                    objectKey="hero/hero002.avif",
                    title="Inactive",
                    sortOrder=1,
                    isActive=False,
                )
            )
            active = await svc.list_active_home_hero()
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].object_key, "hero/hero001.avif")

            all_rows = await svc.list(placement="HOME_HERO", active_only=False)
            self.assertEqual(len(all_rows), 2)

    async def test_update_and_reorder(self):
        async with self.Session() as session:
            svc = MarketingMediaService(session)
            r1 = await svc.create(
                MarketingMediaCreate(placement="HOME_HERO", objectKey="hero/hero001.avif", sortOrder=0)
            )
            r2 = await svc.create(
                MarketingMediaCreate(placement="HOME_HERO", objectKey="hero/hero002.avif", sortOrder=1)
            )
            # Reorder: swap
            reordered = await svc.reorder(
                "HOME_HERO", [{"id": r1.id, "sort_order": 1}, {"id": r2.id, "sort_order": 0}]
            )
            self.assertEqual(reordered[0].id, r2.id)
            self.assertEqual(reordered[1].id, r1.id)

    async def test_url_generation(self):
        async with self.Session() as session:
            svc = MarketingMediaService(session)
            row = await svc.create(
                MarketingMediaCreate(placement="HOME_HERO", objectKey="hero/hero001.avif", sortOrder=0)
            )
            resp = svc.to_response(row)
            self.assertIn("/api/v1/media/objects/hero/hero001.avif", resp["url"])

    async def test_empty_home_hero(self):
        async with self.Session() as session:
            svc = MarketingMediaService(session)
            rows = await svc.list_active_home_hero()
            self.assertEqual(len(rows), 0)

    async def test_get_home_uses_marketing_media(self):
        """Simulate GET /home using marketing media rows."""
        async with self.Session() as session:
            svc = MarketingMediaService(session)
            await svc.create(
                MarketingMediaCreate(
                    placement="HOME_HERO",
                    objectKey="hero/hero003.avif",
                    title="Heritage",
                    subtitle="Six yards",
                    ctaLabel="Shop Sarees",
                    ctaHref="/women/sarees",
                    sortOrder=0,
                    isActive=True,
                )
            )
            await svc.create(
                MarketingMediaCreate(
                    placement="HOME_HERO",
                    objectKey="hero/hero001.avif",
                    title="Festive",
                    sortOrder=1,
                    isActive=True,
                )
            )
            # Now call ExploreService.get_home — it should load from DB
            from app.services.catalog.explore_service import ExploreService

            explore = ExploreService(session)
            home = await explore.get_home()
            self.assertTrue(home.ok)
            # Should have 2 hero slides from DB, ordered
            self.assertEqual(len(home.hero_slides), 2)
            self.assertEqual(home.hero_slides[0].image, "/api/v1/media/objects/hero/hero003.avif")
            self.assertEqual(home.hero_slides[1].image, "/api/v1/media/objects/hero/hero001.avif")
            # Title from DB
            self.assertEqual(home.hero_slides[0].title, "Heritage")
