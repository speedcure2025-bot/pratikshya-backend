"""
Taxonomy & Catalog Foundation Contract Tests (Phase 2).

Tests for:
  - Category / Subcategory / Collection schema aliases (camelCase + snake_case)
  - Lifecycle state transitions (DRAFT -> ACTIVE -> ARCHIVED -> DRAFT/ACTIVE)
  - Duplicate slug 409 ConflictException
  - Collection date window validation (endDate >= startDate, 422 on invalid)
  - Collection type and status enums
  - Dedicated restore endpoints for categories, subcategories, and collections
  - Storefront ACTIVE-only vs. Admin all-status visibility
  - Live taxonomy metrics and product counts
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    NotFoundException,
)
from app.schemas.catalog.category import (
    CategoryCreateRequest,
    CategoryResponse,
    CategoryUpdateRequest,
    SubcategoryCreateRequest,
    SubcategoryResponse,
    SubcategoryUpdateRequest,
)
from app.schemas.catalog.collection import (
    CollectionCreateRequest,
    CollectionResponse,
    CollectionStatusEnum,
    CollectionTypeEnum,
    CollectionUpdateRequest,
)
from app.services.catalog.category_service import CategoryService
from app.services.catalog.collection_service import CollectionService


# ---------------------------------------------------------------------------
# Test Helpers & Stubs
# ---------------------------------------------------------------------------

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

    def __iter__(self):
        return iter(self.values)

    def scalars(self):
        return FakeScalars(self.values)

    def scalar(self):
        return self.scalar_value


class FakeDB:
    def __init__(self, results=None, default_result=None):
        self.results = list(results or [])
        self.default_result = default_result
        self.statements = []
        self.added = []
        self.flushed = 0

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(str(stmt))
        if self.results:
            return self.results.pop(0)
        if self.default_result is not None:
            return self.default_result
        return FakeResult([])

    async def flush(self):
        self.flushed += 1

    async def refresh(self, obj):
        return None

    def add(self, obj):
        self.added.append(obj)


def make_cat_stub(**kw):
    base = dict(
        id="cat-01",
        name="Sarees",
        slug="sarees",
        eyebrow="Traditional",
        description="Silk sarees",
        image="saree.jpg",
        banner_media_id="med-banner",
        status="DRAFT",
        sort_order=10,
        featured=True,
        seo_title="Silk Sarees",
        seo_description="Buy silk sarees",
        created_by="admin-1",
        updated_by="admin-1",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def make_sub_stub(**kw):
    base = dict(
        id="cat-01-banarasi",
        category_id="cat-01",
        name="Banarasi",
        slug="banarasi",
        description="Banarasi silk",
        image="banarasi.jpg",
        status="DRAFT",
        sort_order=5,
        created_by="admin-1",
        updated_by="admin-1",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def make_col_stub(**kw):
    base = dict(
        id="col-01",
        name="Wedding Edit",
        slug="wedding-edit",
        eyebrow="Festive",
        description="Bridal and wedding",
        image="wedding.jpg",
        hero_media_id="med-hero",
        thumbnail_media_id="med-thumb",
        type="MANUAL",
        status="DRAFT",
        featured=True,
        sort_order=20,
        start_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 9, 30, tzinfo=timezone.utc),
        explicit_product_ids=["prod-1", "prod-2"],
        rule={},
        created_by="admin-1",
        updated_by="admin-1",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Test Suite
# ---------------------------------------------------------------------------

class TaxonomyContractTests(unittest.IsolatedAsyncioTestCase):

    # ── Category Contract ───────────────────────────────────────────────────

    def test_category_create_request_accepts_camelcase_and_snake_case(self):
        # camelCase
        req1 = CategoryCreateRequest.model_validate({
            "name": "Lehengas",
            "bannerMediaId": "med-1",
            "sortOrder": 15,
            "seoTitle": "Lehengas Online",
            "seoDescription": "Exclusive lehengas",
            "imageUrl": "https://img.com/leh.jpg",
        })
        self.assertEqual(req1.banner_media_id, "med-1")
        self.assertEqual(req1.sort_order, 15)
        self.assertEqual(req1.seo_title, "Lehengas Online")
        self.assertEqual(req1.image, "https://img.com/leh.jpg")

        # snake_case
        req2 = CategoryCreateRequest.model_validate({
            "name": "Lehengas",
            "banner_media_id": "med-2",
            "sort_order": 25,
            "seo_title": "Lehengas Title",
            "image": "https://img.com/leh2.jpg",
        })
        self.assertEqual(req2.banner_media_id, "med-2")
        self.assertEqual(req2.sort_order, 25)
        self.assertEqual(req2.image, "https://img.com/leh2.jpg")

    def test_category_update_request_accepts_camelcase_aliases(self):
        req = CategoryUpdateRequest.model_validate({
            "bannerMediaId": "med-patch",
            "sortOrder": 50,
            "seoTitle": "New Title",
        })
        self.assertEqual(req.banner_media_id, "med-patch")
        self.assertEqual(req.sort_order, 50)
        self.assertEqual(req.seo_title, "New Title")

    def test_category_response_serializes_with_camelcase_aliases(self):
        stub = make_cat_stub()
        res = CategoryResponse.model_validate(stub)
        dump = res.model_dump(by_alias=True)
        self.assertIn("bannerMediaId", dump)
        self.assertIn("sortOrder", dump)
        self.assertIn("seoTitle", dump)
        self.assertIn("seoDescription", dump)
        self.assertEqual(dump["bannerMediaId"], "med-banner")

    async def test_category_creation_starts_as_draft(self):
        db = FakeDB(results=[
            FakeResult([]),  # slug check (unique)
            FakeResult([], scalar_value=0),  # product count
        ])
        srv = CategoryService(db)
        req = CategoryCreateRequest(name="Anarkali")
        res = await srv.create_category(req, actor="admin-1")
        self.assertEqual(res.status, "DRAFT")
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0].status, "DRAFT")

    async def test_category_slug_conflict_raises_409(self):
        db = FakeDB(results=[
            FakeResult([SimpleNamespace(id="existing-cat")]),  # slug collision
        ])
        srv = CategoryService(db)
        req = CategoryCreateRequest(name="Sarees", slug="sarees")
        with self.assertRaises(ConflictException) as ctx:
            await srv.create_category(req, actor="admin-1")
        self.assertIn("already exists", str(ctx.exception))

    async def test_category_lifecycle_transitions(self):
        cat = make_cat_stub(status="DRAFT")
        db = FakeDB(results=[
            FakeResult([cat]), FakeResult([], scalar_value=0),  # activate
            FakeResult([cat]), FakeResult([], scalar_value=0),  # archive
            FakeResult([cat]), FakeResult([], scalar_value=0),  # restore
        ])
        srv = CategoryService(db)

        # 1. Activate: DRAFT -> ACTIVE
        act = await srv.activate_category("cat-01", actor="admin-1")
        self.assertEqual(act.status, "ACTIVE")

        # 2. Archive: ACTIVE -> ARCHIVED
        arch = await srv.archive_category("cat-01", actor="admin-1")
        self.assertEqual(arch.status, "ARCHIVED")

        # 3. Dedicated Restore: ARCHIVED -> ACTIVE (restores to active catalog)
        rest = await srv.restore_category("cat-01", actor="admin-1")
        self.assertEqual(rest.status, "ACTIVE")

    # ── Subcategory Contract ─────────────────────────────────────────────────

    def test_subcategory_schemas_camelcase_and_aliases(self):
        req = SubcategoryCreateRequest.model_validate({
            "name": "Kanjeevaram",
            "imageUrl": "https://img.com/kanj.jpg",
            "sortOrder": 3,
        })
        self.assertEqual(req.image, "https://img.com/kanj.jpg")
        self.assertEqual(req.sort_order, 3)

        sub_stub = make_sub_stub()
        res = SubcategoryResponse.model_validate(sub_stub)
        dump = res.model_dump(by_alias=True)
        self.assertIn("categoryId", dump)
        self.assertIn("sortOrder", dump)
        self.assertEqual(dump["categoryId"], "cat-01")

    async def test_subcategory_dedicated_restore_endpoint(self):
        sub = make_sub_stub(status="ARCHIVED")
        db = FakeDB(results=[
            FakeResult([sub]), FakeResult([], scalar_value=0),
        ])
        srv = CategoryService(db)
        res = await srv.restore_subcategory("cat-01-banarasi", actor="admin-1")
        self.assertEqual(res.status, "ACTIVE")
        self.assertEqual(sub.status, "ACTIVE")

    # ── Collection Contract & Date Validation ───────────────────────────────

    def test_collection_create_accepts_camelcase_and_valid_dates(self):
        req = CollectionCreateRequest.model_validate({
            "name": "Summer Edit",
            "startDate": "2026-06-01T00:00:00Z",
            "endDate": "2026-06-30T00:00:00Z",
            "heroMediaId": "med-hero",
            "type": "MANUAL",
            "sortOrder": 10,
        })
        self.assertEqual(req.hero_media_id, "med-hero")
        self.assertEqual(req.type, CollectionTypeEnum.MANUAL)
        self.assertEqual(req.sort_order, 10)

    def test_collection_date_validation_rejects_end_date_before_start_date(self):
        with self.assertRaises(ValidationError) as ctx:
            CollectionCreateRequest.model_validate({
                "name": "Winter Edit",
                "startDate": "2026-12-31T00:00:00Z",
                "endDate": "2026-12-01T00:00:00Z",
            })
        errs = ctx.exception.errors()
        self.assertTrue(any("endDate must be greater than or equal to startDate" in e["msg"] for e in errs))

    def test_collection_update_date_validation(self):
        with self.assertRaises(ValidationError) as ctx:
            CollectionUpdateRequest.model_validate({
                "startDate": "2026-12-31T00:00:00Z",
                "endDate": "2026-12-01T00:00:00Z",
            })
        errs = ctx.exception.errors()
        self.assertTrue(any("endDate must be greater than or equal to startDate" in e["msg"] for e in errs))

    async def test_collection_update_effective_dates_validation_in_service(self):
        col = make_col_stub(
            start_date=datetime(2026, 9, 10, tzinfo=timezone.utc),
            end_date=datetime(2026, 9, 20, tzinfo=timezone.utc),
        )
        db = FakeDB(results=[FakeResult([col])])
        srv = CollectionService(db)

        # Attempt to move start_date past existing end_date
        patch = CollectionUpdateRequest(
            start_date=datetime(2026, 9, 25, tzinfo=timezone.utc)
        )
        with self.assertRaises(BusinessLogicException) as ctx:
            await srv.update_collection("col-01", patch, actor="admin-1")
        self.assertIn("endDate must be greater than or equal to startDate", str(ctx.exception))

    async def test_collection_lifecycle_transitions(self):
        col = make_col_stub(status="DRAFT")
        db = FakeDB(results=[
            FakeResult([col]), FakeResult([]),  # activate (get + count)
            FakeResult([col]), FakeResult([]),  # pause (get + count)
            FakeResult([col]),                  # archive (get)
            FakeResult([col]),                  # restore (get)
        ])
        srv = CollectionService(db)

        # 1. Activate: DRAFT -> ACTIVE
        act = await srv.activate_collection("col-01", actor="admin-1")
        self.assertEqual(act.status, "ACTIVE")

        # 2. Pause: ACTIVE -> PAUSED
        pau = await srv.pause_collection("col-01", actor="admin-1")
        self.assertEqual(pau.status, "PAUSED")

        # 3. Archive: PAUSED -> ARCHIVED
        arc = await srv.archive_collection("col-01", actor="admin-1")
        self.assertEqual(arc.status, "ARCHIVED")

        # 4. Dedicated Restore: ARCHIVED -> DRAFT
        rst = await srv.restore_collection("col-01", actor="admin-1")
        self.assertEqual(rst.status, "DRAFT")

    # ── Taxonomy Metrics & Product Counts ───────────────────────────────────

    async def test_taxonomy_metrics_and_product_counts(self):
        db = FakeDB(results=[
            FakeResult([("ACTIVE", 3), ("DRAFT", 2)]),  # collection counts by status
            FakeResult([], scalar_value=5),              # category total
            FakeResult([], scalar_value=12),             # subcategory total
        ])
        srv = CollectionService(db)
        metrics = await srv.taxonomy_metrics()
        self.assertTrue(metrics["ok"])
        self.assertEqual(metrics["categories"]["total"], 5)
        self.assertEqual(metrics["subcategories"]["total"], 12)
        self.assertEqual(metrics["collections"]["total"], 5)
        self.assertEqual(metrics["collections"]["byStatus"]["ACTIVE"], 3)


if __name__ == "__main__":
    unittest.main()
