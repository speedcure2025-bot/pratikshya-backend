"""
Phase 3 Block 5 — the STOREFRONT VISIBILITY / PUBLICATION GATE.

Governing plan: PHASE_3_PRODUCT_CATALOG_IMPLEMENTATION_PLAN.md
  §4 item 7   — "Complete the visibility gate (subcategory; fail-closed)"
                (API-180, PF3-N06, PF3-N07)
  §10         — the storefront visibility contract and its eight-row matrix
  §22.1       — the "Visibility" test row
  §22.3       — the end-to-end lifecycle → storefront flow
  §24 step 7  — subcategory parity; fail-closed default ONLY after the step 0
                report; extend cache invalidation coverage
  §25 (11-14) — the visibility and "approve never publishes" acceptance criteria

What this suite pins
────────────────────
1. NEW BEHAVIOUR (PF3-N06) — a KNOWN non-ACTIVE subcategory now hides its
   products from every public surface, exactly as a non-ACTIVE category
   already did.  Marked `# NEW` below.
2. NEW BEHAVIOUR (§24 step 7, R9) — a taxonomy write now evicts the
   `product:storefront:*` KV entries, which `get_storefront_product` serves
   BEFORE it evaluates the gate.  Without this the brand-new subcategory gate
   is bypassable for a whole TTL on the one transition it exists to catch.
3. REGRESSION LOCKS — everything else: the PUBLISHED/`published` gate, the
   category gate, approve-never-publishes, publish/unpublish, the canonical
   404 envelope, and cross-surface agreement between `/products`,
   `/products/{id}`, `/explore`, `/search` and `/categories/{id}/products`.

Deliberately NOT changed here — PF3-N07 (fail-closed default)
─────────────────────────────────────────────────────────────
An unresolvable category/subcategory reference still FAILS OPEN.  Plan §24
step 7 permits the flip "only after the step 0 report is reviewed" and §23 R1
("Never flip the default blind") rates it the highest regression risk in
Phase 3.  Step 0 is a `SELECT DISTINCT` over the real PostgreSQL catalogue,
which this sandbox does not have (plan Appendix B).  The current fail-open
behaviour is therefore asserted EXPLICITLY below, so the day the default is
flipped these tests fail loudly instead of drifting silently.

Everything runs against the REAL routers, REAL services and REAL ORM models on
a throwaway SQLite file — the same dialect shim the Phase 6/7 and Block 2/3/4
suites use.  No migration is involved: no column, index or constraint changes.
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


# ── Authoritative taxonomy seeded for every case ─────────────────────────────

CAT_ACTIVE = "cat-sarees"          # ACTIVE
CAT_DRAFT = "cat-new-season"       # DRAFT
CAT_ARCHIVED = "cat-legacy"        # ARCHIVED

SUB_ACTIVE = "cat-sarees-banarasi"   # ACTIVE,   belongs to CAT_ACTIVE
SUB_DRAFT = "cat-sarees-upcoming"    # DRAFT,    belongs to CAT_ACTIVE
SUB_ARCHIVED = "cat-sarees-vintage"  # ARCHIVED, belongs to CAT_ACTIVE

# ── The product matrix (plan §10.1) ──────────────────────────────────────────
#
# (id, status, published, category, subcategory, expected_public_visibility)

P_LIVE = "PF-VIS-0001"           # PUBLISHED, ACTIVE cat, ACTIVE sub   → visible
P_LIVE_NOSUB = "PF-VIS-0002"     # PUBLISHED, ACTIVE cat, no sub       → visible
P_ARCH_SUB = "PF-VIS-0003"       # PUBLISHED, ACTIVE cat, ARCHIVED sub → HIDDEN (NEW)
P_DRAFT_SUB = "PF-VIS-0004"      # PUBLISHED, ACTIVE cat, DRAFT sub    → HIDDEN (NEW)
P_DRAFT_CAT = "PF-VIS-0005"      # PUBLISHED, DRAFT cat                → hidden
P_ARCH_CAT = "PF-VIS-0006"       # PUBLISHED, ARCHIVED cat             → hidden
P_UNKNOWN_CAT = "PF-VIS-0007"    # PUBLISHED, unresolvable cat         → VISIBLE (PF3-N07)
P_NO_CAT = "PF-VIS-0008"         # PUBLISHED, empty cat                → VISIBLE (PF3-N07)
P_UNKNOWN_SUB = "PF-VIS-0009"    # PUBLISHED, unresolvable sub         → VISIBLE (PF3-N07)
P_DRAFT_STATUS = "PF-VIS-0010"   # DRAFT                               → hidden
P_PENDING = "PF-VIS-0011"        # PENDING_REVIEW                      → hidden
P_APPROVED = "PF-VIS-0012"       # PENDING_REVIEW + review APPROVED    → hidden
P_ARCHIVED_STATUS = "PF-VIS-0013"  # ARCHIVED                          → hidden
P_FLAG_FALSE = "PF-VIS-0014"     # status PUBLISHED but published=False → hidden

VISIBLE_IDS = {P_LIVE, P_LIVE_NOSUB, P_UNKNOWN_CAT, P_NO_CAT, P_UNKNOWN_SUB}
HIDDEN_IDS = {
    P_ARCH_SUB, P_DRAFT_SUB, P_DRAFT_CAT, P_ARCH_CAT, P_DRAFT_STATUS,
    P_PENDING, P_APPROVED, P_ARCHIVED_STATUS, P_FLAG_FALSE,
}


class _PassThroughCache(Backend):
    """fastapi-cache2 backend that never serves a stored HTTP response."""

    async def get_with_ttl(self, key):
        return 0, None

    async def get(self, key):
        return None

    async def set(self, key, value, expire=None):
        return None

    async def clear(self, namespace=None, key=None):
        return 0


@unittest.skipUnless(HAS_AIOSQLITE, "aiosqlite is not installed")
class _VisibilityCase(unittest.IsolatedAsyncioTestCase):
    """Real app + real taxonomy + real products on a disposable SQLite database."""

    seed_matrix = True

    async def asyncSetUp(self):
        importlib.import_module("app.models")  # registers every mapped class
        from app.core.lru_cache_store import init_lru_cache
        from app.models.auth.user import UserModel
        from app.models.base import Base
        from app.models.catalog.category import CategoryModel, SubcategoryModel
        from app.models.catalog.product import ProductModel
        from app.models.rbac.role import RoleModel
        from app.models.rbac.user_role import UserRoleModel

        self._RoleModel = RoleModel
        self._UserRoleModel = UserRoleModel
        self._UserModel = UserModel
        self._ProductModel = ProductModel
        self._CategoryModel = CategoryModel
        self._SubcategoryModel = SubcategoryModel

        # A fresh in-process KV store per test — the cache client is a
        # process-wide singleton and these tests assert on eviction.
        init_lru_cache()

        self._tmp = tempfile.TemporaryDirectory(prefix="pf3-visibility-")
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
                email="pf3-visibility-admin@pratikshya.test",
                full_name="Visibility Admin",
                hashed_password="x",
                user_type="admin",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            session.add(admin)
            await session.flush()

            # Real RBAC rows, not a patched permission helper: `submit-review`
            # goes through `require_permission_for_user`, which (unlike the
            # admin-surface helper) has no "no roles at all" compatibility
            # path. SUPER_ADMIN is the role the product desk actually runs as.
            role = RoleModel(name="SUPER_ADMIN", description="Test admin", is_system=True)
            session.add(role)
            await session.flush()
            session.add(UserRoleModel(user_id=admin.id, role_id=role.id))

            session.add_all([
                CategoryModel(id=CAT_ACTIVE, name="Sarees", slug="sarees", status="ACTIVE"),
                CategoryModel(id=CAT_DRAFT, name="New Season", slug="new-season", status="DRAFT"),
                CategoryModel(id=CAT_ARCHIVED, name="Legacy", slug="legacy", status="ARCHIVED"),
            ])
            session.add_all([
                SubcategoryModel(
                    id=SUB_ACTIVE, category_id=CAT_ACTIVE,
                    name="Banarasi", slug="banarasi", status="ACTIVE",
                ),
                SubcategoryModel(
                    id=SUB_DRAFT, category_id=CAT_ACTIVE,
                    name="Upcoming", slug="upcoming", status="DRAFT",
                ),
                SubcategoryModel(
                    id=SUB_ARCHIVED, category_id=CAT_ACTIVE,
                    name="Vintage", slug="vintage", status="ARCHIVED",
                ),
            ])
            if self.seed_matrix:
                for row in self._matrix_rows():
                    session.add(row)
            await session.commit()
            self.admin_id = admin.id

        self.app = self._build_app()
        self.client = TestClient(self.app)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    # ── fixtures ──────────────────────────────────────────────────────────────

    def _product(self, product_id, *, status, published, category, subcategory="", review=None):
        """
        The smallest safe fixture row: only the columns the visibility gate and
        the storefront projection actually read.  No golden/seed catalogue data
        is touched — every row here is created inside this test's own
        throwaway SQLite file and destroyed with it.
        """
        return self._ProductModel(
            id=product_id,
            product_id=product_id,
            name=f"Product {product_id}",
            slug=product_id.lower(),
            sku=product_id,
            category=category,
            subcategory=subcategory,
            price=4999,
            status=status,
            published=published,
            review=review or {"state": "NONE"},
            image="https://cdn.test/cover.jpg",
            description="A real description for the publish gate.",
        )

    def _matrix_rows(self):
        P = self._product
        return [
            P(P_LIVE, status="PUBLISHED", published=True,
              category=CAT_ACTIVE, subcategory=SUB_ACTIVE),
            P(P_LIVE_NOSUB, status="PUBLISHED", published=True,
              category=CAT_ACTIVE, subcategory=""),
            P(P_ARCH_SUB, status="PUBLISHED", published=True,
              category=CAT_ACTIVE, subcategory=SUB_ARCHIVED),
            P(P_DRAFT_SUB, status="PUBLISHED", published=True,
              category=CAT_ACTIVE, subcategory=SUB_DRAFT),
            P(P_DRAFT_CAT, status="PUBLISHED", published=True,
              category=CAT_DRAFT, subcategory=""),
            P(P_ARCH_CAT, status="PUBLISHED", published=True,
              category=CAT_ARCHIVED, subcategory=""),
            P(P_UNKNOWN_CAT, status="PUBLISHED", published=True,
              category="a-category-that-does-not-exist", subcategory=""),
            P(P_NO_CAT, status="PUBLISHED", published=True,
              category="", subcategory=""),
            P(P_UNKNOWN_SUB, status="PUBLISHED", published=True,
              category=CAT_ACTIVE, subcategory="a-subcategory-that-does-not-exist"),
            P(P_DRAFT_STATUS, status="DRAFT", published=False,
              category=CAT_ACTIVE, subcategory=SUB_ACTIVE),
            P(P_PENDING, status="PENDING_REVIEW", published=False,
              category=CAT_ACTIVE, subcategory=SUB_ACTIVE,
              review={"state": "PENDING"}),
            P(P_APPROVED, status="PENDING_REVIEW", published=False,
              category=CAT_ACTIVE, subcategory=SUB_ACTIVE,
              review={"state": "APPROVED"}),
            P(P_ARCHIVED_STATUS, status="ARCHIVED", published=False,
              category=CAT_ACTIVE, subcategory=SUB_ACTIVE),
            P(P_FLAG_FALSE, status="PUBLISHED", published=False,
              category=CAT_ACTIVE, subcategory=SUB_ACTIVE),
        ]

    def _build_app(self):
        from app.api.v1.categories import router as categories_router
        from app.api.v1.explore import router as explore_router
        from app.api.v1.products import router as products_router
        from app.api.v1.search import router as search_router
        from app.core.error_handlers import register_error_handlers
        from app.dependencies import get_current_user, get_db

        app = FastAPI()
        register_error_handlers(app)
        FastAPICache.init(backend=_PassThroughCache(), prefix="pf3-visibility-test")
        app.include_router(products_router, prefix="/api/v1")
        app.include_router(categories_router, prefix="/api/v1")
        app.include_router(explore_router, prefix="/api/v1")
        app.include_router(search_router, prefix="/api/v1")

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

    # ── helpers ───────────────────────────────────────────────────────────────

    def listing_ids(self, **params):
        """Ids returned by GET /products (the shared gate for every surface)."""
        response = self.client.get("/api/v1/products", params={"pageSize": 200, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return {item["id"] for item in response.json()["items"]}

    def explore_ids(self):
        response = self.client.get("/api/v1/explore", params={"pageSize": 200})
        self.assertEqual(response.status_code, 200, response.text)
        return {item["id"] for item in response.json()["items"]}

    def search_ids(self, q=None):
        params = {"pageSize": 200}
        if q is not None:
            params["q"] = q
        response = self.client.get("/api/v1/search", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return {item["id"] for item in response.json()["items"]}

    def category_ids(self, category_id=CAT_ACTIVE):
        response = self.client.get(
            f"/api/v1/categories/{category_id}/products", params={"pageSize": 200}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {item["id"] for item in response.json()["items"]}

    def pdp(self, id_or_slug):
        return self.client.get(f"/api/v1/products/{id_or_slug}")

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

    def assert_canonical_404(self, response, product_id):
        """The Phase 1 envelope — never a 403, a 409 or a leaked traceback."""
        self.assertEqual(response.status_code, 404, response.text)
        body = response.json()
        self.assertIs(body.get("success"), False, body)
        self.assertEqual(body.get("error", {}).get("code"), "NOT_FOUND", body)
        self.assertIn(product_id, body["error"]["message"])
        blob = response.text.lower()
        for leak in ("traceback", "sqlalchemy", "select ", " from catalog_product", "psycopg"):
            self.assertNotIn(leak, blob, f"leaked {leak!r} in the 404 body")


# =============================================================================
# 1. The §10.1 visibility matrix — list and detail must agree on every row
# =============================================================================


class VisibilityMatrixTests(_VisibilityCase):

    def test_published_active_taxonomy_is_visible(self):
        """REGRESSION LOCK — the happy path stays visible."""
        self.assertIn(P_LIVE, self.listing_ids())
        self.assertEqual(self.pdp(P_LIVE).status_code, 200)

    def test_published_without_subcategory_is_visible(self):
        """REGRESSION LOCK — an empty subcategory must not be gated."""
        self.assertIn(P_LIVE_NOSUB, self.listing_ids())
        self.assertEqual(self.pdp(P_LIVE_NOSUB).status_code, 200)

    def test_archived_subcategory_hides_the_product(self):
        """NEW (PF3-N06) — plan §10.1 row 2 was `visible/200`; it must now hide."""
        self.assertNotIn(P_ARCH_SUB, self.listing_ids())
        self.assert_canonical_404(self.pdp(P_ARCH_SUB), P_ARCH_SUB)

    def test_draft_subcategory_hides_the_product(self):
        """NEW (PF3-N06) — parity with the category gate, which hides DRAFT too."""
        self.assertNotIn(P_DRAFT_SUB, self.listing_ids())
        self.assert_canonical_404(self.pdp(P_DRAFT_SUB), P_DRAFT_SUB)

    def test_draft_category_hides_the_product(self):
        """REGRESSION LOCK — plan §10.1 row 3."""
        self.assertNotIn(P_DRAFT_CAT, self.listing_ids())
        self.assert_canonical_404(self.pdp(P_DRAFT_CAT), P_DRAFT_CAT)

    def test_archived_category_hides_the_product(self):
        """REGRESSION LOCK — plan §10.1 row 4."""
        self.assertNotIn(P_ARCH_CAT, self.listing_ids())
        self.assert_canonical_404(self.pdp(P_ARCH_CAT), P_ARCH_CAT)

    def test_draft_status_is_never_public(self):
        """REGRESSION LOCK — plan §10.1 row 7; a newly created product is DRAFT."""
        self.assertNotIn(P_DRAFT_STATUS, self.listing_ids())
        self.assert_canonical_404(self.pdp(P_DRAFT_STATUS), P_DRAFT_STATUS)

    def test_submitted_for_review_is_never_public(self):
        """REGRESSION LOCK — PENDING_REVIEW is not a public state."""
        self.assertNotIn(P_PENDING, self.listing_ids())
        self.assert_canonical_404(self.pdp(P_PENDING), P_PENDING)

    def test_approved_but_unpublished_is_never_public(self):
        """
        REGRESSION LOCK — the single most important row in this suite.
        `review.state == APPROVED` with `status == PENDING_REVIEW` is invisible;
        approval is NOT publication (plan §25 criterion 14).
        """
        self.assertNotIn(P_APPROVED, self.listing_ids())
        self.assert_canonical_404(self.pdp(P_APPROVED), P_APPROVED)
        self.assertNotIn(P_APPROVED, self.explore_ids())
        self.assertNotIn(P_APPROVED, self.search_ids())
        self.assertNotIn(P_APPROVED, self.category_ids())

    def test_archived_status_is_never_public(self):
        """REGRESSION LOCK."""
        self.assertNotIn(P_ARCHIVED_STATUS, self.listing_ids())
        self.assert_canonical_404(self.pdp(P_ARCHIVED_STATUS), P_ARCHIVED_STATUS)

    def test_published_status_with_published_flag_false_is_hidden(self):
        """
        REGRESSION LOCK — both halves of the gate are required. A row where the
        two disagree must never leak; `publish_product` writes them together.
        """
        self.assertNotIn(P_FLAG_FALSE, self.listing_ids())
        self.assert_canonical_404(self.pdp(P_FLAG_FALSE), P_FLAG_FALSE)

    def test_list_and_detail_agree_on_every_matrix_row(self):
        """
        Plan §25 criterion 13 — `GET /products` and `GET /products/{id}` agree
        on EVERY row. Asserted as one subtest per product so a disagreement
        names the row.
        """
        listing = self.listing_ids()
        for product_id in sorted(VISIBLE_IDS | HIDDEN_IDS):
            with self.subTest(product=product_id):
                expected_visible = product_id in VISIBLE_IDS
                self.assertEqual(product_id in listing, expected_visible)
                self.assertEqual(
                    self.pdp(product_id).status_code,
                    200 if expected_visible else 404,
                )

    def test_pdp_resolves_by_slug_with_the_same_gate(self):
        """A slug lookup must not be a back door around the gate."""
        self.assertEqual(self.pdp(P_LIVE.lower()).status_code, 200)
        self.assert_canonical_404(self.pdp(P_ARCH_SUB.lower()), P_ARCH_SUB.lower())


# =============================================================================
# 2. PF3-N07 — the fail-open default, asserted as it stands today
# =============================================================================


class FailOpenDefaultTests(_VisibilityCase):
    """
    These four tests DOCUMENT the deferred half of plan §4 item 7.

    They are not an endorsement: they exist so that flipping the default to
    fail-closed is a loud, deliberate, reviewed act (plan §23 R1) rather than a
    silent behaviour change.  When the step 0 reconciliation report exists and
    PF3-N07 is implemented, these expectations invert.
    """

    def test_unresolvable_category_still_fails_open(self):
        self.assertIn(P_UNKNOWN_CAT, self.listing_ids())
        self.assertEqual(self.pdp(P_UNKNOWN_CAT).status_code, 200)

    def test_empty_category_still_fails_open(self):
        self.assertIn(P_NO_CAT, self.listing_ids())
        self.assertEqual(self.pdp(P_NO_CAT).status_code, 200)

    def test_unresolvable_subcategory_fails_open_in_parity_with_category(self):
        """
        The NEW subcategory gate adopts the EXISTING category default rather
        than inventing a stricter one — parity, per plan §10.4(1).
        """
        self.assertIn(P_UNKNOWN_SUB, self.listing_ids())
        self.assertEqual(self.pdp(P_UNKNOWN_SUB).status_code, 200)

    def test_fail_open_is_the_only_gap_between_code_and_criterion_11(self):
        """
        Everything plan §25 criterion 11 demands EXCEPT the fail-closed default
        holds: PUBLISHED + published + ACTIVE category + ACTIVE subcategory.
        """
        visible = self.listing_ids()
        self.assertEqual(visible, VISIBLE_IDS)
        self.assertTrue(visible.isdisjoint(HIDDEN_IDS))


# =============================================================================
# 3. Every public surface applies the same gate
# =============================================================================


class SurfaceParityTests(_VisibilityCase):

    def test_explore_hides_every_unpublished_row(self):
        ids = self.explore_ids()
        for product_id in sorted(HIDDEN_IDS):
            with self.subTest(product=product_id):
                self.assertNotIn(product_id, ids)

    def test_search_hides_every_unpublished_row(self):
        ids = self.search_ids()
        for product_id in sorted(HIDDEN_IDS):
            with self.subTest(product=product_id):
                self.assertNotIn(product_id, ids)

    def test_search_by_term_cannot_surface_a_hidden_product(self):
        """
        A shopper searching the exact product name of a hidden row gets
        nothing — the gate runs before the term match, not after.
        """
        for product_id in (P_ARCH_SUB, P_APPROVED, P_DRAFT_STATUS):
            with self.subTest(product=product_id):
                ids = self.search_ids(q=f"Product {product_id}")
                self.assertNotIn(product_id, ids)

    def test_category_page_hides_every_unpublished_row(self):
        ids = self.category_ids()
        for product_id in sorted(HIDDEN_IDS):
            with self.subTest(product=product_id):
                self.assertNotIn(product_id, ids)

    def test_category_page_shows_the_live_rows_of_that_category(self):
        ids = self.category_ids()
        self.assertIn(P_LIVE, ids)
        self.assertIn(P_LIVE_NOSUB, ids)

    def test_subcategory_facet_filter_cannot_surface_a_hidden_product(self):
        """Asking for the archived subcategory by name returns nothing."""
        ids = self.listing_ids(subcategory=SUB_ARCHIVED)
        self.assertEqual(ids, set())

    def test_explore_search_and_listing_return_the_same_visible_set(self):
        listing = self.listing_ids()
        self.assertEqual(listing, self.explore_ids())
        self.assertEqual(listing, self.search_ids())

    def test_recommendations_apply_the_gate(self):
        """
        `get_recommendations` is the fourth read path plan §10.1 names. Related
        products share the source's category, so the archived-subcategory row
        is a genuine candidate that must still be filtered out.
        """
        response = self.client.get(
            f"/api/v1/products/{P_LIVE}/recommendations", params={"type": "related"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        ids = {item["id"] for item in response.json()["items"]}
        self.assertNotIn(P_ARCH_SUB, ids)
        self.assertNotIn(P_DRAFT_SUB, ids)
        self.assertNotIn(P_APPROVED, ids)
        self.assertNotIn(P_LIVE, ids)  # never recommends the source


# =============================================================================
# 4. Approve ≠ publish, driven through the REAL admin routes
# =============================================================================


class ApproveVersusPublishTests(_VisibilityCase):
    """
    Plan §22.3, walked end to end against the real application, asserting the
    HTTP status, the response body AND the database row at every step.
    """

    seed_matrix = False

    NEW_ID = "PF-VIS-9001"

    def create_draft(self, **overrides):
        payload = {
            "id": self.NEW_ID,
            "name": "Chandheri Silk Saree",
            "sku": self.NEW_ID,
            "price": 8999,
            "category": CAT_ACTIVE,
            "subcategory": SUB_ACTIVE,
            "description": "A real description so the publish gate is satisfied.",
            "image": "https://cdn.test/cover.jpg",
        }
        payload.update(overrides)
        return self.client.post("/api/v1/admin/products/draft", json=payload)

    def action(self, action, product_id=None):
        return self.client.post(
            f"/api/v1/admin/products/{product_id or self.NEW_ID}/{action}", json={}
        )

    def submit(self, product_id=None):
        return self.client.post(
            f"/api/v1/products/{product_id or self.NEW_ID}/submit-review", json={}
        )

    def set_cover(self, product_id=None):
        return self.client.patch(
            f"/api/v1/admin/products/{product_id or self.NEW_ID}",
            json={"image": "https://cdn.test/cover.jpg"},
        )

    def test_a_newly_created_product_is_not_on_the_storefront(self):
        """REGRESSION LOCK — create must never publish."""
        self.assertEqual(self.create_draft().status_code, 201)
        row = self.row(self.NEW_ID)
        self.assertEqual(row.status, "DRAFT")
        self.assertFalse(row.published)
        self.assertNotIn(self.NEW_ID, self.listing_ids())
        self.assert_canonical_404(self.pdp(self.NEW_ID), self.NEW_ID)

    def test_submitting_for_review_does_not_publish(self):
        """REGRESSION LOCK."""
        self.create_draft()
        self.assertEqual(self.submit().status_code, 200)
        row = self.row(self.NEW_ID)
        self.assertEqual(row.status, "PENDING_REVIEW")
        self.assertFalse(row.published)
        self.assertNotIn(self.NEW_ID, self.listing_ids())
        self.assert_canonical_404(self.pdp(self.NEW_ID), self.NEW_ID)

    def test_approve_changes_only_the_review_state(self):
        """
        REGRESSION LOCK — plan §9.2 and §25 criterion 14: approve writes
        `review.state = APPROVED` and NOTHING that affects visibility.
        """
        self.create_draft()
        self.submit()
        before = self.row(self.NEW_ID)
        before_status, before_published = before.status, before.published

        response = self.action("approve")
        self.assertEqual(response.status_code, 200, response.text)

        after = self.row(self.NEW_ID)
        self.assertEqual(after.review.get("state"), "APPROVED")
        self.assertEqual(after.status, before_status)      # still PENDING_REVIEW
        self.assertEqual(after.status, "PENDING_REVIEW")
        self.assertEqual(after.published, before_published)
        self.assertFalse(after.published)
        self.assertIsNone(after.published_at)
        self.assertIsNone(after.published_by)

    def test_approve_does_not_make_the_product_publicly_visible(self):
        """
        REGRESSION LOCK — the storefront consequence of the assertion above,
        checked on every public surface after a real APPROVE call.
        """
        self.create_draft()
        self.submit()
        self.action("approve")
        self.assertNotIn(self.NEW_ID, self.listing_ids())
        self.assertNotIn(self.NEW_ID, self.explore_ids())
        self.assertNotIn(self.NEW_ID, self.search_ids())
        self.assertNotIn(self.NEW_ID, self.category_ids())
        self.assert_canonical_404(self.pdp(self.NEW_ID), self.NEW_ID)

    def test_publish_before_approve_is_refused(self):
        """REGRESSION LOCK — publish is gated on review.state == APPROVED."""
        self.create_draft()
        self.submit()
        response = self.action("publish")
        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertIs(body.get("success"), False)
        self.assertEqual(body["error"]["code"], "BUSINESS_RULE_VIOLATION")
        self.assertFalse(self.row(self.NEW_ID).published)

    def test_publish_is_refused_while_publish_issues_remain(self):
        """REGRESSION LOCK — the cover-image gate, in the canonical envelope."""
        self.create_draft(image="")
        self.submit()
        self.action("approve")
        response = self.action("publish")
        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertEqual(body["error"]["code"], "BUSINESS_RULE_VIOLATION")
        self.assertIn(
            "At least one cover image is required before publishing.",
            body["error"]["details"]["errors"],
        )
        self.assertFalse(self.row(self.NEW_ID).published)
        self.assertNotIn(self.NEW_ID, self.listing_ids())

    def test_explicit_publish_persists_published_and_reveals_the_product(self):
        """
        NEW-BEHAVIOUR-ADJACENT REGRESSION LOCK — the positive half of the gate:
        only an EXPLICIT publish flips the row, and a FRESH request then sees it.
        """
        self.create_draft()
        self.submit()
        self.action("approve")
        self.assertNotIn(self.NEW_ID, self.listing_ids())  # still hidden

        response = self.action("publish")
        self.assertEqual(response.status_code, 200, response.text)

        row = self.row(self.NEW_ID)
        self.assertEqual(row.status, "PUBLISHED")
        self.assertTrue(row.published)
        self.assertIsNotNone(row.published_at)
        self.assertIsNotNone(row.published_by)

        # A fresh read of every surface now includes it.
        self.assertIn(self.NEW_ID, self.listing_ids())
        self.assertIn(self.NEW_ID, self.explore_ids())
        self.assertIn(self.NEW_ID, self.search_ids())
        self.assertIn(self.NEW_ID, self.category_ids())
        self.assertEqual(self.pdp(self.NEW_ID).status_code, 200)

    def test_unpublish_hides_the_product_again_on_a_fresh_request(self):
        """REGRESSION LOCK — PUBLISHED → DRAFT, and the cache does not lie."""
        self.create_draft()
        self.submit()
        self.action("approve")
        self.action("publish")
        self.assertEqual(self.pdp(self.NEW_ID).status_code, 200)  # prime the KV cache

        response = self.action("unpublish")
        self.assertEqual(response.status_code, 200, response.text)
        row = self.row(self.NEW_ID)
        self.assertEqual(row.status, "DRAFT")
        self.assertFalse(row.published)

        self.assertNotIn(self.NEW_ID, self.listing_ids())
        self.assert_canonical_404(self.pdp(self.NEW_ID), self.NEW_ID)

    def test_archive_hides_the_product_again_on_a_fresh_request(self):
        """REGRESSION LOCK."""
        self.create_draft()
        self.submit()
        self.action("approve")
        self.action("publish")
        self.assertEqual(self.pdp(self.NEW_ID).status_code, 200)

        self.assertEqual(self.action("archive").status_code, 200)
        self.assertNotIn(self.NEW_ID, self.listing_ids())
        self.assert_canonical_404(self.pdp(self.NEW_ID), self.NEW_ID)

    def test_approve_and_publish_are_two_distinct_server_calls(self):
        """
        REGRESSION LOCK — there is no single call that takes a submitted
        product all the way live. Publishing always costs a second, explicit,
        separately authorised request.
        """
        self.create_draft()
        self.submit()

        approve = self.action("approve")
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json()["product"]["status"], "PENDING_REVIEW")
        self.assertIs(approve.json()["product"]["published"], False)

        publish = self.action("publish")
        self.assertEqual(publish.status_code, 200)
        self.assertEqual(publish.json()["product"]["status"], "PUBLISHED")
        self.assertIs(publish.json()["product"]["published"], True)


# =============================================================================
# 5. Cache coverage — plan §24 step 7 "extend cache invalidation" / §23 R9
# =============================================================================


class TaxonomyCacheInvalidationTests(_VisibilityCase):
    """
    `get_storefront_product` returns the cached `product:storefront:{key}` DTO
    BEFORE it evaluates the taxonomy gate. A taxonomy write therefore has to
    evict those keys, or the new subcategory gate is bypassable for a whole
    TTL on exactly the transition it exists to catch.
    """

    def archive_subcategory(self, subcategory_id):
        return self.client.post(
            f"/api/v1/admin/subcategories/{subcategory_id}/archive", json={}
        )

    def archive_category(self, category_id):
        return self.client.post(
            f"/api/v1/admin/categories/{category_id}/archive", json={}
        )

    def test_archiving_a_subcategory_hides_a_cached_product_immediately(self):
        """NEW (§24 step 7) — the whole point of the invalidation extension."""
        self.assertEqual(self.pdp(P_LIVE).status_code, 200)  # prime the KV cache

        response = self.archive_subcategory(SUB_ACTIVE)
        self.assertEqual(response.status_code, 200, response.text)

        self.assert_canonical_404(self.pdp(P_LIVE), P_LIVE)
        self.assertNotIn(P_LIVE, self.listing_ids())

    def test_archiving_a_category_hides_a_cached_product_immediately(self):
        """NEW (§24 step 7) — same hole, category level."""
        self.assertEqual(self.pdp(P_LIVE_NOSUB).status_code, 200)

        response = self.archive_category(CAT_ACTIVE)
        self.assertEqual(response.status_code, 200, response.text)

        self.assert_canonical_404(self.pdp(P_LIVE_NOSUB), P_LIVE_NOSUB)
        self.assertNotIn(P_LIVE_NOSUB, self.listing_ids())

    def test_restoring_a_subcategory_shows_the_product_again(self):
        """NEW (§24 step 7) — invalidation works in both directions."""
        self.archive_subcategory(SUB_ACTIVE)
        self.assertEqual(self.pdp(P_LIVE).status_code, 404)

        response = self.client.post(
            f"/api/v1/admin/subcategories/{SUB_ACTIVE}/restore", json={}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.pdp(P_LIVE).status_code, 200)
        self.assertIn(P_LIVE, self.listing_ids())

    def test_restoring_the_archived_subcategory_reveals_its_products(self):
        """
        NEW — the PF3-N06 gate is a gate, not a permanent hide. `restore` takes
        an ARCHIVED subcategory straight back to ACTIVE, and its products
        return to every public surface on the very next request.
        """
        self.assertNotIn(P_ARCH_SUB, self.listing_ids())
        self.assert_canonical_404(self.pdp(P_ARCH_SUB), P_ARCH_SUB)

        response = self.client.post(
            f"/api/v1/admin/subcategories/{SUB_ARCHIVED}/restore", json={}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["subcategory"]["status"], "ACTIVE")

        self.assertIn(P_ARCH_SUB, self.listing_ids())
        self.assertIn(P_ARCH_SUB, self.explore_ids())
        self.assertIn(P_ARCH_SUB, self.search_ids())
        self.assertEqual(self.pdp(P_ARCH_SUB).status_code, 200)

    def test_taxonomy_invalidation_targets_the_product_storefront_namespace(self):
        """
        Static guard on the fix itself: `_invalidate_taxonomy_cache` must clear
        the singular `product:storefront:*` namespace. `invalidate_product_cache`
        uses the glob `*products*`, which does NOT match that prefix — so the
        pattern here cannot be dropped as redundant.
        """
        import inspect

        from app.services.catalog.category_service import CategoryService

        source = inspect.getsource(CategoryService._invalidate_taxonomy_cache)
        self.assertIn('invalidate_pattern("product:storefront:*")', source)
        self.assertIn("invalidate_response_cache()", source)
        self.assertNotIn("product:storefront:", "pratikshya:cache:*products*")


# =============================================================================
# 6. Server authority — the gate lives in the service, not in a caller
# =============================================================================


class ServerAuthorityTests(_VisibilityCase):

    def test_every_storefront_read_path_uses_the_shared_predicate(self):
        """
        Plan §10.1 names four read paths that hand-copied the same category
        predicate. They must now all route through `_taxonomy_visible`, so the
        surfaces cannot drift apart again (plan §25 criterion 13).
        """
        import inspect

        from app.services.catalog.product_service import ProductService

        for name in (
            "list_storefront_products",
            "get_storefront_product",
            "get_recommendations",
            "get_recently_viewed",
        ):
            with self.subTest(read_path=name):
                source = inspect.getsource(getattr(ProductService, name))
                self.assertIn("_taxonomy_visible", source)
                self.assertNotIn(
                    'category_status_map.get(p.category, "ACTIVE")',
                    source,
                    "the hand-copied predicate is back",
                )

    def test_the_gate_is_not_reachable_by_a_client_supplied_parameter(self):
        """
        No query parameter can widen the gate: an explicit `status` filter is
        not part of the public contract and must not resurrect a hidden row.
        """
        for params in (
            {"status": "DRAFT"},
            {"status": "PENDING_REVIEW"},
            {"published": "false"},
            {"category": CAT_ARCHIVED},
            {"subcategory": SUB_ARCHIVED},
        ):
            with self.subTest(params=params):
                ids = self.listing_ids(**params)
                self.assertTrue(ids.isdisjoint(HIDDEN_IDS), f"{params} leaked {ids}")

    def test_the_public_list_never_projects_admin_only_lifecycle_fields(self):
        """
        A storefront row must not carry the review/history trail a client could
        use to infer an unpublished sibling's state.
        """
        response = self.client.get("/api/v1/products", params={"pageSize": 200})
        self.assertEqual(response.status_code, 200)
        for item in response.json()["items"]:
            with self.subTest(product=item["id"]):
                for leaked in ("review", "history", "priceHistory", "createdBy", "updatedBy"):
                    self.assertNotIn(leaked, item)

    def test_a_hidden_product_is_a_404_not_a_403_or_409(self):
        """
        Plan §16.2 — the response is the SAME canonical `NOT_FOUND` a missing
        id produces, so the endpoint cannot be used to enumerate drafts.
        """
        missing = self.pdp("PF-DOES-NOT-EXIST-0001")
        hidden = self.pdp(P_APPROVED)
        self.assertEqual(missing.status_code, hidden.status_code)
        self.assertEqual(
            missing.json()["error"]["code"], hidden.json()["error"]["code"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
