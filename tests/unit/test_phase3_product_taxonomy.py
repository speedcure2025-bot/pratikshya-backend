"""
Phase 3 Block 2 — the PRODUCT ↔ TAXONOMY contract.

The frontend now emits real server taxonomy ids (Block 1). This suite pins the
other half of that contract: the backend is the authoritative validator, and a
product can NEVER be created or updated with an unknown, unauthorised or
mismatched category/subcategory reference.

Rules under test (PHASE_3_PRODUCT_CATALOG_IMPLEMENTATION_PLAN.md §12.3, §16.2,
§22.1):

  * a reference resolves against `catalog_category` / `catalog_subcategory` by
    id, then slug, then name — anything else is rejected;
  * only an ACTIVE node may be ASSIGNED (DRAFT/ARCHIVED are rejected), and the
    rule applies only to the field the request actually writes;
  * the subcategory must BELONG to the resulting category — the pair is
    validated, not just the existence of two ids;
  * what is stored is the canonical row id;
  * every rejection is HTTP 422 `VALIDATION_ERROR` in the Phase 1 envelope with
    FastAPI-shaped field details — never a 500, never a raw database error.

Everything runs against the REAL routers, REAL service and REAL ORM models on a
throwaway SQLite file (the same dialect shim the Phase 6/7 suites use — no
PostgreSQL server is reachable in this sandbox). No migration is involved: the
columns are unchanged `String(100)` values and the enforcement is entirely in
the service layer.
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

CAT_A = "cat-sarees"                    # ACTIVE
CAT_B = "cat-lehengas"                  # ACTIVE
CAT_DRAFT = "cat-new-season"            # DRAFT
CAT_ARCHIVED = "cat-legacy"             # ARCHIVED

SUB_A_ACTIVE = "cat-sarees-banarasi"    # ACTIVE, belongs to CAT_A
SUB_A_DRAFT = "cat-sarees-upcoming"     # DRAFT,  belongs to CAT_A
SUB_A_ARCHIVED = "cat-sarees-vintage"   # ARCHIVED, belongs to CAT_A
SUB_B_ACTIVE = "cat-lehengas-bridal"    # ACTIVE, belongs to CAT_B


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
class TaxonomyContractCase(unittest.IsolatedAsyncioTestCase):
    """Real app + real taxonomy rows on a disposable SQLite database."""

    async def asyncSetUp(self):
        importlib.import_module("app.models")  # registers every mapped class
        from app.models.auth.user import UserModel
        from app.models.base import Base
        from app.models.catalog.category import CategoryModel, SubcategoryModel
        from app.models.catalog.product import ProductModel

        self._UserModel = UserModel
        self._ProductModel = ProductModel

        self._tmp = tempfile.TemporaryDirectory(prefix="pf3-taxonomy-")
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
                email="pf3-taxonomy-admin@pratikshya.test",
                full_name="Taxonomy Admin",
                hashed_password="x",
                user_type="admin",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            session.add(admin)

            session.add_all([
                CategoryModel(id=CAT_A, name="Sarees", slug="sarees", status="ACTIVE"),
                CategoryModel(id=CAT_B, name="Lehengas", slug="lehengas", status="ACTIVE"),
                CategoryModel(id=CAT_DRAFT, name="New Season", slug="new-season", status="DRAFT"),
                CategoryModel(id=CAT_ARCHIVED, name="Legacy", slug="legacy", status="ARCHIVED"),
            ])
            session.add_all([
                SubcategoryModel(
                    id=SUB_A_ACTIVE, category_id=CAT_A,
                    name="Banarasi", slug="banarasi", status="ACTIVE",
                ),
                SubcategoryModel(
                    id=SUB_A_DRAFT, category_id=CAT_A,
                    name="Upcoming", slug="upcoming", status="DRAFT",
                ),
                SubcategoryModel(
                    id=SUB_A_ARCHIVED, category_id=CAT_A,
                    name="Vintage", slug="vintage", status="ARCHIVED",
                ),
                SubcategoryModel(
                    id=SUB_B_ACTIVE, category_id=CAT_B,
                    name="Bridal", slug="bridal", status="ACTIVE",
                ),
            ])
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
        FastAPICache.init(backend=_PassThroughCache(), prefix="pf3-taxonomy-test")
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

    def create_draft(self, product_id, **overrides):
        payload = {
            "id": product_id,
            "name": f"Product {product_id}",
            "sku": product_id,
            "price": 4999,
            "category": CAT_A,
            "subcategory": SUB_A_ACTIVE,
        }
        payload.update(overrides)
        payload = {k: v for k, v in payload.items() if v is not ...}
        return self.client.post("/api/v1/admin/products/draft", json=payload)

    def create_runtime(self, **overrides):
        payload = {"name": "Runtime Product", "category": CAT_A, "subcategory": SUB_A_ACTIVE}
        payload.update(overrides)
        return self.client.post("/api/v1/admin/products", json=payload)

    def patch_product(self, product_id, body):
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

    def assert_taxonomy_rejection(self, response, field, error_type):
        """Every rejection is the Phase 1 envelope — 422, never 500."""
        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertIs(body["success"], False)
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertTrue(body["error"]["message"])
        details = body["error"]["details"]
        self.assertIsInstance(details, list)
        self.assertEqual(len(details), 1, details)
        entry = details[0]
        self.assertEqual(entry["loc"], ["body", field])
        self.assertEqual(entry["field"], field)
        self.assertEqual(entry["type"], error_type)
        self.assertTrue(entry["msg"])
        # No SQL, no driver noise, no traceback ever reaches the operator.
        rendered = response.text.lower()
        for leak in ("traceback", "select ", "sqlalchemy", "integrityerror", "sqlite"):
            self.assertNotIn(leak, rendered, leak)
        return body


# ===========================================================================
# CREATE — POST /admin/products/draft
# ===========================================================================

class DraftCreateTaxonomyTests(TaxonomyContractCase):
    def test_active_pair_is_accepted_and_stored_as_canonical_ids(self):
        response = self.create_draft("PF-TAX-0001")
        self.assertEqual(response.status_code, 201, response.text)
        product = response.json()["product"]
        self.assertEqual(product["status"], "DRAFT")
        self.assertFalse(product["published"])
        self.assertEqual(product["category"], CAT_A)
        self.assertEqual(product["subcategory"], SUB_A_ACTIVE)
        row = self.row("PF-TAX-0001")
        self.assertEqual(row.category, CAT_A)
        self.assertEqual(row.subcategory, SUB_A_ACTIVE)

    def test_slug_and_name_references_canonicalise_to_ids(self):
        response = self.create_draft("PF-TAX-0002", category="sarees", subcategory="Banarasi")
        self.assertEqual(response.status_code, 201, response.text)
        row = self.row("PF-TAX-0002")
        self.assertEqual(row.category, CAT_A, "a slug reference is stored as the id")
        self.assertEqual(row.subcategory, SUB_A_ACTIVE, "a name reference is stored as the id")

    def test_unknown_category_is_rejected(self):
        response = self.create_draft("PF-TAX-0003", category="no-such-category")
        self.assert_taxonomy_rejection(
            response, "category", "value_error.taxonomy.unknown_category"
        )
        self.assertIsNone(self.row("PF-TAX-0003"), "no row may be created")

    def test_unknown_subcategory_is_rejected(self):
        response = self.create_draft("PF-TAX-0004", subcategory="no-such-subcategory")
        self.assert_taxonomy_rejection(
            response, "subcategory", "value_error.taxonomy.unknown_subcategory"
        )
        self.assertIsNone(self.row("PF-TAX-0004"))

    def test_subcategory_of_another_category_is_rejected(self):
        # FLOW D — category A + a subcategory that belongs to category B.
        response = self.create_draft("PF-TAX-0005", category=CAT_A, subcategory=SUB_B_ACTIVE)
        body = self.assert_taxonomy_rejection(
            response, "subcategory", "value_error.taxonomy.subcategory_category_mismatch"
        )
        self.assertIn("does not belong", body["error"]["message"])
        self.assertIsNone(self.row("PF-TAX-0005"))

    def test_draft_category_cannot_be_assigned(self):
        response = self.create_draft("PF-TAX-0006", category=CAT_DRAFT, subcategory="")
        body = self.assert_taxonomy_rejection(
            response, "category", "value_error.taxonomy.category_status"
        )
        self.assertIn("DRAFT", body["error"]["message"])

    def test_archived_category_cannot_be_assigned(self):
        response = self.create_draft("PF-TAX-0007", category=CAT_ARCHIVED, subcategory="")
        body = self.assert_taxonomy_rejection(
            response, "category", "value_error.taxonomy.category_status"
        )
        self.assertIn("ARCHIVED", body["error"]["message"])

    def test_archived_subcategory_cannot_be_assigned(self):
        response = self.create_draft("PF-TAX-0008", subcategory=SUB_A_ARCHIVED)
        body = self.assert_taxonomy_rejection(
            response, "subcategory", "value_error.taxonomy.subcategory_status"
        )
        self.assertIn("ARCHIVED", body["error"]["message"])

    def test_draft_subcategory_cannot_be_assigned(self):
        response = self.create_draft("PF-TAX-0009", subcategory=SUB_A_DRAFT)
        self.assert_taxonomy_rejection(
            response, "subcategory", "value_error.taxonomy.subcategory_status"
        )

    def test_subcategory_without_a_category_is_rejected(self):
        response = self.create_draft("PF-TAX-0010", category="", subcategory=SUB_A_ACTIVE)
        self.assert_taxonomy_rejection(
            response, "subcategory", "value_error.taxonomy.subcategory_without_category"
        )

    def test_taxonomy_is_optional_on_a_draft(self):
        """No category and no subcategory is still a legal DRAFT."""
        response = self.create_draft("PF-TAX-0011", category="", subcategory="")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.row("PF-TAX-0011").category, "")

    def test_null_taxonomy_on_create_falls_back_to_the_column_default(self):
        response = self.create_draft("PF-TAX-0012", category=None, subcategory=None)
        self.assertEqual(response.status_code, 201, response.text)
        row = self.row("PF-TAX-0012")
        # Unchanged pre-existing contract: `category` is NOT NULL with a ""
        # default and `subcategory` carries the same column default.
        self.assertEqual(row.category, "")
        self.assertEqual(row.subcategory, "")

    def test_omitted_taxonomy_on_create_is_accepted(self):
        payload = {"id": "PF-TAX-0013", "name": "No taxonomy", "sku": "PF-TAX-0013"}
        response = self.client.post("/api/v1/admin/products/draft", json=payload)
        self.assertEqual(response.status_code, 201, response.text)

    def test_a_rejected_create_never_returns_500(self):
        for value in ("", "   ", "0", "null", "1; DROP TABLE catalog_product"):
            response = self.create_draft("PF-TAX-0099", category=value, subcategory="")
            self.assertNotEqual(response.status_code, 500, response.text)


# ===========================================================================
# CREATE — POST /admin/products (runtime id path)
# ===========================================================================

class RuntimeCreateTaxonomyTests(TaxonomyContractCase):
    def test_runtime_create_accepts_a_valid_pair(self):
        response = self.create_runtime()
        self.assertEqual(response.status_code, 201, response.text)
        product = response.json()["product"]
        self.assertEqual(product["category"], CAT_A)
        self.assertEqual(product["subcategory"], SUB_A_ACTIVE)

    def test_runtime_create_rejects_an_unknown_category(self):
        response = self.create_runtime(category="ghost-category")
        self.assert_taxonomy_rejection(
            response, "category", "value_error.taxonomy.unknown_category"
        )

    def test_runtime_create_rejects_an_unknown_subcategory(self):
        response = self.create_runtime(subcategory="ghost-subcategory")
        self.assert_taxonomy_rejection(
            response, "subcategory", "value_error.taxonomy.unknown_subcategory"
        )

    def test_runtime_create_rejects_a_mismatched_pair(self):
        response = self.create_runtime(subcategory=SUB_B_ACTIVE)
        self.assert_taxonomy_rejection(
            response, "subcategory", "value_error.taxonomy.subcategory_category_mismatch"
        )

    def test_runtime_create_rejects_an_archived_category(self):
        response = self.create_runtime(category=CAT_ARCHIVED, subcategory="")
        self.assert_taxonomy_rejection(
            response, "category", "value_error.taxonomy.category_status"
        )

    def test_both_create_paths_share_one_rule(self):
        """Neither create endpoint may be the lenient one."""
        draft = self.create_draft("PF-TAX-0201", category="ghost")
        runtime = self.create_runtime(category="ghost")
        self.assertEqual(draft.status_code, 422, draft.text)
        self.assertEqual(runtime.status_code, 422, runtime.text)
        self.assertEqual(
            draft.json()["error"]["details"][0]["type"],
            runtime.json()["error"]["details"][0]["type"],
        )


# ===========================================================================
# UPDATE — PATCH /admin/products/{id}
# ===========================================================================

class PatchTaxonomyTests(TaxonomyContractCase):
    def setUp_product(self, product_id="PF-TAX-1000", **overrides):
        response = self.create_draft(product_id, **overrides)
        self.assertEqual(response.status_code, 201, response.text)
        return product_id

    # — category only ————————————————————————————————————————————————

    def test_category_only_patch_is_validated_against_the_stored_subcategory(self):
        pid = self.setUp_product("PF-TAX-1001")
        # CAT_B is ACTIVE but the stored subcategory belongs to CAT_A.
        response = self.patch_product(pid, {"category": CAT_B})
        self.assert_taxonomy_rejection(
            response, "category", "value_error.taxonomy.subcategory_category_mismatch"
        )
        row = self.row(pid)
        self.assertEqual(row.category, CAT_A, "a rejected patch writes nothing")
        self.assertEqual(row.subcategory, SUB_A_ACTIVE)

    def test_category_only_patch_succeeds_when_the_pair_still_holds(self):
        pid = self.setUp_product("PF-TAX-1002", category="sarees", subcategory="banarasi")
        response = self.patch_product(pid, {"category": "sarees"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.row(pid).category, CAT_A)

    def test_category_only_patch_rejects_an_unknown_category(self):
        pid = self.setUp_product("PF-TAX-1003")
        response = self.patch_product(pid, {"category": "ghost-category"})
        self.assert_taxonomy_rejection(
            response, "category", "value_error.taxonomy.unknown_category"
        )

    def test_category_only_patch_rejects_an_archived_category(self):
        pid = self.setUp_product("PF-TAX-1004")
        response = self.patch_product(pid, {"category": CAT_ARCHIVED})
        self.assert_taxonomy_rejection(
            response, "category", "value_error.taxonomy.category_status"
        )

    # — subcategory only ————————————————————————————————————————————

    def test_subcategory_only_patch_is_validated_against_the_stored_category(self):
        pid = self.setUp_product("PF-TAX-1005")
        response = self.patch_product(pid, {"subcategory": SUB_B_ACTIVE})
        self.assert_taxonomy_rejection(
            response, "subcategory", "value_error.taxonomy.subcategory_category_mismatch"
        )
        self.assertEqual(self.row(pid).subcategory, SUB_A_ACTIVE)

    def test_subcategory_only_patch_rejects_an_unknown_subcategory(self):
        pid = self.setUp_product("PF-TAX-1006")
        response = self.patch_product(pid, {"subcategory": "ghost"})
        self.assert_taxonomy_rejection(
            response, "subcategory", "value_error.taxonomy.unknown_subcategory"
        )

    def test_subcategory_only_patch_rejects_an_archived_subcategory(self):
        pid = self.setUp_product("PF-TAX-1007")
        response = self.patch_product(pid, {"subcategory": SUB_A_ARCHIVED})
        self.assert_taxonomy_rejection(
            response, "subcategory", "value_error.taxonomy.subcategory_status"
        )

    def test_subcategory_only_patch_accepts_a_sibling_of_the_stored_category(self):
        pid = self.setUp_product("PF-TAX-1008")
        response = self.patch_product(pid, {"subcategory": "banarasi"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.row(pid).subcategory, SUB_A_ACTIVE)

    # — both ————————————————————————————————————————————————————————

    def test_patching_both_validates_the_resulting_pair(self):
        pid = self.setUp_product("PF-TAX-1009")
        ok = self.patch_product(pid, {"category": CAT_B, "subcategory": SUB_B_ACTIVE})
        self.assertEqual(ok.status_code, 200, ok.text)
        row = self.row(pid)
        self.assertEqual(row.category, CAT_B)
        self.assertEqual(row.subcategory, SUB_B_ACTIVE)

        bad = self.patch_product(pid, {"category": CAT_A, "subcategory": SUB_B_ACTIVE})
        self.assert_taxonomy_rejection(
            bad, "subcategory", "value_error.taxonomy.subcategory_category_mismatch"
        )
        self.assertEqual(self.row(pid).category, CAT_B, "the rejected pair wrote nothing")

    # — omitted / null ——————————————————————————————————————————————

    def test_omitted_taxonomy_is_never_validated_and_never_written(self):
        """PATCH stays PATCH: an untouched pair is not re-litigated."""
        pid = self.setUp_product("PF-TAX-1010")
        # Archive the category behind the product's back — a content-only save
        # must still succeed, because it does not assign anything.
        import asyncio

        from app.models.catalog.category import CategoryModel

        async def _archive():
            async with self.Session() as session:
                category = (
                    await session.execute(
                        select(CategoryModel).where(CategoryModel.id == CAT_A)
                    )
                ).scalars().first()
                category.status = "ARCHIVED"
                await session.commit()

        asyncio.get_event_loop().run_until_complete(_archive())

        response = self.patch_product(pid, {"name": "Renamed only"})
        self.assertEqual(response.status_code, 200, response.text)
        row = self.row(pid)
        self.assertEqual(row.name, "Renamed only")
        self.assertEqual(row.category, CAT_A, "an omitted field is untouched")
        self.assertEqual(row.subcategory, SUB_A_ACTIVE)

    def test_explicit_null_category_follows_the_existing_nullable_contract(self):
        """`category` is a NOT NULL column with a default: null is a no-op."""
        pid = self.setUp_product("PF-TAX-1011")
        response = self.patch_product(pid, {"category": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.row(pid).category, CAT_A)

    def test_explicit_null_subcategory_clears_the_nullable_field(self):
        pid = self.setUp_product("PF-TAX-1012")
        response = self.patch_product(pid, {"subcategory": None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.row(pid).subcategory)

    def test_clearing_the_subcategory_of_a_legacy_row_is_not_blocked(self):
        """A free-text legacy category must not trap a subcategory clear."""
        pid = self.setUp_product("PF-TAX-1013")

        import asyncio

        async def _legacy():
            async with self.Session() as session:
                product = (
                    await session.execute(
                        select(self._ProductModel).where(self._ProductModel.id == pid)
                    )
                ).scalars().first()
                product.category = "legacy-free-text"
                await session.commit()

        asyncio.get_event_loop().run_until_complete(_legacy())

        response = self.patch_product(pid, {"subcategory": ""})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.row(pid).subcategory, "")

    # — response contract ————————————————————————————————————————————

    def test_the_admin_read_returns_the_canonical_ids(self):
        pid = self.setUp_product("PF-TAX-1014", category="sarees", subcategory="Banarasi")
        response = self.client.get(f"/api/v1/admin/products/{pid}")
        self.assertEqual(response.status_code, 200, response.text)
        product = response.json()["product"]
        self.assertEqual(product["category"], CAT_A)
        self.assertEqual(product["subcategory"], SUB_A_ACTIVE)

    def test_a_rejected_patch_never_returns_500(self):
        pid = self.setUp_product("PF-TAX-1015")
        for body in (
            {"category": "ghost"},
            {"subcategory": "ghost"},
            {"category": CAT_A, "subcategory": SUB_B_ACTIVE},
            {"category": CAT_DRAFT, "subcategory": ""},
            {"category": "", "subcategory": SUB_A_ACTIVE},
        ):
            response = self.patch_product(pid, body)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")


# ===========================================================================
# The employee write path shares the same rule
# ===========================================================================

class EmployeeUpdateTaxonomyTests(TaxonomyContractCase):
    """`PATCH /employee/products/{id}` whitelists category/subcategory too."""

    async def _service_and_product(self, session, product_id="PF-TAX-2001"):
        from app.services.catalog.product_service import ProductService

        response = self.create_draft(product_id)
        self.assertEqual(response.status_code, 201, response.text)
        product = (
            await session.execute(
                select(self._ProductModel).where(self._ProductModel.id == product_id)
            )
        ).scalars().first()
        product.assigned_employee_id = "EMP-1"
        await session.commit()
        return ProductService(session), product

    async def test_employee_patch_rejects_an_unknown_category(self):
        from app.core.exceptions import ValidationException
        from app.schemas.catalog.product import EmployeeProductUpdateRequest

        async with self.Session() as session:
            service, product = await self._service_and_product(session, "PF-TAX-2001")
            with self.assertRaises(ValidationException) as caught:
                await service.update_product_employee(
                    product.id,
                    EmployeeProductUpdateRequest(category="ghost-category"),
                    employee_id="EMP-1",
                )
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual(caught.exception.error_code, "VALIDATION_ERROR")
            self.assertEqual(caught.exception.details[0]["loc"], ["body", "category"])

    async def test_employee_patch_rejects_a_mismatched_pair(self):
        from app.core.exceptions import ValidationException
        from app.schemas.catalog.product import EmployeeProductUpdateRequest

        async with self.Session() as session:
            service, product = await self._service_and_product(session, "PF-TAX-2002")
            with self.assertRaises(ValidationException):
                await service.update_product_employee(
                    product.id,
                    EmployeeProductUpdateRequest(subcategory=SUB_B_ACTIVE),
                    employee_id="EMP-1",
                )

    async def test_employee_patch_accepts_and_canonicalises_a_valid_pair(self):
        from app.schemas.catalog.product import EmployeeProductUpdateRequest

        async with self.Session() as session:
            service, product = await self._service_and_product(session, "PF-TAX-2003")
            updated = await service.update_product_employee(
                product.id,
                EmployeeProductUpdateRequest(category="lehengas", subcategory="bridal"),
                employee_id="EMP-1",
            )
            self.assertEqual(updated.category, CAT_B)
            self.assertEqual(updated.subcategory, SUB_B_ACTIVE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
