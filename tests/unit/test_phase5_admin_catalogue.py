"""
Phase 5 — admin catalogue management regression tests.

Covers the backend half of the Phase 5 contract:
  • canonical product-ID creation (draft with `PF-<DEPT>-<FAM>-<SUB>-NNNN`),
  • the complete admin create/update persistence contract (full field set,
    PATCH exclude_unset semantics, lifecycle keys rejected, unknown keys
    ignored but never persisted),
  • lifecycle semantics (approve ≠ publish, gated publish, guarded
    unpublish/archive/restore, correct from→to history),
  • server-side search / filter / sort / pagination of the admin list,
  • bulk-update whitelist (no status writes),
  • category admin reads (all statuses + honest product counts) and the
    activate transitions,
  • offer/coupon admin surface (filters, 409 on duplicate codes, 422 on
    invalid windows, full eligibility contract in responses),
  • admin RBAC wiring (permissions enforced through the Phase-1 helpers,
    zero-role bootstrap compatibility, cross-surface tokens still blocked),
  • response-cache invalidation after catalogue mutations.

Style follows the Phase 1–4 unit suites: route coroutines and service
methods are invoked directly against SimpleNamespace stubs and AsyncMock
databases — no live server or PostgreSQL required.
"""

import inspect
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.exceptions import (
    BusinessLogicException,
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from app.schemas.catalog.product import (
    AdminProductListQuery,
    PRODUCT_ID_RE,
    ProductCreateRequest,
    ProductDraftRequest,
    ProductUpdateRequest,
)
from app.services.catalog.category_service import CategoryService
from app.services.catalog.product_service import ProductService


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

def full_admin_stub(**overrides):
    """A ProductModel-shaped stub carrying everything _to_admin reads."""
    base = dict(
        id="PF-K-BYS-TSH-0001",
        product_id="PF-K-BYS-TSH-0001",
        name="Banarasi Silk Blend Saree",
        slug="banarasi-silk-blend-saree",
        sku="PF-K-BYS-TSH-0001",
        brand="Pratikshya Fashon",
        product_type="fashion",
        product_code="",
        barcode="",
        internal_reference="",
        category="kidswear",
        subcategory="boys",
        gender="Boys",
        short_description="Short",
        description="A long enough description for the publish gate.",
        highlights=[],
        specifications={},
        care_instructions=[],
        delivery_info="",
        return_info="",
        return_policy=None,
        fabric="Silk blend",
        material="",
        primary_color="",
        secondary_color="",
        colors=[],
        patterns=[],
        work=[],
        occasion=[],
        sizes=[],
        unavailable_colors=[],
        unavailable_sizes=[],
        season="",
        fit="",
        length="",
        collection="",
        collections=[],
        tags=[],
        badges=[],
        is_featured=False,
        is_bestseller=False,
        is_new=False,
        is_limited_edition=False,
        is_trending=False,
        flags={},
        price=1499,
        original_price=None,
        compare_at_price=1999,
        currency="INR",
        pricing={"mrp": 1999, "sellingPrice": 1499, "discountType": "none", "discountValue": 0},
        price_history=[],
        stock=5,
        availability="in-stock",
        inventory_tracked=False,
        low_stock_threshold=5,
        rating=None,
        review_count=0,
        seo=None,
        status="DRAFT",
        published=False,
        review={"state": "NONE"},
        review_flags=[],
        assigned_employee_id=None,
        media_ids=[],
        primary_media_id=None,
        gallery_media_ids=[],
        image="/images/products/test.webp",
        hover_image="",
        additional_images=[],
        created_by="admin-1",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        updated_by=None,
        updated_at=None,
        published_by=None,
        published_at=None,
        history=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def category_stub(**overrides):
    """A `catalog_category` row as the taxonomy resolver reads it."""
    base = dict(id="cat-kidswear", slug="kidswear", name="Kidswear", status="ACTIVE")
    base.update(overrides)
    return SimpleNamespace(**base)


def subcategory_stub(**overrides):
    """A `catalog_subcategory` row as the taxonomy resolver reads it."""
    base = dict(
        id="cat-kidswear-boys",
        category_id="cat-kidswear",
        slug="boys",
        name="Boys",
        status="ACTIVE",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeScalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values

    def first(self):
        return self.values[0] if self.values else None


class FakeResult:
    def __init__(self, values, scalar_value=0):
        self.values = values
        self.scalar_value = scalar_value

    def scalars(self):
        return FakeScalars(self.values)

    def scalar(self):
        return self.scalar_value


class FakeDB:
    """Queue-based fake AsyncSession; extra results reuse the last item."""

    def __init__(self, results=None, product=None, flush_hook=None):
        self.results = list(results or [])
        self.product = product
        self.flush_hook = flush_hook
        self.added = []
        self.flushed = 0

    def _next(self):
        if self.results:
            return self.results.pop(0)
        return FakeResult([self.product] if self.product else [])

    async def execute(self, *args, **kwargs):
        return self._next()

    async def flush(self):
        self.flushed += 1
        if self.flush_hook:
            self.flush_hook()

    async def refresh(self, obj):
        return None

    def add(self, obj):
        self.added.append(obj)


def service_with(product, extra_results=None):
    db = FakeDB(results=extra_results, product=product)
    service = ProductService(db)
    # Cache invalidation performs best-effort redis work; replace with a
    # recording mock so tests can assert it fires without side effects.
    service.invalidate_product_cache = AsyncMock()
    return service, db


# ---------------------------------------------------------------------------
# Product ID contract (canonical ids must fit the String(36) primary key)
# ---------------------------------------------------------------------------

class ProductIdContractTests(unittest.TestCase):
    def test_canonical_id_accepted(self):
        self.assertTrue(PRODUCT_ID_RE.match("PF-K-BYS-TSH-0001"))
        self.assertTrue(PRODUCT_ID_RE.match("PF-SAR-001"))
        self.assertTrue(PRODUCT_ID_RE.match("A1"))

    def test_rejects_bad_ids(self):
        for bad in ("", "a-bad-lower", "-LEADING-DASH", "X" * 37):
            self.assertIsNone(PRODUCT_ID_RE.match(bad), bad)

    def test_draft_request_accepts_canonical_id(self):
        req = ProductDraftRequest(id="PF-K-BYS-TSH-0001", name="Test", category="kidswear")
        self.assertEqual(req.id, "PF-K-BYS-TSH-0001")

    def test_draft_request_rejects_lowercase_id(self):
        with self.assertRaises(ValueError):
            ProductDraftRequest(id="pf-k-bys-tsh-0001")


# ---------------------------------------------------------------------------
# Request schema — complete persistence contract (C-36 / C-37)
# ---------------------------------------------------------------------------

class ProductRequestSchemaTests(unittest.TestCase):
    def test_update_rejects_lifecycle_keys(self):
        for key in ("status", "published", "review", "history", "priceHistory"):
            with self.assertRaises(ValueError, msg=key):
                ProductUpdateRequest(**{"name": "ok", key: "PUBLISHED"})

    def test_update_ignores_unsupported_keys(self):
        req = ProductUpdateRequest(name="ok", variants=[{"size": "M"}], department="kids")
        dumped = req.model_dump(exclude_unset=True, by_alias=False)
        self.assertNotIn("variants", dumped)
        self.assertNotIn("department", dumped)
        self.assertEqual(dumped.get("name"), "ok")

    def test_camel_case_aliases_populate_model_fields(self):
        req = ProductUpdateRequest(
            **{
                "shortDescription": "hi",
                "compareAtPrice": 1999,
                "isFeatured": True,
                "primaryMediaId": "m-1",
                "careInstructions": ["Dry clean"],
            }
        )
        dumped = req.model_dump(exclude_unset=True, by_alias=False)
        self.assertEqual(dumped["short_description"], "hi")
        self.assertEqual(dumped["compare_at_price"], 1999)
        self.assertTrue(dumped["is_featured"])
        self.assertEqual(dumped["primary_media_id"], "m-1")
        self.assertEqual(dumped["care_instructions"], ["Dry clean"])

    def test_create_request_full_field_set(self):
        req = ProductCreateRequest(
            name="Saree", category="sarees", fabric="Georgette",
            material="Silk", tags=["wedding"], seo={"title": "t", "description": "d"},
            isBestseller=True, stock=4, mediaIds=["m1", "m2"],
            price="1299",  # string money is coerced, never rejected by accident
        )
        dumped = req.model_dump(exclude_unset=True, by_alias=False)
        for key in ("name", "category", "fabric", "material", "tags", "seo",
                    "is_bestseller", "stock", "media_ids", "price"):
            self.assertIn(key, dumped, key)
        self.assertEqual(dumped["price"], 1299)


# ---------------------------------------------------------------------------
# Admin list — search / filter / sort / pagination
# ---------------------------------------------------------------------------

class AdminListTests(unittest.IsolatedAsyncioTestCase):
    def _products(self):
        return [
            full_admin_stub(id="P1", name="Alpha", price=300, category="kidswear",
                            subcategory="boys", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
            full_admin_stub(id="P2", name="bravo", price=100, category="kidswear",
                            subcategory="girls", created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
                            status="PUBLISHED"),
            full_admin_stub(id="P3", name="Charlie", price=200, category="sarees",
                            subcategory="silk", created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
                            status="ARCHIVED"),
        ]

    async def test_total_is_full_filtered_count_not_page_count(self):
        rows = self._products()
        service, _ = service_with(None)
        service.db = FakeDB(results=[FakeResult([], scalar_value=3), FakeResult(rows)])
        result = await service.list_admin_products(
            AdminProductListQuery(category="kidswear", page=1, pageSize=1)
        )
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["page_size"], 1)

    async def test_sort_newest_and_oldest(self):
        rows = self._products()
        service, _ = service_with(None)
        service.db = FakeDB(results=[FakeResult([], scalar_value=3), FakeResult(rows)])
        newest = await service.list_admin_products(AdminProductListQuery(sort="newest", pageSize=10))
        self.assertEqual(newest["items"][0].id, "P3")
        service.db = FakeDB(results=[FakeResult([], scalar_value=3), FakeResult(rows)])
        oldest = await service.list_admin_products(AdminProductListQuery(sort="oldest", pageSize=10))
        self.assertEqual(oldest["items"][0].id, "P1")

    async def test_unknown_sort_falls_back_to_newest(self):
        query = AdminProductListQuery(sort="rm -rf")
        self.assertEqual(query.sort, "newest")

    async def test_page_beyond_range_returns_empty_items_not_error(self):
        rows = self._products()
        service, _ = service_with(None)
        service.db = FakeDB(results=[FakeResult([], scalar_value=3), FakeResult(rows)])
        result = await service.list_admin_products(AdminProductListQuery(page=99, pageSize=25))
        self.assertTrue(result["ok"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 3)


# ---------------------------------------------------------------------------
# Draft creation — the canonical admin creation path
# ---------------------------------------------------------------------------

class DraftCreationTests(unittest.IsolatedAsyncioTestCase):
    async def test_draft_persists_full_supported_payload(self):
        req = ProductDraftRequest(
            id="PF-K-BYS-TSH-0001",
            name="Boys Formal Shirt",
            category="kidswear",
            subcategory="boys",
            fabric="Cotton poplin",
            price="0",
            isFeatured=True,
            seo={"title": "Boys shirt", "description": "Kids formals"},
            mediaIds=["media-1"],
        )
        service, db = service_with(None)
        service.db = FakeDB(results=[
            FakeResult([]),          # id-collision probe: free
            FakeResult([category_stub()]),      # taxonomy: category resolves
            FakeResult([subcategory_stub()]),   # taxonomy: subcategory resolves
            FakeResult([]),          # slug probe: unique
            FakeResult([]),          # sku probe: unique
        ])
        service.db.add = lambda obj: db.added.append(obj)
        service.db.flush = db.flush
        admin = await service.create_draft(req, actor="admin-9")
        self.assertEqual(admin.id, "PF-K-BYS-TSH-0001")
        self.assertEqual(admin.status, "DRAFT")
        self.assertFalse(admin.published)
        created = db.added[0]
        self.assertEqual(created.product_id, "PF-K-BYS-TSH-0001")
        self.assertEqual(created.fabric, "Cotton poplin")
        self.assertEqual(created.seo, {"title": "Boys shirt", "description": "Kids formals"})
        self.assertTrue(created.is_featured)
        self.assertEqual(created.media_ids, ["media-1"])
        self.assertEqual(created.created_by, "admin-9")
        # derived flag mirror is consistent on creation
        self.assertTrue(created.flags["featured"])

    async def test_draft_taken_id_conflicts(self):
        req = ProductDraftRequest(id="PF-TAKEN-0001")
        taken = full_admin_stub(id="PF-TAKEN-0001")
        service, _ = service_with(taken)
        with self.assertRaises(ConflictException):
            await service.create_draft(req, actor="admin-1")

    async def test_create_also_persists_rich_fields(self):
        req = ProductCreateRequest(name="Fresh Product", category="sarees")
        service, db = service_with(None)
        service.db = FakeDB(results=[
            # taxonomy resolution runs before an id/slug/sku is consumed
            FakeResult([category_stub(id="cat-sarees", slug="sarees", name="Sarees")]),
            FakeResult([]),          # id collision probe: free
            FakeResult([]),          # slug probe
            FakeResult([]),          # sku probe
        ])
        service.db.add = lambda obj: db.added.append(obj)
        service.db.flush = db.flush
        admin = await service.create_product(req, actor="admin-1")
        self.assertEqual(admin.status, "DRAFT")
        created = db.added[0]
        self.assertEqual(created.brand, "Pratikshya Fashon")
        self.assertEqual(created.currency, "INR")


# ---------------------------------------------------------------------------
# PATCH semantics + server-computed pricing + history correctness
# ---------------------------------------------------------------------------

class AdminUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_provided_fields_are_written(self):
        p = full_admin_stub()
        service, _ = service_with(p)
        req = ProductUpdateRequest(name="Renamed Only")
        admin = await service.update_product(p.id, req, actor="admin-1")
        self.assertEqual(p.name, "Renamed Only")
        self.assertEqual(p.price, 1499)             # untouched
        self.assertEqual(p.fabric, "Silk blend")    # untouched
        self.assertEqual(admin.history[-1]["field"], "name")
        self.assertEqual(admin.history[-1]["from"], "Banarasi Silk Blend Saree")
        self.assertEqual(admin.history[-1]["to"], "Renamed Only")

    async def test_valid_pricing_block_recomputes_price_server_side(self):
        p = full_admin_stub()
        service, _ = service_with(p)
        req = ProductUpdateRequest.model_validate({
            "pricing": {"mrp": 2000, "sellingPrice": 1500,
                        "discountType": "percentage", "discountValue": 10},
        })
        await service.update_product(p.id, req, actor="admin-1")
        self.assertEqual(p.price, 1350)             # 1500 − 10%
        self.assertEqual(p.original_price, 2000)    # MRP above final → strikethrough

    async def test_incomplete_pricing_draft_save_does_not_zero_price(self):
        p = full_admin_stub()
        service, _ = service_with(p)
        req = ProductUpdateRequest.model_validate({"pricing": {"mrp": 0, "sellingPrice": 0}})
        await service.update_product(p.id, req, actor="admin-1")
        self.assertEqual(p.price, 1499)  # invalid pricing is stored but not applied

    async def test_price_history_records_old_to_new(self):
        p = full_admin_stub()
        service, _ = service_with(p)
        req = ProductUpdateRequest(price=999)
        await service.update_product(p.id, req, actor="admin-1")
        self.assertEqual(len(p.price_history), 1)
        entry = p.price_history[0]
        self.assertEqual(entry["from"], 1499)
        self.assertEqual(entry["to"], 999)

    async def test_partial_flag_patch_preserves_other_flags(self):
        p = full_admin_stub(is_bestseller=True, flags={"bestseller": True})
        service, _ = service_with(p)
        req = ProductUpdateRequest(isFeatured=True)
        await service.update_product(p.id, req, actor="admin-1")
        self.assertTrue(p.flags["bestseller"])
        self.assertTrue(p.flags["featured"])

    async def test_update_invalidates_response_cache(self):
        p = full_admin_stub()
        service, _ = service_with(p)
        req = ProductUpdateRequest(name="Cache Bust")
        await service.update_product(p.id, req, actor="admin-1")
        service.invalidate_product_cache.assert_awaited_once()


# ---------------------------------------------------------------------------
# Lifecycle semantics (C-29: review vs. publish separation)
# ---------------------------------------------------------------------------

class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_approve_marks_review_only_and_does_not_publish(self):
        p = full_admin_stub(status="PENDING_REVIEW", review={"state": "PENDING"})
        service, _ = service_with(p)
        admin = await service.approve_product(p.id, actor="admin-1")
        self.assertEqual(admin.review.state, "APPROVED")
        self.assertEqual(admin.status, "PENDING_REVIEW")
        self.assertFalse(admin.published)

    async def test_approve_requires_pending_review(self):
        p = full_admin_stub(status="DRAFT")
        service, _ = service_with(p)
        with self.assertRaises(BusinessLogicException):
            await service.approve_product(p.id, actor="admin-1")

    async def test_publish_requires_approved_review(self):
        p = full_admin_stub(status="DRAFT", review={"state": "NONE"})
        service, _ = service_with(p)
        with self.assertRaises(BusinessLogicException) as ctx:
            await service.publish_product(p.id, actor="admin-1")
        self.assertIn("approved", str(ctx.exception).lower())

    async def test_publish_after_approval_sets_all_published_fields(self):
        p = full_admin_stub(status="PENDING_REVIEW", review={"state": "APPROVED"})
        service, _ = service_with(p)
        admin = await service.publish_product(p.id, actor="admin-2")
        self.assertEqual(admin.status, "PUBLISHED")
        self.assertTrue(admin.published)
        self.assertEqual(admin.published_by, "admin-2")
        self.assertIsNotNone(admin.published_at)
        # history from→to is honest (the C-47 audit bug)
        status_entry = [h for h in admin.history if h["field"] == "status"][-1]
        self.assertEqual(status_entry["from"], "PENDING_REVIEW")
        self.assertEqual(status_entry["to"], "PUBLISHED")

    async def test_publish_blocks_on_incomplete_product(self):
        p = full_admin_stub(
            status="PENDING_REVIEW", review={"state": "APPROVED"},
            price=0, pricing=None, description="", short_description="", image="",
        )
        service, _ = service_with(p)
        with self.assertRaises(BusinessLogicException) as ctx:
            await service.publish_product(p.id, actor="admin-1")
        self.assertTrue(ctx.exception.details.get("errors"))

    async def test_unpublish_requires_published(self):
        p = full_admin_stub(status="DRAFT")
        service, _ = service_with(p)
        with self.assertRaises(BusinessLogicException):
            await service.unpublish_product(p.id, actor="admin-1")
        p2 = full_admin_stub(status="PUBLISHED", published=True)
        service2, _ = service_with(p2)
        admin = await service2.unpublish_product(p2.id, actor="admin-1")
        self.assertEqual(admin.status, "DRAFT")
        self.assertFalse(admin.published)

    async def test_archive_and_restore_guards(self):
        p = full_admin_stub(status="ARCHIVED")
        service, _ = service_with(p)
        with self.assertRaises(BusinessLogicException):
            await service.archive_product(p.id, actor="admin-1")
        with self.assertRaises(BusinessLogicException):
            await service.publish_product(p.id, actor="admin-1")
        admin = await service.restore_product(p.id, actor="admin-1")
        self.assertEqual(admin.status, "DRAFT")

    async def test_restore_requires_archived(self):
        p = full_admin_stub(status="DRAFT")
        service, _ = service_with(p)
        with self.assertRaises(BusinessLogicException):
            await service.restore_product(p.id, actor="admin-1")

    async def test_reject_requires_pending_and_returns_to_draft(self):
        from app.schemas.catalog.product import RejectProductRequest
        p = full_admin_stub(status="DRAFT")
        service, _ = service_with(p)
        with self.assertRaises(BusinessLogicException):
            await service.reject_product(p.id, RejectProductRequest(reason="nope"), actor="admin-1")
        p2 = full_admin_stub(status="PENDING_REVIEW", review={"state": "PENDING"})
        service2, _ = service_with(p2)
        admin = await service2.reject_product(p2.id, RejectProductRequest(reason="pricing"), actor="admin-1")
        self.assertEqual(admin.status, "DRAFT")
        self.assertEqual(admin.review.state, "REJECTED")
        self.assertEqual(admin.review.rejection_reason, "pricing")

    async def test_submit_review_requires_complete_record(self):
        p = full_admin_stub(sku="", category="", subcategory="")
        service, _ = service_with(p)
        with self.assertRaises(BusinessLogicException):
            await service.submit_for_review(p.id, actor="emp-1")


# ---------------------------------------------------------------------------
# Bulk update — merchandising flags only, status forbidden
# ---------------------------------------------------------------------------

class BulkUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_status_update_is_refused(self):
        from app.schemas.catalog.product import BulkUpdateRequest
        service, _ = service_with(None)
        req = BulkUpdateRequest(productIds=["P1"], updates={"status": "PUBLISHED"})
        with self.assertRaises(BusinessLogicException) as ctx:
            await service.bulk_update(req, actor="admin-1")
        self.assertIn("status", str(ctx.exception.details.get("rejected")))

    async def test_bulk_flags_apply_and_mirror(self):
        from app.schemas.catalog.product import BulkUpdateRequest
        p = full_admin_stub()
        service, _ = service_with(p)
        req = BulkUpdateRequest(productIds=[p.id], updates={"isFeatured": True, "isBestseller": True})
        result = await service.bulk_update(req, actor="admin-1")
        self.assertEqual(result["updatedCount"], 1)
        self.assertTrue(p.is_featured)
        self.assertTrue(p.flags["bestseller"])
        entry = [h for h in p.history if h["field"] == "is_featured"][-1]
        self.assertEqual(entry["from"], False)
        self.assertEqual(entry["to"], True)


# ---------------------------------------------------------------------------
# Duplicate — carries the supported field set
# ---------------------------------------------------------------------------

class DuplicateTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_copies_pricing_seo_flags_media_and_stock(self):
        p = full_admin_stub(is_trending=True, stock=7, seo={"title": "t"},
                            media_ids=["m1"], low_stock_threshold=2,
                            original_price=2500, return_policy={"window_days": 7})
        service, db = service_with(p)
        # _get_or_404 resolves from the queue first, then slug + sku probes.
        service.db = FakeDB(results=[FakeResult([p]), FakeResult([]), FakeResult([])], product=p)
        service.db.add = lambda obj: db.added.append(obj)
        service.db.flush = db.flush
        admin = await service.duplicate_product(p.id, actor="admin-1")
        dup = db.added[0]
        self.assertEqual(admin.status, "DRAFT")
        self.assertEqual(dup.price, 1499)
        self.assertEqual(dup.original_price, 2500)
        self.assertEqual(dup.stock, 7)
        self.assertEqual(dup.seo, {"title": "t"})
        self.assertEqual(dup.media_ids, ["m1"])
        self.assertEqual(dup.low_stock_threshold, 2)
        self.assertTrue(dup.is_trending)
        self.assertEqual(dup.return_policy, {"window_days": 7})


# ---------------------------------------------------------------------------
# Category admin reads + activation
# ---------------------------------------------------------------------------

def category_stub(**kw):
    base = dict(
        id="kidswear", slug="kidswear", name="Kidswear", eyebrow="", description="",
        image="", banner_media_id=None, status="ACTIVE", sort_order=1, featured=False,
        seo_title="", seo_description="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def subcategory_stub(**kw):
    base = dict(
        id="kidswear-boys", category_id="kidswear", slug="boys", name="Boys",
        description="", image="", status="ACTIVE", sort_order=1,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class CategoryAdminTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_list_includes_draft_and_archived_with_counts(self):
        rows = [category_stub(id="kidswear"), category_stub(id="draftcat", status="DRAFT")]
        db = FakeDB(results=[
            FakeResult(rows),
            FakeResult([], scalar_value=12),   # published count kidswear
            FakeResult([], scalar_value=0),    # published count draftcat
            FakeResult([], scalar_value=15),   # total count kidswear
            FakeResult([], scalar_value=2),    # total count draftcat
        ])
        service = CategoryService(db)
        items = await service.list_admin_categories(status_filter=None)
        self.assertEqual(len(items), 2)
        by_id = {row["id"]: row for row in items}
        self.assertEqual(by_id["kidswear"]["productCount"], 12)
        self.assertEqual(by_id["kidswear"]["productCountTotal"], 15)
        self.assertEqual(by_id["draftcat"]["productCount"], 0)
        self.assertEqual(by_id["draftcat"]["productCountTotal"], 2)
        self.assertEqual(by_id["draftcat"]["status"], "DRAFT")

    async def test_get_admin_category_resolves_non_active(self):
        db = FakeDB(results=[FakeResult([category_stub(id="draftcat", status="DRAFT")]),
                             FakeResult([], scalar_value=0)])
        service = CategoryService(db)
        cat = await service.get_admin_category("draftcat")
        self.assertEqual(cat.status, "DRAFT")

    async def test_activate_category_only_from_draft(self):
        cat = category_stub(id="draftcat", status="DRAFT")
        db = FakeDB(results=[FakeResult([cat]), FakeResult([], scalar_value=0)])
        service = CategoryService(db)
        service._invalidate_taxonomy_cache = AsyncMock()
        result = await service.activate_category("draftcat", actor="admin-1")
        self.assertEqual(result.status, "ACTIVE")
        service._invalidate_taxonomy_cache.assert_awaited_once()

        active_db = FakeDB(results=[FakeResult([category_stub(id="x", status="ACTIVE")])])
        with self.assertRaises(ConflictException):
            await CategoryService(active_db).activate_category("x", actor="admin-1")

    async def test_activate_subcategory_only_from_draft(self):
        sub = subcategory_stub(status="DRAFT")
        db = FakeDB(results=[FakeResult([sub]), FakeResult([], scalar_value=0)])
        service = CategoryService(db)
        service._invalidate_taxonomy_cache = AsyncMock()
        result = await service.activate_subcategory(sub.id, actor="admin-1")
        self.assertEqual(result.status, "ACTIVE")


# ---------------------------------------------------------------------------
# Offers / coupons admin contract
# ---------------------------------------------------------------------------

def coupon_stub(**kw):
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    base = dict(
        id="c-1", code="DIWALI25", name="Diwali 25", description="",
        discount_type="percentage", discount_value=25.0, minimum_order_value=1000,
        starts_at=now, expires_at=None, usage_limit=100, usage_count=5,
        per_customer_limit=1, is_stackable=False, is_active=True,
        eligible_customer_ids=None, eligible_product_ids=None,
        eligible_category_ids=None, eligible_collection_ids=None,
        excluded_product_ids=None, excluded_category_ids=None,
        created_at=now, updated_at=now,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class OfferContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_percentage_over_100_is_rejected(self):
        from app.api.v1.coupons import CreateCouponRequest, _validate_coupon_fields
        with self.assertRaises(BusinessLogicException) as ctx:
            _validate_coupon_fields(
                code="BIG", discount_type="percentage", discount_value=120,
                starts_at=None, expires_at=None,
            )
        self.assertTrue(any("100" in e for e in ctx.exception.details["errors"]))

    async def test_expiry_before_start_is_rejected(self):
        from app.api.v1.coupons import _validate_coupon_fields
        with self.assertRaises(BusinessLogicException) as ctx:
            _validate_coupon_fields(
                code="TIME", discount_type="fixed", discount_value=50,
                starts_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
                expires_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            )
        self.assertTrue(any("expires_at" in e for e in ctx.exception.details["errors"]))

    async def test_coupon_dict_exposes_full_eligibility(self):
        from app.api.v1.coupons import _coupon_to_dict
        data = _coupon_to_dict(coupon_stub(excluded_product_ids=["P1", "P2"]))
        self.assertEqual(data["excluded_product_ids"], ["P1", "P2"])
        self.assertEqual(data["eligible_category_ids"], [])
        self.assertEqual(data["display_status"], "ACTIVE")

    async def test_scheduled_and_expired_display_status(self):
        from app.api.v1.coupons import _coupon_to_dict
        future = datetime(2999, 1, 1, tzinfo=timezone.utc)
        past = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(
            _coupon_to_dict(coupon_stub(starts_at=future, expires_at=None))["display_status"],
            "SCHEDULED",
        )
        self.assertEqual(
            _coupon_to_dict(coupon_stub(starts_at=past, expires_at=past))["display_status"],
            "EXPIRED",
        )
        self.assertEqual(
            _coupon_to_dict(coupon_stub(is_active=False))["display_status"],
            "ARCHIVED",
        )

    async def test_duplicate_code_conflicts_on_create(self):
        from app.api.v1.coupons import CreateCouponRequest, admin_create_offer
        db = FakeDB(results=[FakeResult([coupon_stub()])])  # code already exists
        with self.assertRaises(ConflictException):
            with patch("app.api.v1.coupons.require_admin_permission", AsyncMock()):
                await admin_create_offer(
                    req=CreateCouponRequest(code="diwali25", discount_value=10),
                    current_user=SimpleNamespace(id="admin-1"),
                    db=db,
                )

    async def test_create_offer_happy_path_uppercases_code(self):
        from app.api.v1.coupons import CreateCouponRequest, admin_create_offer
        captured = {}

        def hook():
            # CouponModel instances default created/updated to None on
            # unflushed objects; stamp them like a real insert would.
            captured["obj"].created_at = datetime.now(timezone.utc)
            captured["obj"].updated_at = datetime.now(timezone.utc)

        class AddDB(FakeDB):
            def add(self, obj):
                captured["obj"] = obj
                obj.created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
                obj.updated_at = datetime(2026, 8, 1, tzinfo=timezone.utc)

        db = AddDB(results=[FakeResult([])])  # code free
        db.flush = AsyncMock()
        with patch("app.api.v1.coupons.require_admin_permission", AsyncMock()), \
             patch("app.api.v1.coupons.invalidate_response_cache", AsyncMock()):
            result = await admin_create_offer(
                req=CreateCouponRequest(
                    code="monsoon10", name="Monsoon", discount_type="fixed",
                    discount_value=100, minimum_order_value=500,
                    eligible_collection_ids=["col-1"],
                ),
                current_user=SimpleNamespace(id="admin-1"),
                db=db,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["offer"]["code"], "MONSOON10")
        self.assertEqual(result["offer"]["eligible_collection_ids"], ["col-1"])

    async def test_admin_list_filters_and_paginates(self):
        from app.api.v1.coupons import admin_list_offers
        coupons = [
            coupon_stub(id="c1", code="ALPHA", is_active=True),
            coupon_stub(id="c2", code="BETA", is_active=False),
            coupon_stub(id="c3", code="ALPHABET", is_active=True),
        ]
        db = FakeDB(results=[FakeResult(coupons)])
        with patch("app.api.v1.coupons.require_admin_permission", AsyncMock()):
            page = await admin_list_offers(
                q=None, status="ARCHIVED", page=1, pageSize=2,
                current_user=SimpleNamespace(id="a"), db=db,
            )
        self.assertEqual(page["total"], 1)              # derived-status filter
        self.assertEqual(page["offers"][0]["code"], "BETA")
        with patch("app.api.v1.coupons.require_admin_permission", AsyncMock()):
            db2 = FakeDB(results=[FakeResult(coupons)])
            all_page = await admin_list_offers(
                q=None, status=None, page=2, pageSize=2,
                current_user=SimpleNamespace(id="a"), db=db2,
            )
        self.assertEqual(all_page["total"], 3)          # full set, not the page
        self.assertEqual(len(all_page["offers"]), 1)    # remainder on page 2

    async def test_update_offer_patch_is_partial(self):
        from app.api.v1.coupons import UpdateCouponRequest, admin_update_offer
        coupon = coupon_stub()
        db = FakeDB(results=[FakeResult([coupon])])
        db.flush = AsyncMock()
        req = UpdateCouponRequest.model_validate({"discount_value": 30, "bogusKey": 1})
        with patch("app.api.v1.coupons.require_admin_permission", AsyncMock()), \
             patch("app.api.v1.coupons.invalidate_response_cache", AsyncMock()):
            result = await admin_update_offer(
                offer_id="c-1", req=req,
                current_user=SimpleNamespace(id="a"), db=db,
            )
        self.assertEqual(coupon.discount_value, 30)
        self.assertEqual(coupon.code, "DIWALI25")   # untouched
        self.assertFalse(hasattr(coupon, "bogusKey"))
        self.assertEqual(result["offer"]["discount_value"], 30)

    async def test_get_offer_missing_is_404(self):
        from app.api.v1.coupons import admin_get_offer
        db = FakeDB(results=[FakeResult([])])
        with patch("app.api.v1.coupons.require_admin_permission", AsyncMock()):
            with self.assertRaises(NotFoundException):
                await admin_get_offer(
                    offer_id="ghost", current_user=SimpleNamespace(id="a"), db=db
                )


# ---------------------------------------------------------------------------
# RBAC — Phase-1 permission helpers, admin surfaces only
# ---------------------------------------------------------------------------

class AdminRbacTests(unittest.IsolatedAsyncioTestCase):
    async def test_super_admin_bypasses_specific_permissions(self):
        from app.dependencies import require_admin_permission
        user = SimpleNamespace(id="u")
        with patch("app.dependencies.get_user_roles_and_permissions",
                   AsyncMock(return_value=(["SUPER_ADMIN"], []))):
            await require_admin_permission(user, AsyncMock(), "products.manage")

    async def test_role_missing_permission_is_forbidden(self):
        from app.dependencies import require_admin_permission
        user = SimpleNamespace(id="u")
        with patch("app.dependencies.get_user_roles_and_permissions",
                   AsyncMock(return_value=(["CATALOG_MANAGER"], ["products.view"]))):
            with self.assertRaises(ForbiddenException) as ctx:
                await require_admin_permission(user, AsyncMock(), "products.manage")
        self.assertIn("products.manage", str(ctx.exception))

    async def test_zero_role_admin_keeps_surface_access(self):
        # Documented bootstrap-compat path: admins exist before the role
        # directory is provisioned; surface isolation is enforced by
        # get_current_admin, not here.
        from app.dependencies import require_admin_permission
        user = SimpleNamespace(id="u")
        with patch("app.dependencies.get_user_roles_and_permissions",
                   AsyncMock(return_value=([], []))):
            await require_admin_permission(user, AsyncMock(), "offers.create")

    async def test_admin_routes_are_wired_to_permission_checks(self):
        expectations = {
            ("app.api.v1.products", "admin_update_product"): "products.manage",
            ("app.api.v1.products", "admin_list_products"): "products.view",
            ("app.api.v1.products", "admin_publish_product"): "products.manage",
            ("app.api.v1.coupons", "admin_archive_offer"): "offers.archive",
            ("app.api.v1.categories", "admin_create_category"): "categories.create",
            ("app.api.v1.categories", "admin_archive_category"): "categories.archive",
            ("app.api.v1.collections", "admin_assign_products"): "collections.assign",
            ("app.api.v1.collections", "admin_list_collections"): "collections.view",
        }
        for (module, fn), perm in expectations.items():
            source = inspect.getsource(getattr(__import__(module, fromlist=[fn]), fn))
            self.assertIn(
                f'"{perm}"', source,
                f"{module}.{fn} must enforce {perm}",
            )

    async def test_admin_route_functions_depend_on_surface_guard(self):
        # Token-scope isolation lives in get_current_admin (Phase 1): every
        # admin catalogue handler must take it as a dependency — a customer
        # or employee bearer token can never authenticate one of them.
        import fastapi
        for module, fn in (
            ("app.api.v1.products", "admin_update_product"),
            ("app.api.v1.products", "admin_list_products"),
            ("app.api.v1.categories", "admin_create_category"),
            ("app.api.v1.collections", "admin_assign_products"),
            ("app.api.v1.coupons", "admin_create_offer"),
        ):
            func = getattr(__import__(module, fromlist=[fn]), fn)
            deps = [
                call.call
                for call in getattr(func, "__fastapi_dependencies__", []) or []
            ]
            source = inspect.getsource(func)
            self.assertIn("get_current_admin", source, f"{module}.{fn} surface guard")


# ---------------------------------------------------------------------------
# Response cache helper
# ---------------------------------------------------------------------------

class ResponseCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalidate_calls_fastapi_cache_clear(self):
        from app.core.cache import invalidate_response_cache
        clear = AsyncMock()
        fake = MagicMock()
        fake.FastAPICache.clear = clear
        with patch.dict("sys.modules", {"fastapi_cache": fake}):
            await invalidate_response_cache()
        clear.assert_awaited_once()

    async def test_invalidate_swallows_failures(self):
        from app.core.cache import invalidate_response_cache
        fake = MagicMock()
        fake.FastAPICache.clear = AsyncMock(side_effect=RuntimeError("backend down"))
        with patch.dict("sys.modules", {"fastapi_cache": fake}):
            await invalidate_response_cache()  # must not raise


if __name__ == "__main__":
    unittest.main()
