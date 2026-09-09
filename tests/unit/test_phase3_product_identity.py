"""
Phase 3 Block 3 — SKU / slug uniqueness and create-path correctness.

Two defects are closed here, both from the plan:

  PF3-N03  duplicate SKUs were accepted (two rows, both 201) because nothing
           ever probed the column — `ix_catalog_product_sku` is a NON-unique
           index and no service check existed.

  PF3-N04  `POST /admin/products` silently DISCARDED a supplied slug, while
           `POST /admin/products/draft` honoured it but silently suffixed it
           with `-1`/`-2` on collision. The two create paths disagreed and
           neither told the caller.

The contract asserted here (plan §16.2, §22.1, §25.7-25.8):

  * a duplicate SKU or slug is HTTP 409 `CONFLICT` in the canonical Phase 1
    envelope — never a 200/201, never a silent rename;
  * `details` carries `{field, value}` for a SKU and `{field, value,
    suggestedSlug}` for a slug, so the caller can retry deterministically;
  * a supplied slug/sku is stored VERBATIM on BOTH create paths; generation
    happens only when the caller supplied nothing;
  * a product may always keep its own SKU/slug on PATCH;
  * comparison is whitespace-trimmed and case-insensitive;
  * a rejected write creates and mutates nothing.

Runs against the REAL routers, service and ORM on a throwaway SQLite file.
Block 2 taxonomy validation is live, so the authoritative category is seeded.
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

HAS_AIOSQLITE = importlib.util.find_spec("aiosqlite") is not None


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):  # pragma: no cover - dialect glue
    return "JSON"


CATEGORY_ID = "cat-sarees"
SUBCATEGORY_ID = "cat-sarees-banarasi"


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
class ProductIdentityCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        importlib.import_module("app.models")
        from app.models.auth.user import UserModel
        from app.models.base import Base
        from app.models.catalog.category import CategoryModel, SubcategoryModel
        from app.models.catalog.product import ProductModel

        self._UserModel = UserModel
        self._ProductModel = ProductModel

        self._tmp = tempfile.TemporaryDirectory(prefix="pf3-identity-")
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

        async with self.Session() as session:
            admin = UserModel(
                email="pf3-identity-admin@pratikshya.test",
                full_name="Identity Admin",
                hashed_password="x",
                user_type="admin",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            session.add(admin)
            session.add(
                CategoryModel(id=CATEGORY_ID, name="Sarees", slug="sarees", status="ACTIVE")
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
        FastAPICache.init(backend=_PassThroughCache(), prefix="pf3-identity-test")
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

    # ── helpers ──────────────────────────────────────────────────────────────

    def draft(self, product_id, **overrides):
        payload = {
            "id": product_id,
            "name": f"Product {product_id}",
            "category": CATEGORY_ID,
            "subcategory": SUBCATEGORY_ID,
            "price": 4999,
        }
        payload.update(overrides)
        return self.client.post("/api/v1/admin/products/draft", json=payload)

    def runtime(self, **overrides):
        payload = {
            "name": "Runtime Product",
            "category": CATEGORY_ID,
            "subcategory": SUBCATEGORY_ID,
        }
        payload.update(overrides)
        return self.client.post("/api/v1/admin/products", json=payload)

    def patch(self, product_id, body):
        return self.client.patch(f"/api/v1/admin/products/{product_id}", json=body)

    def row(self, product_id):
        import asyncio

        async def _read():
            async with self.Session() as session:
                return (
                    await session.execute(
                        select(self._ProductModel).where(
                            self._ProductModel.id == product_id
                        )
                    )
                ).scalars().first()

        return asyncio.get_event_loop().run_until_complete(_read())

    def rows(self):
        import asyncio

        async def _read():
            async with self.Session() as session:
                return (
                    await session.execute(select(self._ProductModel))
                ).scalars().all()

        return asyncio.get_event_loop().run_until_complete(_read())

    def assert_conflict(self, response, field, value):
        """Canonical Phase 1 409 — one envelope, no second format."""
        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        self.assertIs(body["success"], False)
        self.assertEqual(body["error"]["code"], "CONFLICT")
        self.assertIn(value, body["error"]["message"])
        details = body["error"]["details"]
        self.assertIsInstance(details, dict)
        self.assertEqual(details["field"], field)
        self.assertEqual(details["value"], value)
        rendered = response.text.lower()
        for leak in ("traceback", "select ", "sqlalchemy", "integrityerror", "sqlite"):
            self.assertNotIn(leak, rendered, leak)
        return body


# ===========================================================================
# SKU uniqueness
# ===========================================================================

class SkuUniquenessTests(ProductIdentityCase):
    def test_duplicate_sku_on_draft_create_is_409(self):
        self.assertEqual(self.draft("PF-SKU-0001", sku="EXPLICIT-SKU-1").status_code, 201)
        second = self.draft("PF-SKU-0002", sku="EXPLICIT-SKU-1")
        self.assert_conflict(second, "sku", "EXPLICIT-SKU-1")
        self.assertIsNone(self.row("PF-SKU-0002"), "the rejected create wrote no row")
        self.assertEqual(len([r for r in self.rows() if r.sku == "EXPLICIT-SKU-1"]), 1)

    def test_duplicate_sku_on_runtime_create_is_409(self):
        first = self.runtime(sku="EXPLICIT-SKU-2", slug="runtime-one")
        self.assertEqual(first.status_code, 201, first.text)
        before = len(self.rows())
        second = self.runtime(sku="EXPLICIT-SKU-2", slug="runtime-two")
        self.assert_conflict(second, "sku", "EXPLICIT-SKU-2")
        self.assertEqual(len(self.rows()), before, "no row may be created")

    def test_duplicate_sku_on_patch_is_409_and_writes_nothing(self):
        self.assertEqual(self.draft("PF-SKU-0003", sku="SKU-AAA").status_code, 201)
        self.assertEqual(self.draft("PF-SKU-0004", sku="SKU-BBB").status_code, 201)

        response = self.patch("PF-SKU-0004", {"sku": "SKU-AAA", "name": "Should not land"})
        self.assert_conflict(response, "sku", "SKU-AAA")

        row = self.row("PF-SKU-0004")
        self.assertEqual(row.sku, "SKU-BBB", "the SKU is untouched")
        self.assertNotEqual(row.name, "Should not land", "no field of a rejected patch lands")

    def test_a_product_may_keep_its_own_sku_on_patch(self):
        self.assertEqual(self.draft("PF-SKU-0005", sku="SKU-OWN").status_code, 201)
        response = self.patch("PF-SKU-0005", {"sku": "SKU-OWN", "name": "Renamed"})
        self.assertEqual(response.status_code, 200, response.text)
        row = self.row("PF-SKU-0005")
        self.assertEqual(row.sku, "SKU-OWN")
        self.assertEqual(row.name, "Renamed")

    def test_sku_comparison_is_case_insensitive_and_whitespace_trimmed(self):
        self.assertEqual(self.draft("PF-SKU-0006", sku="PF-SAR-0001").status_code, 201)
        clash = self.draft("PF-SKU-0007", sku="  pf-sar-0001  ")
        self.assert_conflict(clash, "sku", "pf-sar-0001")

    def test_a_supplied_sku_is_stored_trimmed_and_verbatim(self):
        self.assertEqual(self.draft("PF-SKU-0008", sku="  Mixed-Case-SKU  ").status_code, 201)
        self.assertEqual(self.row("PF-SKU-0008").sku, "Mixed-Case-SKU")

    def test_an_omitted_sku_is_generated(self):
        self.assertEqual(self.draft("PF-SKU-0009").status_code, 201)
        sku = self.row("PF-SKU-0009").sku
        self.assertTrue(sku, "a SKU is always allocated")
        self.assertRegex(sku, r"^PF-\d{5}$")

    def test_omitted_sku_on_patch_leaves_it_unchanged(self):
        self.assertEqual(self.draft("PF-SKU-0010", sku="SKU-KEEP").status_code, 201)
        self.assertEqual(self.patch("PF-SKU-0010", {"name": "Only a name"}).status_code, 200)
        self.assertEqual(self.row("PF-SKU-0010").sku, "SKU-KEEP")

    def test_explicit_null_sku_on_patch_is_a_no_op(self):
        """`sku` is a NOT NULL column with a default — the null is dropped."""
        self.assertEqual(self.draft("PF-SKU-0011", sku="SKU-NULLTEST").status_code, 201)
        response = self.patch("PF-SKU-0011", {"sku": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.row("PF-SKU-0011").sku, "SKU-NULLTEST")


# ===========================================================================
# Slug uniqueness + supplied-slug correctness
# ===========================================================================

class SlugUniquenessTests(ProductIdentityCase):
    def assert_slug_conflict(self, response, value, suggested):
        body = self.assert_conflict(response, "slug", value)
        self.assertEqual(body["error"]["details"]["suggestedSlug"], suggested)
        return body

    def test_supplied_slug_is_honoured_verbatim_on_the_runtime_create_path(self):
        """PF3-N04 regression — this path used to discard the slug entirely."""
        response = self.runtime(name="Second Saree", slug="my-explicit-slug")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["product"]["slug"], "my-explicit-slug")

    def test_supplied_slug_is_honoured_verbatim_on_the_draft_create_path(self):
        response = self.draft("PF-SLUG-0001", slug="draft-explicit-slug")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.row("PF-SLUG-0001").slug, "draft-explicit-slug")

    def test_duplicate_slug_on_draft_create_is_409_with_a_suggestion(self):
        self.assertEqual(self.draft("PF-SLUG-0002", slug="banarasi-silk").status_code, 201)
        second = self.draft("PF-SLUG-0003", slug="banarasi-silk")
        self.assert_slug_conflict(second, "banarasi-silk", "banarasi-silk-1")
        self.assertIsNone(self.row("PF-SLUG-0003"))

    def test_duplicate_slug_on_runtime_create_is_409_with_a_suggestion(self):
        self.assertEqual(self.runtime(slug="runtime-slug").status_code, 201)
        before = len(self.rows())
        second = self.runtime(slug="runtime-slug")
        self.assert_slug_conflict(second, "runtime-slug", "runtime-slug-1")
        self.assertEqual(len(self.rows()), before)

    def test_the_suggestion_is_deterministic_and_itself_free(self):
        self.assertEqual(self.draft("PF-SLUG-0004", slug="taken").status_code, 201)
        self.assertEqual(self.draft("PF-SLUG-0005", slug="taken-1").status_code, 201)

        first = self.draft("PF-SLUG-0006", slug="taken")
        second = self.draft("PF-SLUG-0007", slug="taken")
        suggestion = first.json()["error"]["details"]["suggestedSlug"]
        self.assertEqual(suggestion, "taken-2", "the first free suffix, skipping taken-1")
        self.assertEqual(
            suggestion,
            second.json()["error"]["details"]["suggestedSlug"],
            "the same DB state always yields the same suggestion",
        )
        # Retrying with the suggestion succeeds — the contract is actionable.
        retry = self.draft("PF-SLUG-0008", slug=suggestion)
        self.assertEqual(retry.status_code, 201, retry.text)

    def test_duplicate_slug_on_patch_is_409_and_writes_nothing(self):
        self.assertEqual(self.draft("PF-SLUG-0009", slug="alpha").status_code, 201)
        self.assertEqual(self.draft("PF-SLUG-0010", slug="beta").status_code, 201)

        response = self.patch("PF-SLUG-0010", {"slug": "alpha", "name": "Should not land"})
        self.assert_slug_conflict(response, "alpha", "alpha-1")
        row = self.row("PF-SLUG-0010")
        self.assertEqual(row.slug, "beta")
        self.assertNotEqual(row.name, "Should not land")

    def test_a_product_may_keep_its_own_slug_on_patch(self):
        self.assertEqual(self.draft("PF-SLUG-0011", slug="own-slug").status_code, 201)
        response = self.patch("PF-SLUG-0011", {"slug": "own-slug", "name": "Renamed"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.row("PF-SLUG-0011").slug, "own-slug")

    def test_a_free_slug_on_patch_is_stored_verbatim_and_never_suffixed(self):
        """Plan §8 — an admin typo must not silently produce a different URL."""
        self.assertEqual(self.draft("PF-SLUG-0012", slug="before").status_code, 201)
        response = self.patch("PF-SLUG-0012", {"slug": "after"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.row("PF-SLUG-0012").slug, "after")

    def test_omitted_slug_on_patch_leaves_it_unchanged(self):
        self.assertEqual(self.draft("PF-SLUG-0013", slug="keep-me").status_code, 201)
        self.assertEqual(self.patch("PF-SLUG-0013", {"name": "Only a name"}).status_code, 200)
        self.assertEqual(self.row("PF-SLUG-0013").slug, "keep-me")

    def test_explicit_null_slug_on_patch_is_a_no_op(self):
        self.assertEqual(self.draft("PF-SLUG-0014", slug="null-test").status_code, 201)
        self.assertEqual(self.patch("PF-SLUG-0014", {"slug": None}).status_code, 200)
        self.assertEqual(self.row("PF-SLUG-0014").slug, "null-test")

    def test_an_omitted_slug_is_generated_from_the_name(self):
        response = self.draft("PF-SLUG-0015", name="Generated Slug Product")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.row("PF-SLUG-0015").slug, "generated-slug-product")

    def test_generation_still_de_duplicates_when_no_slug_was_supplied(self):
        self.assertEqual(self.draft("PF-SLUG-0016", name="Same Name").status_code, 201)
        second = self.draft("PF-SLUG-0017", name="Same Name")
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(self.row("PF-SLUG-0017").slug, "same-name-1")

    def test_slug_comparison_is_case_insensitive_and_trimmed(self):
        self.assertEqual(self.draft("PF-SLUG-0018", slug="Case-Slug").status_code, 201)
        clash = self.draft("PF-SLUG-0019", slug="  case-slug ")
        self.assert_conflict(clash, "slug", "case-slug")


# ===========================================================================
# Create-path parity + error-contract separation
# ===========================================================================

class CreatePathParityTests(ProductIdentityCase):
    def test_both_create_paths_reject_a_duplicate_sku_identically(self):
        self.assertEqual(self.draft("PF-PAR-0001", sku="SHARED-SKU").status_code, 201)
        draft_clash = self.draft("PF-PAR-0002", sku="SHARED-SKU")
        runtime_clash = self.runtime(sku="SHARED-SKU", slug="parity-runtime")
        for response in (draft_clash, runtime_clash):
            self.assert_conflict(response, "sku", "SHARED-SKU")

    def test_both_create_paths_reject_a_duplicate_slug_identically(self):
        self.assertEqual(self.draft("PF-PAR-0003", slug="shared-slug").status_code, 201)
        draft_clash = self.draft("PF-PAR-0004", slug="shared-slug")
        runtime_clash = self.runtime(slug="shared-slug")
        for response in (draft_clash, runtime_clash):
            body = self.assert_conflict(response, "slug", "shared-slug")
            self.assertEqual(body["error"]["details"]["suggestedSlug"], "shared-slug-1")

    def test_both_create_paths_honour_a_supplied_slug(self):
        self.assertEqual(self.draft("PF-PAR-0005", slug="draft-kept").status_code, 201)
        self.assertEqual(self.runtime(slug="runtime-kept").status_code, 201)
        self.assertEqual(self.row("PF-PAR-0005").slug, "draft-kept")
        self.assertIn("runtime-kept", [row.slug for row in self.rows()])

    def test_a_taken_product_id_is_still_a_409(self):
        self.assertEqual(self.draft("PF-PAR-0006").status_code, 201)
        response = self.draft("PF-PAR-0006", sku="OTHER-SKU", slug="other-slug")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "CONFLICT")

    def test_409_identity_is_distinguishable_from_422_taxonomy(self):
        self.assertEqual(self.draft("PF-PAR-0007", sku="DISTINCT-SKU").status_code, 201)
        conflict = self.draft("PF-PAR-0008", sku="DISTINCT-SKU")
        validation = self.draft("PF-PAR-0009", category="ghost-category")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "CONFLICT")
        self.assertEqual(validation.status_code, 422)
        self.assertEqual(validation.json()["error"]["code"], "VALIDATION_ERROR")

    def test_no_identity_rejection_ever_returns_500(self):
        self.assertEqual(self.draft("PF-PAR-0010", sku="NO-500", slug="no-500").status_code, 201)
        for payload in (
            {"sku": "NO-500"},
            {"slug": "no-500"},
            {"sku": "no-500x", "slug": "no-500"},
            {"sku": " NO-500 "},
        ):
            response = self.draft("PF-PAR-0011", **payload)
            self.assertEqual(response.status_code, 409, response.text)

    def test_duplicate_endpoint_still_allocates_a_free_slug_and_sku(self):
        self.assertEqual(self.draft("PF-PAR-0012", slug="original", sku="ORIG-SKU").status_code, 201)
        response = self.client.post("/api/v1/admin/products/PF-PAR-0012/duplicate")
        self.assertEqual(response.status_code, 201, response.text)
        copy = response.json()["product"]
        self.assertNotEqual(copy["slug"], "original")
        self.assertNotEqual(copy["sku"], "ORIG-SKU")


# ===========================================================================
# The availability probe agrees with the enforcement
# ===========================================================================

class AvailabilityProbeTests(ProductIdentityCase):
    def test_probe_reports_taken_sku_and_slug(self):
        self.assertEqual(self.draft("PF-AVL-0001", sku="PROBE-SKU", slug="probe-slug").status_code, 201)
        response = self.client.get(
            "/api/v1/admin/products/availability?sku=PROBE-SKU&slug=probe-slug"
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["skuTaken"])
        self.assertTrue(body["slugTaken"])
        self.assertEqual(body["suggestedSlug"], "probe-slug-1")

    def test_probe_uses_the_same_case_insensitive_rule_as_the_write_path(self):
        self.assertEqual(self.draft("PF-AVL-0002", sku="PROBE-CASE", slug="probe-case").status_code, 201)
        response = self.client.get(
            "/api/v1/admin/products/availability?sku=probe-case&slug=PROBE-CASE"
        )
        body = response.json()
        self.assertTrue(body["skuTaken"], "the probe must not disagree with the 409")
        self.assertTrue(body["slugTaken"])

    def test_probe_reports_free_values_as_free(self):
        response = self.client.get(
            "/api/v1/admin/products/availability?sku=NOBODY&slug=nobody"
        )
        body = response.json()
        self.assertFalse(body["skuTaken"])
        self.assertFalse(body["slugTaken"])
        self.assertIsNone(body["suggestedSlug"])


# ===========================================================================
# Block 1 / Block 2 must not regress
# ===========================================================================

class PriorBlockRegressionTests(ProductIdentityCase):
    def test_taxonomy_validation_still_applies_on_create(self):
        response = self.draft("PF-REG-0001", category="ghost-category")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(
            response.json()["error"]["details"][0]["loc"], ["body", "category"]
        )

    def test_taxonomy_is_still_canonicalised_alongside_the_identity_rules(self):
        response = self.draft("PF-REG-0002", category="sarees", subcategory="Banarasi", sku="REG-SKU")
        self.assertEqual(response.status_code, 201, response.text)
        row = self.row("PF-REG-0002")
        self.assertEqual(row.category, CATEGORY_ID)
        self.assertEqual(row.subcategory, SUBCATEGORY_ID)
        self.assertEqual(row.sku, "REG-SKU")

    def test_the_save_and_continue_shape_is_unchanged(self):
        next_id = self.client.get(
            f"/api/v1/admin/products/next-id?category={CATEGORY_ID}"
        ).json()["nextId"]
        created = self.draft(next_id, sku=f"SKU-{next_id}")
        self.assertEqual(created.status_code, 201, created.text)
        product = created.json()["product"]
        self.assertEqual(product["status"], "DRAFT")
        self.assertFalse(product["published"])
        self.assertEqual(
            self.client.get(f"/api/v1/admin/products/{next_id}").status_code, 200
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
