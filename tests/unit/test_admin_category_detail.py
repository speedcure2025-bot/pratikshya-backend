"""
Admin category detail — lifecycle-agnostic read contract.

Regression suite for the "Category not found" bug on the admin edit desk:
a DRAFT category (e.g. one just created — categories are born DRAFT) could
not be opened for editing because the desk resolved it from the STOREFRONT
collection `GET /categories?status=ACTIVE`, which by design never carries
DRAFT rows.

The frontend fix routes the edit desk at `GET /admin/categories/{id}`.
These tests pin the backend half of that contract so it cannot regress:

  CASE 1  admin detail returns a DRAFT category (200, status preserved)
  CASE 2  admin detail returns an ACTIVE category (200)
  CASE 4  the storefront list keeps its ACTIVE filter; the admin list does not
  CASE 5  reading a DRAFT category never promotes/mutates it
  CASE 6  an unknown id is a genuine NotFound on both surfaces

Style follows the Phase 1–5 unit suites: routes and services are invoked
directly against SimpleNamespace stubs and a queue-based fake session — no
live server and no PostgreSQL.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.v1 import categories as categories_api
from app.core.exceptions import NotFoundException
from app.services.catalog.category_service import CategoryService


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

DRAFT_ID = "28664436-3307-4174-87ca-21fbe3c3775b"


def category_stub(**kw):
    base = dict(
        id=DRAFT_ID, slug="sarees", name="Sarees", eyebrow="", description="",
        image="", banner_media_id=None, status="DRAFT", sort_order=1, featured=False,
        seo_title="", seo_description="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class FakeScalars:
    def __init__(self, values):
        self.values = list(values)

    def first(self):
        return self.values[0] if self.values else None

    def all(self):
        return list(self.values)


class FakeResult:
    def __init__(self, values, scalar_value=0):
        self.values = values
        self.scalar_value = scalar_value

    def scalars(self):
        return FakeScalars(self.values)

    def scalar(self):
        return self.scalar_value


class FakeDB:
    """Queue-based fake AsyncSession that records every statement it ran."""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.statements = []
        self.added = []
        self.flushed = 0

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(str(stmt))
        return self.results.pop(0) if self.results else FakeResult([])

    async def flush(self):
        self.flushed += 1

    async def refresh(self, obj):
        return None

    def add(self, obj):
        self.added.append(obj)


def admin_user():
    return SimpleNamespace(id="admin-1", email="admin@pratikshya.test")


class _Permission:
    """Patches the RBAC gate for route-level calls."""

    def __enter__(self):
        self._original = categories_api.require_admin_permission
        categories_api.require_admin_permission = AsyncMock(return_value=True)
        return categories_api.require_admin_permission

    def __exit__(self, *exc):
        categories_api.require_admin_permission = self._original
        return False


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

class AdminCategoryDetailTests(unittest.IsolatedAsyncioTestCase):

    async def test_case1_admin_detail_route_returns_draft_category(self):
        db = FakeDB(results=[FakeResult([category_stub()]), FakeResult([], scalar_value=0)])
        with _Permission() as gate:
            response = await categories_api.admin_get_category(
                category_id=DRAFT_ID, current_user=admin_user(), db=db
            )
        gate.assert_awaited_once()
        self.assertEqual(response.category.id, DRAFT_ID)
        self.assertEqual(response.category.name, "Sarees")
        self.assertEqual(response.category.slug, "sarees")
        self.assertEqual(response.category.status, "DRAFT")
        self.assertTrue(response.ok)

    async def test_case2_admin_detail_route_returns_active_category(self):
        db = FakeDB(results=[FakeResult([category_stub(status="ACTIVE")]),
                             FakeResult([], scalar_value=3)])
        with _Permission():
            response = await categories_api.admin_get_category(
                category_id=DRAFT_ID, current_user=admin_user(), db=db
            )
        self.assertEqual(response.category.status, "ACTIVE")
        self.assertEqual(response.category.productCount, 3)

    async def test_admin_detail_resolves_by_slug_too(self):
        db = FakeDB(results=[FakeResult([category_stub(status="ARCHIVED")]),
                             FakeResult([], scalar_value=0)])
        cat = await CategoryService(db).get_admin_category("sarees")
        self.assertEqual(cat.status, "ARCHIVED", "any lifecycle state is readable by admins")

    async def test_admin_detail_query_carries_no_status_filter(self):
        db = FakeDB(results=[FakeResult([category_stub()]), FakeResult([], scalar_value=0)])
        await CategoryService(db).get_admin_category(DRAFT_ID)
        resolver_sql = db.statements[0]
        self.assertNotIn("status", resolver_sql.lower().split("where")[-1],
                         "the admin resolver must not filter on status")

    async def test_case4_storefront_list_filters_active_admin_list_does_not(self):
        # Storefront: the ACTIVE filter reaches SQL and DRAFT rows never load.
        storefront_db = FakeDB(results=[FakeResult([]), FakeResult([], scalar_value=0)])
        await CategoryService(storefront_db).list_categories(status_filter="ACTIVE")
        self.assertIn("status", storefront_db.statements[0].lower())

        # Admin: no status predicate — DRAFT and ARCHIVED rows are returned.
        admin_db = FakeDB(results=[
            FakeResult([category_stub(id="c1", status="DRAFT"),
                        category_stub(id="c2", slug="kidswear", status="ACTIVE")]),
            FakeResult([], scalar_value=0), FakeResult([], scalar_value=0),
            FakeResult([], scalar_value=0), FakeResult([], scalar_value=0),
        ])
        items = await CategoryService(admin_db).list_admin_categories(status_filter=None)
        self.assertEqual({row["status"] for row in items}, {"DRAFT", "ACTIVE"})
        admin_sql = admin_db.statements[0].lower()
        self.assertNotIn("where", admin_sql, "the unfiltered admin list adds no status predicate")

    async def test_case4b_public_detail_hides_non_active_records(self):
        db = FakeDB(results=[FakeResult([category_stub(status="DRAFT")])])
        with self.assertRaises(NotFoundException):
            await CategoryService(db).get_category(DRAFT_ID)

    async def test_case5_reading_a_draft_category_never_promotes_it(self):
        row = category_stub()
        db = FakeDB(results=[FakeResult([row]), FakeResult([], scalar_value=0)])
        cat = await CategoryService(db).get_admin_category(DRAFT_ID)
        self.assertEqual(cat.status, "DRAFT")
        self.assertEqual(row.status, "DRAFT", "the persisted row is untouched by a read")
        self.assertEqual(db.flushed, 0, "a read issues no writes")
        self.assertEqual(db.added, [])

    async def test_case6_unknown_id_is_a_real_not_found(self):
        db = FakeDB(results=[FakeResult([])])
        with self.assertRaises(NotFoundException):
            await CategoryService(db).get_admin_category("does-not-exist")

        public_db = FakeDB(results=[FakeResult([])])
        with self.assertRaises(NotFoundException):
            await CategoryService(public_db).get_category("does-not-exist")


if __name__ == "__main__":
    unittest.main()
