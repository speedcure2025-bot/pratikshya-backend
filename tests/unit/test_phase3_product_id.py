"""
Phase 3 — server-authoritative product id allocation + Save & Continue flow.

Part A pins the reconciled `get_next_id` format (PF3-N15 / R4): the backend
is the single allocator and returns the canonical four-digit
`PF-{CODE}-{NNNN}` form, so the client's old local `PF-…-{NNNN}` convention
and the server's old three-digit `{CODE}-{NNN}` form collapse onto one
authority with one format.

Part B walks the real Save & Continue chain against the real routers, ORM
models and a real SQLite session (the same shims as the Phase 6/7 suites —
no PostgreSQL server is reachable in this sandbox):

    next-id  GET  /admin/products/next-id           → 200 {nextId}
    create   POST /admin/products/draft             → 201, DRAFT, published=false
    read     GET  /admin/products/{id}              → 200 (server retrievable)
    store    GET  /products                         → draft ABSENT (not visible)

The category/subcategory sent are server-shaped ids (exactly what the
editor now emits from the admin taxonomy surface). Since Block 2 the server
validates them against the real `catalog_category` / `catalog_subcategory`
rows, so the flow seeds the authoritative taxonomy first and asserts the
write path persists the canonical ids.
"""

import importlib
import os
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_cache import FastAPICache
from fastapi_cache.backends import Backend
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.schemas.catalog.product import PRODUCT_ID_RE
from app.services.catalog.product_service import ProductService

HAS_AIOSQLITE = importlib.util.find_spec("aiosqlite") is not None

# The server taxonomy the admin editor selects from — the ids the editor puts
# on the wire are these primary keys, not names or static slugs.
CATEGORY_ID = "6f1c2b3a-0000-4000-8000-0000000000c1"
SUBCATEGORY_ID = "7a9d0001-0000-4000-8000-0000000000c2"


# ---------------------------------------------------------------------------
# Test-only dialect shim (production code is not modified)
# ---------------------------------------------------------------------------

@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):  # pragma: no cover - dialect glue
    return "JSON"


# ---------------------------------------------------------------------------
# Part A — get_next_id format reconciliation (unit, no database)
# ---------------------------------------------------------------------------

class _Rows:
    """Iterable stand-in for the rows `get_next_id` scans."""

    def __init__(self, rows):
        self.rows = list(rows)

    def __iter__(self):
        return iter(self.rows)


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, stmt, *args, **kwargs):
        return self.rows


def _next_id(category, rows, preferred=None):
    service = ProductService(_FakeDB(_Rows(rows)))
    return service.get_next_id(category, preferred_number=preferred)


class NextIdFormatTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_register_starts_at_four_digit_0001(self):
        self.assertEqual(await _next_id("sarees", []), "PF-SAR-0001")

    async def test_skips_taken_serials_and_stays_four_digits(self):
        rows = [("PF-SAR-0001",), ("PF-SAR-0002",)]
        self.assertEqual(await _next_id("sarees", rows), "PF-SAR-0003")

    async def test_honours_free_preferred_number(self):
        rows = [("PF-SAR-0001",)]
        self.assertEqual(await _next_id("sarees", rows, preferred=7), "PF-SAR-0007")

    async def test_preferred_number_taken_falls_back_to_lowest_free(self):
        rows = [("PF-SAR-0001",)]
        self.assertEqual(await _next_id("sarees", rows, preferred=1), "PF-SAR-0002")

    async def test_unknown_category_gets_generic_prefix(self):
        self.assertEqual(await _next_id("28664436-3307-4174-87ca-21fbe3c3775b", []), "PF-GEN-0001")

    async def test_legacy_canonical_rows_keep_resolving_alongside_new_format(self):
        # A legacy frontend-allocated id lives under a different family prefix;
        # it must not collide with — nor break — the server sequence.
        rows = [("PF-W-SAR-SIL-0001",)]
        self.assertEqual(await _next_id("sarees", rows), "PF-SAR-0001")

    async def test_output_satisfies_canonical_id_regex(self):
        for category in ("sarees", "kidswear", "28664436-3307-4174-87ca-21fbe3c3775b"):
            next_id = await _next_id(category, [])
            self.assertIsNotNone(PRODUCT_ID_RE.match(next_id), next_id)


# ---------------------------------------------------------------------------
# Part B — Save & Continue end-to-end (real app on SQLite)
# ---------------------------------------------------------------------------

class _PassThroughCache(Backend):
    async def get_with_ttl(self, key):
        return 0, None

    async def get(self, key):
        return None

    async def set(self, key, value, expire=None):
        return None

    async def clear(self, namespace=None, key=None):
        return 0


@unittest.skipUnless(HAS_AIOSQLITE, "aiosqlite is not installed")
class SaveAndContinueFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        importlib.import_module("app.models")  # registers every mapped class
        from app.dependencies import get_current_user, get_db
        from app.models.auth.user import UserModel
        from app.models.base import Base
        from app.models.catalog.product import ProductModel

        self._UserModel = UserModel
        self._ProductModel = ProductModel

        from app.models.catalog.category import CategoryModel, SubcategoryModel

        self._CategoryModel = CategoryModel
        self._SubcategoryModel = SubcategoryModel

        self._tmp = tempfile.TemporaryDirectory(prefix="pf3-save-")
        self.root = self._tmp.name
        self.main_db = os.path.join(self.root, "main.sqlite")
        self.schema_db = os.path.join(self.root, "pratikshya.sqlite")

        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.main_db}")

        @event.listens_for(self.engine.sync_engine, "connect")
        def _attach(dbapi_conn, _record):  # pragma: no cover - driver hook
            cursor = dbapi_conn.cursor()
            cursor.execute(f"ATTACH DATABASE '{self.schema_db}' AS pratikshya")
            cursor.close()

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        # Seed a single admin (no role rows — the documented zero-role
        # compatibility path) so `require_admin_permission` is exercised
        # against the real RBAC joins, not patched out.
        async with self.Session() as session:
            admin = UserModel(
                email="pf3-admin@pratikshya.test",
                full_name="Phase 3 Admin",
                hashed_password="x",
                user_type="admin",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            session.add(admin)

            # The authoritative taxonomy the editor selects from. Both rows
            # are ACTIVE — the only status a product may be assigned to.
            session.add(
                CategoryModel(
                    id=CATEGORY_ID, name="Sarees", slug="sarees", status="ACTIVE"
                )
            )
            session.add(
                SubcategoryModel(
                    id=SUBCATEGORY_ID,
                    category_id=CATEGORY_ID,
                    name="Banarasi",
                    slug="banarasi",
                    status="ACTIVE",
                )
            )
            await session.commit()
            self.admin_id = admin.id

        self.app = self._build_app()
        self.client = TestClient(self.app)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    def _build_app(self):
        from app.api.v1.products import router as products_router
        from app.core.error_handlers import register_error_handlers
        from app.dependencies import get_current_user, get_db

        app = FastAPI()
        register_error_handlers(app)
        FastAPICache.init(backend=_PassThroughCache(), prefix="pf3-save-test")
        app.include_router(products_router, prefix="/api/v1")

        Session = self.Session

        async def _override_get_db():
            async with Session() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        async def _override_current_user():
            async with Session() as session:
                return (
                    await session.execute(
                        select(self._UserModel).where(self._UserModel.id == self.admin_id)
                    )
                ).scalars().first()

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_current_user
        return app

    def _db_row(self, product_id):
        import asyncio

        async def _read():
            async with self.Session() as session:
                return (
                    await session.execute(
                        select(self._ProductModel).where(self._ProductModel.id == product_id)
                    )
                ).scalars().first()

        return asyncio.get_event_loop().run_until_complete(_read())

    def test_save_and_continue_chain(self):
        category_id = CATEGORY_ID
        subcategory_id = SUBCATEGORY_ID

        # 1. Server allocates the id.
        next_res = self.client.get(f"/api/v1/admin/products/next-id?category={category_id}")
        self.assertEqual(next_res.status_code, 200, next_res.text)
        next_id = next_res.json()["nextId"]
        self.assertTrue(next_id.startswith("PF-"), next_id)

        # 2. The draft is POSTed under the server id with server taxonomy ids.
        create_res = self.client.post(
            "/api/v1/admin/products/draft",
            json={
                "id": next_id,
                "name": "Server Created Saree",
                "sku": f"SKU-{next_id}",
                "category": category_id,
                "subcategory": subcategory_id,
                "price": 7500,
                "description": "Created through the admin API for Phase 3.",
            },
        )
        self.assertEqual(create_res.status_code, 201, create_res.text)
        body = create_res.json()
        self.assertTrue(body["ok"])
        product = body["product"]
        self.assertEqual(product["id"], next_id)
        self.assertEqual(product["status"], "DRAFT")
        self.assertFalse(product["published"])
        self.assertEqual(product["category"], category_id)
        self.assertEqual(product["subcategory"], subcategory_id)

        # The durable row agrees with the response.
        row = self._db_row(next_id)
        self.assertEqual(row.status, "DRAFT")
        self.assertFalse(row.published)
        self.assertEqual(row.category, category_id)
        self.assertEqual(row.subcategory, subcategory_id)

        # 3. The subsequent edit flow can retrieve it from the server.
        get_res = self.client.get(f"/api/v1/admin/products/{next_id}")
        self.assertEqual(get_res.status_code, 200, get_res.text)
        self.assertEqual(get_res.json()["product"]["id"], next_id)

        # 4. It is NOT visible in the storefront (DRAFT, published=False).
        store_res = self.client.get("/api/v1/products")
        self.assertEqual(store_res.status_code, 200, store_res.text)
        visible_ids = [item["id"] for item in store_res.json().get("items", [])]
        self.assertNotIn(next_id, visible_ids)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
