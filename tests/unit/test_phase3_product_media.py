"""
Phase 3 Block 7 — PRODUCT MEDIA HONESTY (plan §24 step 9).

Governing plan: PHASE_3_PRODUCT_CATALOG_IMPLEMENTATION_PLAN.md
  §2.2        — API-085/132 (`namespace` unvalidated), API-086/133 (`role`
                unvalidated), API-125/126/140 (no enum for media role/status)
  §2.3        — PF3-N09: media written through the PRODUCT contract lands only
                in the legacy JSONB columns; reads prefer registered records
  §4 item 16  — "Media `role` / `namespace` allow-lists (product-media only)"
  §11.1/§11.2 — the verified media architecture and its behaviours
  §11.4       — the recommended architecture (DESIGN ONLY — not implemented)
  §21         — `app/api/v1/media.py` ← role / namespace allow-lists;
                `app/schemas/media/*.py` ← the vocabularies, if declared
  §23 R5      — removing the media-write fields is TWO-STAGE by design
  §24 step 9  — "Product media honesty: role/namespace allow-lists; frontend
                stops sending media-write keys; then remove them from
                ProductContentFields; publish gate unchanged during the
                transition."

What this suite pins
────────────────────
NEW BEHAVIOUR (fails if the Block 7 change is reverted)
  1. `role` on `POST /media/register` is checked against a DECLARED closed
     vocabulary.  Before Block 7 the column accepted ANY string — a
     200-character role was accepted into a `String(30)` column, which is an
     HTTP 500 (`StringDataRightTruncation`) on PostgreSQL for what is a
     validation rejection.
  2. A rejected role writes NOTHING: no asset row, no association row, and an
     existing association keeps its previous role.
  3. Surrounding whitespace is trimmed rather than stored.

REGRESSION LOCKS (pass with or without the Block 7 change)
  4. The `namespace` allow-list, which §2.2 reports as missing but which the
     storage layer has enforced all along — all five members accepted, every
     non-member rejected 422 with no object written, and the check is
     case-sensitive.
  5. Every member of the role vocabulary is accepted in the caller's own
     casing, and an empty role still stores the pre-existing default.
  6. Re-registration updates the association in place (Phase 7's contract).
  7. RBAC: a non-privileged admin cannot reach either route, and nothing is
     written when authorization fails.
  8. PF3-N09's CURRENT state, asserted exactly as it is so that the two-stage
     removal in §23 R5 cannot happen silently or by accident.
  9. THE PUBLISH GATE RESOLUTION (approved Option A decision): the gate's
     media branch now also accepts a registered `is_primary=true`
     association, so a registered-only product publishes with NO legacy
     PATCH — while the legacy `image` / `primary_media_id` branch stays as
     the transitional fallback. `role="COVER"` is descriptive and is NOT
     the primary signal, and the media-set "first item" fallback does NOT
     satisfy the gate.

Harness: the REAL media + products routers, the REAL storage provider and the
REAL ORM on a throwaway SQLite file with a temporary media root.  No
migration is involved and no golden data is touched.
"""

import importlib
import io
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

from app.schemas.media.media import (
    DEFAULT_PRODUCT_MEDIA_ROLE,
    MEDIA_UPLOAD_NAMESPACES,
    PRODUCT_MEDIA_ROLE_VALUES,
    coerce_product_media_role,
    is_product_media_role,
    product_media_role_error,
)

HAS_AIOSQLITE = importlib.util.find_spec("aiosqlite") is not None


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):  # pragma: no cover - dialect glue
    return "JSON"


class _PassThroughCache(Backend):
    async def get_with_ttl(self, key):
        return 0, None

    async def get(self, key):
        return None

    async def set(self, key, value, expire=None):
        return None

    async def clear(self, namespace=None, key=None):
        return 0


#: A real, signature-valid 1x1 PNG. The upload path validates content
#: signatures, so fake bytes would be rejected before the vocabulary is
#: reached and the test would prove nothing.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

CAT_ACTIVE = "cat-pf3b7-sarees"
SUB_ACTIVE = "cat-pf3b7-sarees-silk"
PRODUCT_ID = "PF-W-SAR-SIL-0007"


@unittest.skipUnless(HAS_AIOSQLITE, "aiosqlite is not installed")
class _MediaCase(unittest.IsolatedAsyncioTestCase):
    """Real app + real RBAC + real object store on a disposable database."""

    async def asyncSetUp(self):
        importlib.import_module("app.models")
        from app.config import settings
        from app.core.lru_cache_store import init_lru_cache
        from app.models.auth.user import UserModel
        from app.models.base import Base
        from app.models.catalog.category import CategoryModel, SubcategoryModel
        from app.models.catalog.product import ProductModel
        from app.models.media.media_asset import MediaAssetModel
        from app.models.media.product_media import ProductMediaModel
        from app.models.rbac.permission import PermissionModel
        from app.models.rbac.role import RoleModel
        from app.models.rbac.role_permission import RolePermissionModel
        from app.models.rbac.user_role import UserRoleModel
        from app.storage import get_storage_provider, reset_storage_provider

        self._UserModel = UserModel
        self._ProductModel = ProductModel
        self._MediaAssetModel = MediaAssetModel
        self._ProductMediaModel = ProductMediaModel
        self._settings = settings

        init_lru_cache()

        self._tmp = tempfile.TemporaryDirectory(prefix="pf3-media-")
        root = self._tmp.name
        self._saved_media_root = settings.LOCAL_MEDIA_ROOT
        settings.LOCAL_MEDIA_ROOT = os.path.join(root, "media")
        reset_storage_provider()
        self.provider = get_storage_provider()

        self.engine = create_async_engine(f"sqlite+aiosqlite:///{root}/main.sqlite")
        schema_db = os.path.join(root, "pratikshya.sqlite")

        @event.listens_for(self.engine.sync_engine, "connect")
        def _attach(dbapi_conn, _record):  # pragma: no cover - driver hook
            cursor = dbapi_conn.cursor()
            cursor.execute(f"ATTACH DATABASE '{schema_db}' AS pratikshya")
            cursor.close()

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as session:
            # Fine-grained roles, NOT built-in names, so no vocabulary
            # fallback can quietly grant anything.
            media_role = RoleModel(name="PF3B7_MEDIA_ADMIN", description="t", is_system=False)
            plain_role = RoleModel(name="PF3B7_NO_MEDIA", description="t", is_system=False)
            session.add_all([media_role, plain_role])

            permissions = {}
            for code in ("products.view", "products.manage", "media.view",
                         "media.upload", "media.delete"):
                permission = PermissionModel(code=code, name=code,
                                             category="media", description=code)
                session.add(permission)
                permissions[code] = permission
            await session.flush()

            for code in ("products.view", "products.manage", "media.view",
                         "media.upload", "media.delete"):
                session.add(RolePermissionModel(
                    role_id=media_role.id, permission_id=permissions[code].id))
            # The second admin can look, but may not upload or register.
            session.add(RolePermissionModel(
                role_id=plain_role.id, permission_id=permissions["media.view"].id))

            admin = UserModel(
                email="pf3b7-admin@pratikshya.test", full_name="Media Admin",
                hashed_password="x", user_type="admin", status="ACTIVE",
                is_verified=True, force_password_change=False,
            )
            viewer = UserModel(
                email="pf3b7-viewer@pratikshya.test", full_name="Media Viewer",
                hashed_password="x", user_type="admin", status="ACTIVE",
                is_verified=True, force_password_change=False,
            )
            session.add_all([admin, viewer])
            await session.flush()
            session.add(UserRoleModel(user_id=admin.id, role_id=media_role.id))
            session.add(UserRoleModel(user_id=viewer.id, role_id=plain_role.id))

            session.add(CategoryModel(id=CAT_ACTIVE, name="Sarees",
                                      slug="sarees", status="ACTIVE"))
            session.add(SubcategoryModel(id=SUB_ACTIVE, category_id=CAT_ACTIVE,
                                         name="Silk", slug="silk", status="ACTIVE"))
            session.add(ProductModel(
                id=PRODUCT_ID, product_id=PRODUCT_ID,
                name="Banarasi Silk Saree", slug="pf3b7-banarasi-silk",
                sku=PRODUCT_ID, category=CAT_ACTIVE, subcategory=SUB_ACTIVE,
                price=4999, status="DRAFT", published=False,
                description="A real description for the publish gate.",
            ))
            await session.commit()
            self.admin_id = admin.id
            self.viewer_id = viewer.id

        self.app = self._build_app()
        self.client = TestClient(self.app)
        self.as_admin()

    async def asyncTearDown(self):
        from app.storage import reset_storage_provider

        reset_storage_provider()
        self._settings.LOCAL_MEDIA_ROOT = self._saved_media_root
        FastAPICache.reset()
        await self.engine.dispose()
        self._tmp.cleanup()

    def _build_app(self):
        from app.api.v1.media import router as media_router
        from app.api.v1.products import router as products_router
        from app.core.error_handlers import register_error_handlers
        from app.dependencies import get_current_user, get_db

        app = FastAPI()
        register_error_handlers(app)
        FastAPICache.init(backend=_PassThroughCache(), prefix="pf3-media-test")
        app.include_router(media_router, prefix="/api/v1")
        app.include_router(products_router, prefix="/api/v1")

        Session = self.Session
        UserModel = self._UserModel
        self._actor = None

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
                        select(UserModel).where(UserModel.id == self._actor)
                    )
                ).scalars().first()

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_current_user
        return app

    # ── actors ───────────────────────────────────────────────────────────────

    def as_admin(self):
        self._actor = self.admin_id

    def as_view_only_admin(self):
        self._actor = self.viewer_id

    # ── helpers ──────────────────────────────────────────────────────────────

    def upload_for_product(self, filename="cover.png", product_id=PRODUCT_ID):
        return self.client.post(
            f"/api/v1/media/products/{product_id}/objects",
            files={"file": (filename, io.BytesIO(PNG_BYTES), "image/png")},
        )

    def upload(self, filename="plate.png", **form):
        return self.client.post(
            "/api/v1/media/objects",
            files={"file": (filename, io.BytesIO(PNG_BYTES), "image/png")},
            data=form,
        )

    def register(self, object_key, *, product_id=PRODUCT_ID, role=None,
                 sort_order=0, is_primary=False):
        form = {"object_key": object_key, "sort_order": str(sort_order)}
        if product_id is not None:
            form["product_id"] = product_id
        if role is not None:
            form["role"] = role
        if is_primary:
            form["is_primary"] = "true"
        return self.client.post("/api/v1/media/register", data=form)

    async def mappings(self, product_id=PRODUCT_ID):
        async with self.Session() as session:
            return (
                await session.execute(
                    select(self._ProductMediaModel).where(
                        self._ProductMediaModel.product_id == product_id
                    )
                )
            ).scalars().all()

    async def assets(self):
        async with self.Session() as session:
            return (
                await session.execute(select(self._MediaAssetModel))
            ).scalars().all()

    async def product_row(self, product_id=PRODUCT_ID):
        async with self.Session() as session:
            return (
                await session.execute(
                    select(self._ProductModel).where(
                        self._ProductModel.id == product_id
                    )
                )
            ).scalars().first()

    def assert_canonical_422(self, response):
        """The Phase 1 canonical business-rule envelope — no second format."""
        self.assertEqual(response.status_code, 422, response.text)
        body = response.json()
        self.assertIs(body.get("success"), False, body)
        error = body.get("error") or {}
        self.assertEqual(error.get("code"), "BUSINESS_RULE_VIOLATION", body)
        self.assertTrue((error.get("message") or "").strip(), body)
        blob = response.text.lower()
        for leak in ("traceback", "sqlalchemy", "select ", "psycopg", "asyncpg"):
            self.assertNotIn(leak, blob, f"internal detail leaked: {leak}")
        return error


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE DECLARED VOCABULARY  (plan §4 item 16, §21 "if declared")
# ═══════════════════════════════════════════════════════════════════════════

class RoleVocabularyDeclarationTests(unittest.TestCase):
    """The vocabulary exists, is closed, and is derived rather than invented."""

    def test_vocabulary_is_the_frontend_declared_role_set(self):
        """
        DERIVED, NOT INVENTED.  `frontend/src/config/mediaTypes.js` is the only
        place the product-media role vocabulary was ever written down; this is
        that set.  If the two ever diverge, the frontend suite says so.
        """
        self.assertEqual(
            PRODUCT_MEDIA_ROLE_VALUES,
            ("COVER", "GALLERY", "DETAIL", "LIFESTYLE", "MODEL", "CLOSEUP",
             "PRODUCT_VIDEO", "SHOWCASE", "DETAIL_VIDEO", "LIFESTYLE_VIDEO"),
        )

    def test_vocabulary_has_no_duplicates_and_fits_the_column(self):
        self.assertEqual(len(set(PRODUCT_MEDIA_ROLE_VALUES)),
                         len(PRODUCT_MEDIA_ROLE_VALUES))
        for value in PRODUCT_MEDIA_ROLE_VALUES:
            # media_product_media.role is String(30). A member that did not fit
            # would be a guaranteed PostgreSQL write failure.
            self.assertLessEqual(len(value), 30, value)
            self.assertTrue(value.strip() == value, value)

    def test_the_default_role_is_a_member(self):
        """The pre-existing `Form`/column default must survive its own check."""
        self.assertTrue(is_product_media_role(DEFAULT_PRODUCT_MEDIA_ROLE))

    def test_backend_primary_role_constant_is_a_member(self):
        """
        `product_media_records.PRIMARY_ROLE` is the backend's own idea of the
        cover role. It has no call sites, but it is evidence about the intended
        vocabulary, and it agrees with it.
        """
        from app.services.media.product_media_records import PRIMARY_ROLE

        self.assertTrue(is_product_media_role(PRIMARY_ROLE))

    def test_membership_is_case_insensitive(self):
        for value in PRODUCT_MEDIA_ROLE_VALUES:
            self.assertTrue(is_product_media_role(value))
            self.assertTrue(is_product_media_role(value.lower()))
            self.assertTrue(is_product_media_role(value.title()))

    def test_non_members_are_rejected_by_the_helper(self):
        for junk in ("hero", "banner", "gallery-2", "COVER ART", "커버",
                     "x" * 200, "COVER'; DROP TABLE--", "0", "null"):
            with self.subTest(junk=junk):
                self.assertFalse(is_product_media_role(junk))
                with self.assertRaises(ValueError):
                    coerce_product_media_role(junk)

    def test_coercion_preserves_casing_and_trims(self):
        self.assertEqual(coerce_product_media_role("COVER"), "COVER")
        self.assertEqual(coerce_product_media_role("gallery"), "gallery")
        self.assertEqual(coerce_product_media_role("  detail  "), "detail")

    def test_empty_role_falls_back_to_the_pre_existing_default(self):
        """Not a rejection: this is exactly what the route did before."""
        for empty in (None, "", "   ", "\t"):
            with self.subTest(empty=repr(empty)):
                self.assertEqual(coerce_product_media_role(empty),
                                 DEFAULT_PRODUCT_MEDIA_ROLE)

    def test_the_error_message_names_every_allowed_value(self):
        message = product_media_role_error("nonsense")
        self.assertIn("nonsense", message)
        for value in PRODUCT_MEDIA_ROLE_VALUES:
            self.assertIn(value, message)

    def test_namespace_vocabulary_mirrors_the_storage_layer(self):
        """
        Re-exported, not redeclared: a second copy would be free to drift from
        the copy that actually enforces.
        """
        from app.storage.keys import ALLOWED_NAMESPACES

        self.assertEqual(MEDIA_UPLOAD_NAMESPACES, tuple(ALLOWED_NAMESPACES))
        self.assertEqual(MEDIA_UPLOAD_NAMESPACES,
                         ("products", "collections", "hero", "marketing", "uploads"))


# ═══════════════════════════════════════════════════════════════════════════
# 2. ROLE ALLOW-LIST ON THE REAL ROUTE  (API-086 / API-133)
# ═══════════════════════════════════════════════════════════════════════════

class RegisterRoleAllowListTests(_MediaCase):

    async def test_every_declared_role_is_accepted_in_the_callers_casing(self):
        up = self.upload_for_product("roles.png")
        self.assertEqual(up.status_code, 201, up.text)
        key = up.json()["object"]["key"]

        for index, value in enumerate(PRODUCT_MEDIA_ROLE_VALUES):
            for casing in (value, value.lower()):
                with self.subTest(role=casing):
                    response = self.register(key, role=casing)
                    self.assertEqual(response.status_code, 201, response.text)
                    self.assertEqual(response.json()["assignment"]["role"], casing)
                    rows = await self.mappings()
                    self.assertEqual(len(rows), 1, "re-registration must not duplicate")
                    self.assertEqual(rows[0].role, casing)

    async def test_an_unknown_role_is_a_canonical_422_and_writes_nothing(self):
        up = self.upload_for_product("unknown.png")
        key = up.json()["object"]["key"]

        response = self.register(key, role="hero-banner")
        error = self.assert_canonical_422(response)
        self.assertIn("hero-banner", error["message"])
        self.assertIn("COVER", error["message"])

        self.assertEqual(len(await self.mappings()), 0,
                         "a rejected role must not create an association")
        self.assertEqual(len(await self.assets()), 0,
                         "a rejected role must not create an asset row")

    async def test_a_role_longer_than_the_column_is_rejected_not_truncated(self):
        """
        NEW BEHAVIOUR.  `media_product_media.role` is `String(30)`.  Before
        Block 7 a 200-character role was written straight through: SQLite
        stores it, PostgreSQL raises `StringDataRightTruncation`, which the
        error handler renders as HTTP 500 — a 500 for a validation rejection.
        """
        up = self.upload_for_product("long.png")
        key = up.json()["object"]["key"]

        response = self.register(key, role="x" * 200)
        self.assert_canonical_422(response)
        self.assertEqual(len(await self.mappings()), 0)

    async def test_a_rejected_role_leaves_an_existing_association_untouched(self):
        up = self.upload_for_product("keep.png")
        key = up.json()["object"]["key"]
        self.assertEqual(self.register(key, role="COVER").status_code, 201)

        before = (await self.mappings())[0]
        self.assertEqual(before.role, "COVER")

        response = self.register(key, role="not-a-role", sort_order=99, is_primary=True)
        self.assert_canonical_422(response)

        after = await self.mappings()
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0].role, "COVER", "role must not change")
        self.assertEqual(after[0].sort_order, before.sort_order,
                         "a rejected call must not apply its other fields either")
        self.assertEqual(after[0].is_primary, before.is_primary)

    async def test_surrounding_whitespace_is_trimmed(self):
        up = self.upload_for_product("trim.png")
        key = up.json()["object"]["key"]
        response = self.register(key, role="  GALLERY  ")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual((await self.mappings())[0].role, "GALLERY")

    async def test_an_empty_role_still_stores_the_default(self):
        """REGRESSION LOCK — pre-existing behaviour, deliberately unchanged."""
        up = self.upload_for_product("empty.png")
        key = up.json()["object"]["key"]
        response = self.register(key, role="")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual((await self.mappings())[0].role, DEFAULT_PRODUCT_MEDIA_ROLE)

    async def test_an_omitted_role_still_stores_the_default(self):
        up = self.upload_for_product("omitted.png")
        key = up.json()["object"]["key"]
        response = self.register(key, role=None)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual((await self.mappings())[0].role, DEFAULT_PRODUCT_MEDIA_ROLE)

    async def test_role_is_validated_even_without_a_product(self):
        """
        The asset half of the route runs before any product is looked at, so
        the check must not hide behind `if product_id:`.
        """
        up = self.upload_for_product("noproduct.png")
        key = up.json()["object"]["key"]
        response = self.register(key, product_id=None, role="not-a-role")
        self.assert_canonical_422(response)
        self.assertEqual(len(await self.assets()), 0)

    async def test_reregistration_updates_the_role_in_place(self):
        """REGRESSION LOCK — Phase 7's idempotency contract still holds."""
        up = self.upload_for_product("inplace.png")
        key = up.json()["object"]["key"]
        self.assertEqual(self.register(key, role="gallery").status_code, 201)
        updated = self.register(key, role="detail", sort_order=3, is_primary=True)
        self.assertEqual(updated.status_code, 201, updated.text)
        body = updated.json()
        self.assertEqual(body["assignment"]["role"], "detail")
        self.assertEqual(body["assignment"]["sortOrder"], 3)
        self.assertTrue(body["assignment"]["isPrimary"])
        self.assertEqual(len(await self.mappings()), 1)
        self.assertEqual(len(await self.assets()), 1)


# ═══════════════════════════════════════════════════════════════════════════
# 3. NAMESPACE ALLOW-LIST  (API-085 / API-132)
# ═══════════════════════════════════════════════════════════════════════════

class NamespaceAllowListTests(_MediaCase):
    """
    §2.2 records `namespace` as unvalidated.  That is a shallow read of
    `UploadService.store_upload(namespace: str = "products")`: the allow-list
    lives one layer down in `MediaService.object_key_for_upload` →
    `app.storage.keys.ALLOWED_NAMESPACES`, and it has always been enforced.
    These are regression locks on behaviour that already existed.
    """

    async def test_every_declared_namespace_is_accepted(self):
        for index, namespace in enumerate(MEDIA_UPLOAD_NAMESPACES):
            with self.subTest(namespace=namespace):
                if namespace == "products":
                    response = self.upload(f"ns{index}.png", namespace=namespace,
                                           productId=PRODUCT_ID)
                else:
                    response = self.upload(f"ns{index}.png", namespace=namespace)
                self.assertEqual(response.status_code, 201, response.text)
                key = response.json()["object"]["key"]
                self.assertTrue(key.startswith(f"{namespace}/"), key)

    async def test_a_namespace_outside_the_vocabulary_is_a_canonical_422(self):
        for junk in ("evil", "../etc", "secrets", "..", "products/../secrets"):
            with self.subTest(namespace=junk):
                response = self.upload("bad.png", namespace=junk)
                error = self.assert_canonical_422(response)
                self.assertIn("namespace", error["message"].lower())

    async def test_the_namespace_check_is_case_sensitive(self):
        """
        Unlike `role`, the namespace vocabulary is an object-key path segment,
        and object keys are lowercase by construction.
        """
        response = self.upload("upper.png", namespace="PRODUCTS")
        self.assert_canonical_422(response)

    async def test_a_rejected_namespace_writes_no_object(self):
        before = sorted(os.listdir(self._settings.LOCAL_MEDIA_ROOT)) \
            if os.path.isdir(self._settings.LOCAL_MEDIA_ROOT) else []
        self.assert_canonical_422(self.upload("nope.png", namespace="evil"))
        after = sorted(os.listdir(self._settings.LOCAL_MEDIA_ROOT)) \
            if os.path.isdir(self._settings.LOCAL_MEDIA_ROOT) else []
        self.assertEqual(before, after)

    async def test_the_products_namespace_still_requires_a_product_id(self):
        response = self.upload("orphan.png", namespace="products")
        error = self.assert_canonical_422(response)
        self.assertIn("product id", error["message"].lower())

    async def test_the_per_product_upload_route_cannot_be_told_a_namespace(self):
        """
        `POST /media/products/{id}/objects` hard-codes `namespace="products"`,
        which is what makes it un-spoofable. A namespace field in the body is
        ignored rather than honoured.
        """
        response = self.client.post(
            f"/api/v1/media/products/{PRODUCT_ID}/objects",
            files={"file": ("scoped.png", io.BytesIO(PNG_BYTES), "image/png")},
            data={"namespace": "hero"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["object"]["key"].startswith("products/"))


# ═══════════════════════════════════════════════════════════════════════════
# 4. AUTHORIZATION  (existing RBAC only — no new permission vocabulary)
# ═══════════════════════════════════════════════════════════════════════════

class MediaVocabularyRbacTests(_MediaCase):

    async def test_an_admin_without_media_upload_cannot_register(self):
        up = self.upload_for_product("rbac.png")
        key = up.json()["object"]["key"]

        self.as_view_only_admin()
        response = self.register(key, role="COVER")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(len(await self.mappings()), 0)

    async def test_an_admin_without_media_upload_cannot_upload(self):
        self.as_view_only_admin()
        response = self.upload("rbac2.png", namespace="hero")
        self.assertEqual(response.status_code, 403, response.text)

    async def test_authorization_is_checked_before_the_vocabulary(self):
        """
        An unauthorized caller must not be able to probe the vocabulary: the
        answer to "is FOO a legal role?" is 403, not a helpful 422.
        """
        up = self.upload_for_product("probe.png")
        key = up.json()["object"]["key"]
        self.as_view_only_admin()
        response = self.register(key, role="definitely-not-a-role")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertNotIn("COVER", response.text)


# ═══════════════════════════════════════════════════════════════════════════
# 5. THE DECLARATION IS VISIBLE IN THE CONTRACT
# ═══════════════════════════════════════════════════════════════════════════

class MediaContractDeclarationTests(_MediaCase):

    def _form_properties(self, path):
        spec = self.app.openapi()
        content = spec["paths"][path]["post"]["requestBody"]["content"]
        schema = content[list(content)[0]]["schema"]
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            return spec["components"]["schemas"][name]["properties"]
        return schema.get("properties", {})

    async def test_namespace_carries_a_real_enum(self):
        """Accurate: the namespace check IS case-sensitive and exact."""
        namespace = self._form_properties("/api/v1/media/objects")["namespace"]
        self.assertEqual(namespace.get("enum"), list(MEDIA_UPLOAD_NAMESPACES))

    async def test_role_declares_its_vocabulary_in_the_description(self):
        """
        Deliberately a description and NOT a JSON Schema `enum`: matching is
        case-insensitive, and `enum` means "exactly one of these", so an enum
        here would be a contract that the implementation does not honour.
        """
        role = self._form_properties("/api/v1/media/register")["role"]
        self.assertIsNone(role.get("enum"))
        for value in PRODUCT_MEDIA_ROLE_VALUES:
            self.assertIn(value, role["description"])
        self.assertEqual(role.get("default"), DEFAULT_PRODUCT_MEDIA_ROLE)

    async def test_the_registered_read_model_does_not_enum_the_role(self):
        """
        A response enum would make a legacy row with an out-of-vocabulary role
        unserialisable — an HTTP 500 on read. Exactly the R6 hazard, so the
        allow-list is a WRITE-path control only.
        """
        spec = self.app.openapi()
        item = spec["components"]["schemas"]["RegisteredProductMediaItem"]
        self.assertIsNone(item["properties"]["role"].get("enum"))


# ═══════════════════════════════════════════════════════════════════════════
# 6. PF3-N09 — THE CURRENT, UNCHANGED STATE OF THE PRODUCT MEDIA WRITE PATH
# ═══════════════════════════════════════════════════════════════════════════

class ProductMediaWriteHonestyTests(_MediaCase):
    """
    Plan §24 step 9 also asks for the media-write keys to be removed from
    `ProductContentFields`, in two stages (§23 R5).  Neither stage shipped in
    Block 7 — see the report §11 for why.  These tests assert the CURRENT
    state exactly, so that stage 1 and stage 2 have to be deliberate.
    """

    async def test_the_product_contract_still_accepts_the_media_write_keys(self):
        response = self.client.patch(
            f"/api/v1/admin/products/{PRODUCT_ID}",
            json={"mediaIds": ["asset-x", "asset-y"],
                  "primaryMediaId": "asset-x",
                  "galleryMediaIds": ["asset-y"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        row = await self.product_row()
        self.assertEqual(row.media_ids, ["asset-x", "asset-y"])
        self.assertEqual(row.primary_media_id, "asset-x")
        self.assertEqual(row.gallery_media_ids, ["asset-y"])

    async def test_the_media_write_keys_reach_only_the_legacy_columns(self):
        """PF3-N09: no `media_product_media` row is ever created this way."""
        self.client.patch(
            f"/api/v1/admin/products/{PRODUCT_ID}",
            json={"mediaIds": ["ghost-1"], "primaryMediaId": "ghost-1"},
        )
        self.assertEqual(len(await self.mappings()), 0)
        self.assertEqual(len(await self.assets()), 0)
        row = await self.product_row()
        self.assertEqual(row.image, "", "the authored plate is not touched")

    async def test_registered_records_win_the_read_while_the_claims_persist(self):
        """
        The dishonesty in one assertion: the product advertises media ids that
        correspond to no asset at all, while the resolved set comes from the
        registered records.
        """
        self.client.patch(
            f"/api/v1/admin/products/{PRODUCT_ID}",
            json={"mediaIds": ["ghost-1"], "primaryMediaId": "ghost-1"},
        )
        up = self.upload_for_product("real.png")
        self.register(up.json()["object"]["key"], role="COVER", is_primary=True)

        body = self.client.get(f"/api/v1/media/products/{PRODUCT_ID}/media-set").json()
        self.assertEqual(body["mediaIds"], ["ghost-1"], "the fiction is still served")
        self.assertTrue(body["mediaRecordsAvailable"])
        self.assertEqual(len(body["mediaItems"]), 1)
        self.assertNotIn("ghost-1", [item["mediaId"] for item in body["mediaItems"]])

    async def test_the_write_keys_are_still_declared_on_the_write_schema(self):
        """Stage 2 (removal) has NOT happened — asserted so it cannot drift."""
        spec = self.app.openapi()
        for schema_name in ("ProductUpdateRequest", "ProductCreateRequest"):
            properties = spec["components"]["schemas"][schema_name]["properties"]
            for key in ("mediaIds", "primaryMediaId", "galleryMediaIds"):
                with self.subTest(schema=schema_name, key=key):
                    self.assertIn(key, properties)


# ═══════════════════════════════════════════════════════════════════════════
# 7. THE BLOCKER — THE PUBLISH GATE IS BLIND TO REGISTERED MEDIA
# ═══════════════════════════════════════════════════════════════════════════

class PublishGateMediaSourceTests(_MediaCase):
    """
    The Block 7 blocker, now RESOLVED (approved decision: Option A).

    The gate's media branch is:

        authored `product.image`
        OR legacy `product.primary_media_id`
        OR a registered `media_product_media` association with
           `is_primary = True`

    `role` text — including `"COVER"` — is descriptive and is deliberately
    NOT the primary signal, and the media-set "first item" fallback does NOT
    satisfy the gate. The legacy branch is retained as the transitional
    fallback (plan §11.4 item 3), so legacy-only products keep publishing,
    and `POST /media/register` still writes nothing onto the product row
    (the Phase 7 contract lock below stays green).

    Before this resolution `test_registered_primary_media_does_not_satisfy_the_publish_gate`
    pinned the broken behaviour; it has been rewritten — deliberately, not
    by accident — into the accepted behaviour, exactly as the Block 7 report
    said the resolution would announce itself.
    """

    def _cover_error(self, issues):
        return "At least one cover image is required before publishing." in issues

    def _publish_issues(self):
        return self.client.get(
            f"/api/v1/admin/products/{PRODUCT_ID}/publish-issues"
        ).json()["issues"]

    async def _register_one(self, filename, *, role, is_primary):
        up = self.upload_for_product(filename)
        self.assertEqual(up.status_code, 201, up.text)
        registered = self.register(
            up.json()["object"]["key"], role=role, is_primary=is_primary
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        return registered

    async def _submit_and_approve(self):
        submitted = self.client.post(f"/api/v1/products/{PRODUCT_ID}/submit-review")
        self.assertEqual(submitted.status_code, 200, submitted.text)
        approved = self.client.post(f"/api/v1/admin/products/{PRODUCT_ID}/approve")
        self.assertEqual(approved.status_code, 200, approved.text)

    async def _assert_publish_succeeds(self):
        published = self.client.post(f"/api/v1/admin/products/{PRODUCT_ID}/publish")
        self.assertEqual(published.status_code, 200, published.text)
        body = published.json()["product"]
        self.assertEqual(body["status"], "PUBLISHED")
        self.assertIs(body["published"], True)
        return body

    # ── Matrix A: no media at all ────────────────────────────────────────────

    async def test_no_media_at_all_keeps_the_gate_closed(self):
        issues = self._publish_issues()
        self.assertTrue(self._cover_error(issues), issues)

    # ── Matrix B: registered NON-primary media only ──────────────────────────

    async def test_registered_non_primary_media_does_not_satisfy_the_publish_gate(self):
        await self._register_one("gallery-only.png", role="gallery", is_primary=False)
        rows = await self.mappings()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].is_primary)
        issues = self._publish_issues()
        self.assertTrue(self._cover_error(issues), issues)

    # ── Matrix C + H + K: registered primary, legacy empty, no PATCH ─────────

    async def test_registered_primary_media_alone_satisfies_the_publish_gate(self):
        """
        THE regression test of this block. Before the gate resolution this
        asserted the OPPOSITE — that registered primary media did NOT satisfy
        the gate (Block 7 §23). Now it proves, with no legacy PATCH anywhere:

        · the association exists with is_primary=True
        · the legacy `image` / `primary_media_id` columns stay empty
        · publish-issues carries no cover blocker
        · publish itself succeeds
        """
        registered = await self._register_one(
            "registered-cover.png", role="COVER", is_primary=True
        )

        rows = await self.mappings()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].is_primary)

        row = await self.product_row()
        self.assertEqual((row.image or "").strip(), "")
        self.assertIsNone(row.primary_media_id)
        self.assertEqual(row.media_ids or [], [])

        mediaset = self.client.get(
            f"/api/v1/media/products/{PRODUCT_ID}/media-set").json()
        self.assertTrue(mediaset["mediaRecordsAvailable"])
        self.assertTrue(mediaset["primaryMediaUrl"])
        # media-set's `primaryMediaId` is the legacy column echo (still None);
        # the registered primary's id lives in the ordered mediaItems.
        self.assertEqual(
            mediaset["mediaItems"][0]["mediaId"], registered.json()["media"]["id"]
        )
        self.assertIsNone(mediaset["primaryMediaId"])

        issues = self._publish_issues()
        self.assertFalse(self._cover_error(issues), issues)

        await self._submit_and_approve()
        await self._assert_publish_succeeds()

        # A published row whose media came solely from the association.
        after = await self.product_row()
        self.assertEqual(after.status, "PUBLISHED")
        self.assertIs(after.published, True)
        self.assertEqual((after.image or "").strip(), "")
        self.assertIsNone(after.primary_media_id)

    # ── Matrix D: exactly one primary among several ──────────────────────────

    async def test_multiple_registered_media_with_exactly_one_primary_satisfy_the_gate(self):
        await self._register_one("cover.png", role="COVER", is_primary=True)
        await self._register_one("angle.png", role="GALLERY", is_primary=False)
        await self._register_one("detail.png", role="DETAIL", is_primary=False)

        rows = await self.mappings()
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(1 for r in rows if r.is_primary), 1)

        issues = self._publish_issues()
        self.assertFalse(self._cover_error(issues), issues)

        await self._submit_and_approve()
        await self._assert_publish_succeeds()

    # ── Matrix E: registered rows but ZERO primaries ─────────────────────────

    async def test_zero_registered_primaries_do_not_satisfy_the_gate(self):
        await self._register_one("one.png", role="GALLERY", is_primary=False)
        await self._register_one("two.png", role="DETAIL", is_primary=False)

        rows = await self.mappings()
        self.assertEqual(len(rows), 2)
        self.assertFalse(any(r.is_primary for r in rows))

        # The media-set read model FALLS BACK to the first item — and that
        # fallback must NOT leak into the publish gate.
        mediaset = self.client.get(
            f"/api/v1/media/products/{PRODUCT_ID}/media-set").json()
        self.assertTrue(mediaset["mediaRecordsAvailable"])
        self.assertTrue(mediaset["primaryMediaUrl"])

        issues = self._publish_issues()
        self.assertTrue(self._cover_error(issues), issues)

    # ── Matrix I: role=COVER is descriptive, not the primary signal ──────────

    async def test_cover_role_without_primary_does_not_satisfy_the_gate(self):
        await self._register_one("cover-role.png", role="COVER", is_primary=False)
        rows = await self.mappings()
        self.assertEqual(rows[0].role, "COVER")
        self.assertFalse(rows[0].is_primary)
        issues = self._publish_issues()
        self.assertTrue(self._cover_error(issues), issues)

    # ── Matrix J: is_primary=True with a non-COVER role ──────────────────────

    async def test_primary_with_a_non_cover_role_satisfies_the_gate(self):
        await self._register_one("detail-primary.png", role="DETAIL", is_primary=True)
        rows = await self.mappings()
        self.assertEqual(rows[0].role, "DETAIL")
        self.assertTrue(rows[0].is_primary)
        issues = self._publish_issues()
        self.assertFalse(self._cover_error(issues), issues)

        await self._submit_and_approve()
        await self._assert_publish_succeeds()

    # ── Matrix F + G: the legacy branches remain transitional sources ────────

    async def test_the_legacy_columns_do_satisfy_the_publish_gate(self):
        """The other half of the proof: the gate reads product columns only."""
        self.client.patch(f"/api/v1/admin/products/{PRODUCT_ID}",
                          json={"primaryMediaId": "anything-at-all"})
        issues = self._publish_issues()
        self.assertNotIn("At least one cover image is required before publishing.",
                         issues)

    async def test_an_authored_legacy_image_satisfies_the_publish_gate(self):
        self.client.patch(f"/api/v1/admin/products/{PRODUCT_ID}",
                          json={"image": "/images/products/legacy/plate.jpg"})
        issues = self._publish_issues()
        self.assertFalse(self._cover_error(issues), issues)

    # ── Matrix L + M: fresh reads agree with the published record ────────────

    async def test_publish_result_survives_a_fresh_admin_read(self):
        registered = await self._register_one(
            "fresh-read.png", role="COVER", is_primary=True
        )
        await self._submit_and_approve()
        await self._assert_publish_succeeds()

        fresh = self.client.get(
            f"/api/v1/admin/products/{PRODUCT_ID}").json()["product"]
        self.assertEqual(fresh["status"], "PUBLISHED")
        self.assertIs(fresh["published"], True)
        self.assertEqual(fresh["primaryMediaId"], registered.json()["media"]["id"])
        self.assertTrue(fresh["image"].endswith("fresh-read.png"), fresh["image"])

    async def test_storefront_resolves_the_same_registered_primary(self):
        await self._register_one("storefront-cover.png", role="COVER", is_primary=True)
        await self._submit_and_approve()
        await self._assert_publish_succeeds()

        mediaset = self.client.get(
            f"/api/v1/media/products/{PRODUCT_ID}/media-set").json()
        storefront = self.client.get(
            f"/api/v1/products/{PRODUCT_ID}").json()["product"]
        self.assertEqual(storefront["image"], mediaset["primaryMediaUrl"])

    # ── Matrix N: registration still invalidates the cached storefront DTO ───

    async def test_registering_a_new_primary_invalidates_the_product_cache(self):
        import unittest.mock as mock

        from app.services.catalog.product_service import ProductService

        await self._register_one("first-cover.png", role="COVER", is_primary=True)
        calls = []

        async def _spy(self_, product_id, slug=None):
            calls.append((product_id, slug))

        with mock.patch.object(
            ProductService, "invalidate_product_cache", new=_spy
        ):
            await self._register_one("second-cover.png", role="COVER", is_primary=True)

        self.assertEqual(calls, [(PRODUCT_ID, "pf3b7-banarasi-silk")], calls)

        # And the fresh read model reflects the new primary immediately.
        mediaset = self.client.get(
            f"/api/v1/media/products/{PRODUCT_ID}/media-set").json()
        self.assertTrue(mediaset["primaryMediaUrl"].endswith("second-cover.png"),
                        mediaset["primaryMediaUrl"])

    # ── Matrix O: pre-migration fallback (media tables unavailable) ──────────

    async def test_an_unavailable_media_read_falls_back_to_the_legacy_branch(self):
        import unittest.mock as mock
        from sqlalchemy.exc import SQLAlchemyError

        from app.services.catalog import product_service

        def _boom(db, product_id):
            raise SQLAlchemyError("media_product_media is not available")

        with mock.patch.object(
            product_service, "registered_media_for_product", side_effect=_boom
        ):
            # Legacy-populated product: the gate must stay open, not 500.
            self.client.patch(f"/api/v1/admin/products/{PRODUCT_ID}",
                              json={"image": "/images/products/legacy/plate.jpg"})
            response = self.client.get(
                f"/api/v1/admin/products/{PRODUCT_ID}/publish-issues")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(self._cover_error(response.json()["issues"]))

            # Legacy-empty product: the canonical issue must still be returned.
            self.client.patch(f"/api/v1/admin/products/{PRODUCT_ID}",
                              json={"image": ""})
            response = self.client.get(
                f"/api/v1/admin/products/{PRODUCT_ID}/publish-issues")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(self._cover_error(response.json()["issues"]))

    async def test_registering_media_does_not_write_the_product_row(self):
        """
        Registration is transactionally complete on its own tables. Nothing
        server-side projects it onto the product — the Phase 7 contract lock
        that Option A keeps green. The publish gate now READS the
        association instead of needing the product row written.
        """
        before = await self.product_row()
        snapshot = (before.image, before.primary_media_id,
                    before.media_ids, before.gallery_media_ids)

        up = self.upload_for_product("noproject.png")
        self.register(up.json()["object"]["key"], role="COVER", is_primary=True)

        after = await self.product_row()
        self.assertEqual(
            (after.image, after.primary_media_id,
             after.media_ids, after.gallery_media_ids),
            snapshot,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
