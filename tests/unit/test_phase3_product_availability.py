"""
Phase 3 Block 4 — product identity availability / pre-flight contract.

Closes PF3-N16: `GET /admin/products/availability` existed with zero call sites
while the admin editor decided SKU/slug uniqueness from a browser-session cache.
Wiring the editor to it is only safe if the endpoint cannot DISAGREE with the
Block 3 write path, so that is what this suite pins:

  * the probe and the 409 use one implementation of the collision rule —
    trimmed, case-insensitive, self-excluding;
  * `excludeId` makes a product's own SKU/slug free, exactly as PATCH does;
  * `suggestedSlug` is the same deterministic value the 409 would carry, and is
    actually free;
  * availability FREE is followed by a successful write, availability TAKEN is
    followed by a 409 — the pre-flight is a convenience, the 409 is the
    authority.

Runs against the REAL routers, service and ORM on a throwaway SQLite file, with
Block 2 taxonomy validation live (so the authoritative category is seeded).
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
AVAILABILITY = "/api/v1/admin/products/availability"


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
class AvailabilityCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        importlib.import_module("app.models")
        from app.models.auth.user import UserModel
        from app.models.base import Base
        from app.models.catalog.category import CategoryModel, SubcategoryModel
        from app.models.catalog.product import ProductModel

        self._UserModel = UserModel
        self._ProductModel = ProductModel

        self._tmp = tempfile.TemporaryDirectory(prefix="pf3-availability-")
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
                email="pf3-availability-admin@pratikshya.test",
                full_name="Availability Admin",
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
        FastAPICache.init(backend=_PassThroughCache(), prefix="pf3-availability-test")
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

    def patch(self, product_id, body):
        return self.client.patch(f"/api/v1/admin/products/{product_id}", json=body)

    def probe(self, **params):
        response = self.client.get(AVAILABILITY, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

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


# ===========================================================================
# SKU availability
# ===========================================================================

class SkuAvailabilityTests(AvailabilityCase):
    def test_a_free_sku_is_reported_available(self):
        body = self.probe(sku="NOBODY-HAS-THIS")
        self.assertFalse(body["skuTaken"])
        self.assertFalse(body["slugTaken"])
        self.assertIsNone(body["suggestedSlug"])
        self.assertIs(body["ok"], True)

    def test_a_taken_sku_is_reported_unavailable(self):
        self.assertEqual(self.draft("PF-AV-0001", sku="TAKEN-SKU").status_code, 201)
        self.assertTrue(self.probe(sku="TAKEN-SKU")["skuTaken"])

    def test_sku_matching_is_case_insensitive(self):
        self.assertEqual(self.draft("PF-AV-0002", sku="PF-SAR-0001").status_code, 201)
        self.assertTrue(self.probe(sku="pf-sar-0001")["skuTaken"])
        self.assertTrue(self.probe(sku="Pf-SaR-0001")["skuTaken"])

    def test_sku_matching_is_whitespace_normalised(self):
        self.assertEqual(self.draft("PF-AV-0003", sku="TRIMMED-SKU").status_code, 201)
        self.assertTrue(self.probe(sku="   TRIMMED-SKU   ")["skuTaken"])

    def test_an_omitted_sku_is_simply_not_reported_taken(self):
        self.assertEqual(self.draft("PF-AV-0004", sku="SOMETHING").status_code, 201)
        body = self.probe(slug="unrelated-slug")
        self.assertFalse(body["skuTaken"], "an unasked question is never answered 'taken'")


# ===========================================================================
# Slug availability + suggestedSlug
# ===========================================================================

class SlugAvailabilityTests(AvailabilityCase):
    def test_a_free_slug_is_reported_available_with_no_suggestion(self):
        body = self.probe(slug="nobody-has-this")
        self.assertFalse(body["slugTaken"])
        self.assertIsNone(body["suggestedSlug"], "a free slug needs no alternative")

    def test_a_taken_slug_is_reported_unavailable_with_a_suggestion(self):
        self.assertEqual(self.draft("PF-AV-0005", slug="banarasi-silk").status_code, 201)
        body = self.probe(slug="banarasi-silk")
        self.assertTrue(body["slugTaken"])
        self.assertEqual(body["suggestedSlug"], "banarasi-silk-1")

    def test_slug_matching_is_case_insensitive(self):
        self.assertEqual(self.draft("PF-AV-0006", slug="Case-Slug").status_code, 201)
        self.assertTrue(self.probe(slug="case-slug")["slugTaken"])

    def test_slug_matching_is_whitespace_normalised(self):
        self.assertEqual(self.draft("PF-AV-0007", slug="trim-slug").status_code, 201)
        self.assertTrue(self.probe(slug="  trim-slug  ")["slugTaken"])

    def test_the_suggestion_skips_every_occupied_value(self):
        self.assertEqual(self.draft("PF-AV-0008", slug="taken").status_code, 201)
        self.assertEqual(self.draft("PF-AV-0009", slug="taken-1").status_code, 201)
        self.assertEqual(self.draft("PF-AV-0010", slug="taken-2").status_code, 201)
        self.assertEqual(self.probe(slug="taken")["suggestedSlug"], "taken-3")

    def test_the_suggestion_is_deterministic(self):
        self.assertEqual(self.draft("PF-AV-0011", slug="stable").status_code, 201)
        first = self.probe(slug="stable")["suggestedSlug"]
        second = self.probe(slug="stable")["suggestedSlug"]
        self.assertEqual(first, second)
        self.assertEqual(first, "stable-1")

    def test_the_suggestion_is_actually_free_and_accepted_by_the_write_path(self):
        self.assertEqual(self.draft("PF-AV-0012", slug="offered").status_code, 201)
        suggestion = self.probe(slug="offered")["suggestedSlug"]
        self.assertFalse(self.probe(slug=suggestion)["slugTaken"], "the offer must be free")
        created = self.draft("PF-AV-0013", slug=suggestion)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(self.row("PF-AV-0013").slug, suggestion)

    def test_the_suggestion_matches_the_one_the_409_carries(self):
        """The probe and the conflict must never offer different retry values."""
        self.assertEqual(self.draft("PF-AV-0014", slug="shared-offer").status_code, 201)
        from_probe = self.probe(slug="shared-offer")["suggestedSlug"]
        conflict = self.draft("PF-AV-0015", slug="shared-offer")
        self.assertEqual(conflict.status_code, 409, conflict.text)
        from_409 = conflict.json()["error"]["details"]["suggestedSlug"]
        self.assertEqual(from_probe, from_409)


# ===========================================================================
# excludeId
# ===========================================================================

class ExcludeIdTests(AvailabilityCase):
    def test_excludeId_reports_the_products_own_sku_as_free(self):
        self.assertEqual(self.draft("PF-EX-0001", sku="OWN-SKU").status_code, 201)
        self.assertTrue(self.probe(sku="OWN-SKU")["skuTaken"], "taken without exclusion")
        self.assertFalse(
            self.probe(sku="OWN-SKU", excludeId="PF-EX-0001")["skuTaken"],
            "a product editing itself must not be told its own SKU is taken",
        )

    def test_excludeId_reports_the_products_own_slug_as_free(self):
        self.assertEqual(self.draft("PF-EX-0002", slug="own-slug").status_code, 201)
        self.assertTrue(self.probe(slug="own-slug")["slugTaken"])
        body = self.probe(slug="own-slug", excludeId="PF-EX-0002")
        self.assertFalse(body["slugTaken"])
        self.assertIsNone(body["suggestedSlug"], "no alternative is needed for its own slug")

    def test_excludeId_does_not_hide_another_products_sku(self):
        self.assertEqual(self.draft("PF-EX-0003", sku="MINE").status_code, 201)
        self.assertEqual(self.draft("PF-EX-0004", sku="THEIRS").status_code, 201)
        body = self.probe(sku="THEIRS", excludeId="PF-EX-0003")
        self.assertTrue(body["skuTaken"], "exclusion applies to one row, not to the rule")

    def test_excludeId_does_not_hide_another_products_slug(self):
        self.assertEqual(self.draft("PF-EX-0005", slug="mine").status_code, 201)
        self.assertEqual(self.draft("PF-EX-0006", slug="theirs").status_code, 201)
        body = self.probe(slug="theirs", excludeId="PF-EX-0005")
        self.assertTrue(body["slugTaken"])
        self.assertEqual(body["suggestedSlug"], "theirs-1")

    def test_excludeId_is_case_insensitive_on_the_products_own_value(self):
        self.assertEqual(self.draft("PF-EX-0007", sku="Mixed-Own").status_code, 201)
        self.assertFalse(
            self.probe(sku="mixed-own", excludeId="PF-EX-0007")["skuTaken"],
            "self-exclusion must survive the case-insensitive compare",
        )

    def test_an_unknown_excludeId_excludes_nothing(self):
        self.assertEqual(self.draft("PF-EX-0008", sku="STILL-TAKEN").status_code, 201)
        self.assertTrue(self.probe(sku="STILL-TAKEN", excludeId="PF-DOES-NOT-EXIST")["skuTaken"])

    def test_a_blank_excludeId_is_ignored(self):
        self.assertEqual(self.draft("PF-EX-0009", sku="BLANK-EX").status_code, 201)
        self.assertTrue(self.probe(sku="BLANK-EX", excludeId="")["skuTaken"])
        self.assertTrue(self.probe(sku="BLANK-EX", excludeId="   ")["skuTaken"])

    def test_the_suggestion_respects_excludeId(self):
        """A product keeping its own slug frees that value for the suggestion."""
        self.assertEqual(self.draft("PF-EX-0010", slug="pool").status_code, 201)
        self.assertEqual(self.draft("PF-EX-0011", slug="pool-1").status_code, 201)
        # Without exclusion, `pool` and `pool-1` are both gone → pool-2.
        self.assertEqual(self.probe(slug="pool")["suggestedSlug"], "pool-2")
        # Excluding the holder of `pool-1` frees it → pool-1.
        self.assertEqual(
            self.probe(slug="pool", excludeId="PF-EX-0011")["suggestedSlug"], "pool-1"
        )


# ===========================================================================
# The probe and the write path must never disagree
# ===========================================================================

class ProbeAgreesWithWritePathTests(AvailabilityCase):
    def test_free_then_create_succeeds(self):
        self.assertFalse(self.probe(sku="AGREE-SKU", slug="agree-slug")["skuTaken"])
        created = self.draft("PF-AG-0001", sku="AGREE-SKU", slug="agree-slug")
        self.assertEqual(created.status_code, 201, created.text)

    def test_taken_then_create_is_rejected(self):
        self.assertEqual(self.draft("PF-AG-0002", sku="BUSY", slug="busy").status_code, 201)
        body = self.probe(sku="BUSY", slug="busy")
        self.assertTrue(body["skuTaken"])
        self.assertTrue(body["slugTaken"])
        self.assertEqual(self.draft("PF-AG-0003", sku="BUSY").status_code, 409)
        self.assertEqual(self.draft("PF-AG-0004", slug="busy").status_code, 409)

    def test_every_free_verdict_is_honoured_by_the_write_path(self):
        """A FREE verdict followed immediately by a write must never 409."""
        self.assertEqual(self.draft("PF-AG-0005", sku="SEED", slug="seed").status_code, 201)
        candidates = [
            ("free-one", "FREE-ONE"),
            ("SEED-CASE", "seed-case"),
            ("  spaced  ", "  SPACED  "),
        ]
        for index, (slug, sku) in enumerate(candidates):
            with self.subTest(slug=slug):
                body = self.probe(sku=sku, slug=slug)
                self.assertFalse(body["skuTaken"])
                self.assertFalse(body["slugTaken"])
                created = self.draft(f"PF-AG-100{index}", sku=sku, slug=slug)
                self.assertEqual(created.status_code, 201, created.text)

    def test_every_taken_verdict_is_rejected_by_the_write_path(self):
        self.assertEqual(self.draft("PF-AG-0006", sku="HELD-SKU", slug="held-slug").status_code, 201)
        for sku, slug in (
            ("HELD-SKU", None),
            ("held-sku", None),
            ("  HELD-SKU ", None),
            (None, "held-slug"),
            (None, "HELD-SLUG"),
            (None, " held-slug "),
        ):
            with self.subTest(sku=sku, slug=slug):
                params = {k: v for k, v in (("sku", sku), ("slug", slug)) if v is not None}
                body = self.probe(**params)
                self.assertTrue(body["skuTaken"] or body["slugTaken"])
                rejected = self.draft("PF-AG-0007", **params)
                self.assertEqual(rejected.status_code, 409, rejected.text)

    def test_probe_and_patch_agree_on_the_products_own_identity(self):
        self.assertEqual(self.draft("PF-AG-0008", sku="SELF", slug="self").status_code, 201)
        body = self.probe(sku="SELF", slug="self", excludeId="PF-AG-0008")
        self.assertFalse(body["skuTaken"])
        self.assertFalse(body["slugTaken"])
        patched = self.patch("PF-AG-0008", {"sku": "SELF", "slug": "self"})
        self.assertEqual(patched.status_code, 200, patched.text)

    def test_probe_and_patch_agree_on_another_products_identity(self):
        self.assertEqual(self.draft("PF-AG-0009", sku="A-SKU", slug="a-slug").status_code, 201)
        self.assertEqual(self.draft("PF-AG-0010", sku="B-SKU", slug="b-slug").status_code, 201)
        body = self.probe(sku="A-SKU", slug="a-slug", excludeId="PF-AG-0010")
        self.assertTrue(body["skuTaken"])
        self.assertTrue(body["slugTaken"])
        self.assertEqual(self.patch("PF-AG-0010", {"sku": "A-SKU"}).status_code, 409)
        self.assertEqual(self.patch("PF-AG-0010", {"slug": "a-slug"}).status_code, 409)


# ===========================================================================
# Contract hygiene
# ===========================================================================

class AvailabilityContractTests(AvailabilityCase):
    def test_the_response_shape_is_exactly_the_declared_contract(self):
        body = self.probe(sku="SHAPE", slug="shape")
        self.assertEqual(set(body), {"ok", "skuTaken", "slugTaken", "suggestedSlug"})
        self.assertIsInstance(body["skuTaken"], bool)
        self.assertIsInstance(body["slugTaken"], bool)

    def test_no_parameters_at_all_is_a_valid_empty_answer(self):
        body = self.probe()
        self.assertFalse(body["skuTaken"])
        self.assertFalse(body["slugTaken"])
        self.assertIsNone(body["suggestedSlug"])

    def test_the_probe_never_leaks_sql_or_internals(self):
        self.assertEqual(self.draft("PF-CT-0001", sku="LEAK", slug="leak").status_code, 201)
        for params in (
            {"sku": "LEAK"},
            {"slug": "leak"},
            {"sku": "LEAK", "slug": "leak", "excludeId": "PF-CT-0001"},
            {"sku": "'; DROP TABLE catalog_product; --"},
            {"slug": "%_%"},
        ):
            with self.subTest(params=params):
                response = self.client.get(AVAILABILITY, params=params)
                self.assertEqual(response.status_code, 200, response.text)
                rendered = response.text.lower()
                for leak in ("traceback", "select ", "sqlalchemy", "sqlite", "catalog_product"):
                    self.assertNotIn(leak, rendered, leak)

    def test_a_hostile_sku_value_cannot_reach_the_database_as_sql(self):
        self.assertEqual(self.draft("PF-CT-0002", sku="INTACT").status_code, 201)
        self.probe(sku="'; DROP TABLE catalog_product; --")
        self.assertTrue(self.probe(sku="INTACT")["skuTaken"], "the table is still there")

    def test_the_probe_is_a_read_and_creates_nothing(self):
        import asyncio

        async def _count():
            async with self.Session() as session:
                return len((await session.execute(select(self._ProductModel))).scalars().all())

        before = asyncio.get_event_loop().run_until_complete(_count())
        self.probe(sku="PHANTOM", slug="phantom", excludeId="PF-NOPE")
        after = asyncio.get_event_loop().run_until_complete(_count())
        self.assertEqual(before, after, "a probe must never write")

    def test_an_unauthenticated_probe_uses_the_canonical_envelope(self):
        """Phase 1 contract: no bespoke error shape on this endpoint either."""
        from app.dependencies import get_current_user

        app = self._build_app()
        app.dependency_overrides.pop(get_current_user, None)
        client = TestClient(app)

        response = client.get(AVAILABILITY, params={"sku": "X"})
        self.assertIn(response.status_code, (401, 403), response.text)
        body = response.json()
        self.assertIs(body["success"], False)
        self.assertIn(body["error"]["code"], ("UNAUTHORIZED", "FORBIDDEN"))
        self.assertTrue(body["error"]["message"])


# ===========================================================================
# §12 — the three real flows, end to end through the ASGI app
# ===========================================================================

class RealFlowTests(AvailabilityCase):
    def test_new_product_flow_availability_then_next_id_then_create(self):
        """availability(free) → FREE → server id allocation → create → 201."""
        probe = self.probe(sku="FLOW-NEW-SKU", slug="flow-new-slug")
        self.assertFalse(probe["skuTaken"])
        self.assertFalse(probe["slugTaken"])

        next_id_response = self.client.get(
            f"/api/v1/admin/products/next-id?category={CATEGORY_ID}"
        )
        self.assertEqual(next_id_response.status_code, 200, next_id_response.text)
        next_id = next_id_response.json()["nextId"]

        created = self.draft(next_id, sku="FLOW-NEW-SKU", slug="flow-new-slug")
        self.assertEqual(created.status_code, 201, created.text)
        product = created.json()["product"]
        self.assertEqual(product["id"], next_id, "the server's id is the one used")
        self.assertEqual(product["sku"], "FLOW-NEW-SKU")
        self.assertEqual(product["slug"], "flow-new-slug")
        self.assertEqual(product["status"], "DRAFT")

    def test_edit_existing_product_flow_own_identity_then_patch(self):
        """availability(own, excludeId) → FREE → PATCH → 200."""
        self.assertEqual(
            self.draft("PF-FLOW-0001", sku="FLOW-OWN", slug="flow-own").status_code, 201
        )
        probe = self.probe(sku="FLOW-OWN", slug="flow-own", excludeId="PF-FLOW-0001")
        self.assertFalse(probe["skuTaken"])
        self.assertFalse(probe["slugTaken"])

        patched = self.patch(
            "PF-FLOW-0001", {"sku": "FLOW-OWN", "slug": "flow-own", "name": "Edited"}
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        row = self.row("PF-FLOW-0001")
        self.assertEqual(row.sku, "FLOW-OWN")
        self.assertEqual(row.slug, "flow-own")
        self.assertEqual(row.name, "Edited")

    def test_edit_onto_another_product_flow_taken_then_409(self):
        """availability(other, excludeId) → TAKEN → PATCH anyway → 409."""
        self.assertEqual(
            self.draft("PF-FLOW-0002", sku="FLOW-A", slug="flow-a").status_code, 201
        )
        self.assertEqual(
            self.draft("PF-FLOW-0003", sku="FLOW-B", slug="flow-b").status_code, 201
        )

        probe = self.probe(sku="FLOW-A", slug="flow-a", excludeId="PF-FLOW-0003")
        self.assertTrue(probe["skuTaken"])
        self.assertTrue(probe["slugTaken"])
        self.assertEqual(probe["suggestedSlug"], "flow-a-1")

        # The operator ignores the warning; the server is still the authority.
        conflict = self.patch("PF-FLOW-0003", {"sku": "FLOW-A"})
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["error"]["code"], "CONFLICT")
        self.assertEqual(conflict.json()["error"]["details"]["field"], "sku")

        slug_conflict = self.patch("PF-FLOW-0003", {"slug": "flow-a"})
        self.assertEqual(slug_conflict.status_code, 409, slug_conflict.text)
        self.assertEqual(
            slug_conflict.json()["error"]["details"]["suggestedSlug"],
            probe["suggestedSlug"],
            "the 409 offers exactly what the pre-flight offered",
        )

        row = self.row("PF-FLOW-0003")
        self.assertEqual(row.sku, "FLOW-B", "nothing was overwritten")
        self.assertEqual(row.slug, "flow-b")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
