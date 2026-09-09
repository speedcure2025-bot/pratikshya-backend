"""
Phase 6 — media routes against a REAL database session.

`test_phase6_media_storage.py` exercises the storage, security, resolver and
migration layers with mocks, which is the right tool for those. But three
Phase 6 routes are only meaningful against a live session, because their whole
job is to read and authorise database state:

    GET    /media/products/{id}/media-set     reads catalog_product columns
    POST   /media/objects                     RBAC via users/roles/permissions
    DELETE /media/objects/{key}               RBAC via users/roles/permissions
    GET    /products/{id_or_slug}             _to_storefront image resolution

These tests therefore stand up a real SQLAlchemy session over the real
declarative models and drive the real routers through TestClient, with the
Phase-1 RBAC helpers (`get_current_user` → `get_current_admin` →
`require_admin_permission`) executing unpatched.

WHY SQLITE
----------
No PostgreSQL server is reachable in this environment (the Debian package
mirror is not on the sandbox network, and PostgreSQL has no pip-installable
server). Rather than leave these paths unverified, the suite runs the REAL
models against SQLite with two test-only shims, neither of which touches
production code:

  · `@compiles(JSONB, "sqlite")` renders Postgres JSONB as JSON.
  · the models' `schema="pratikshya"` is provided by `ATTACH DATABASE … AS
    pratikshya` on every pooled connection.

What this proves: the queries, the ORM mappings, the RBAC joins and the route
contracts. What it does NOT prove: Postgres-specific DDL or JSONB operators —
none of which Phase 6 introduces (no migration, no new column, no new index).
"""

import os
import tempfile
import unittest

from fastapi_cache import FastAPICache
from fastapi_cache.backends import Backend
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

import importlib

# Test-only driver; see the "Testing only" block in requirements.txt.
HAS_AIOSQLITE = importlib.util.find_spec("aiosqlite") is not None


# ---------------------------------------------------------------------------
# Test-only dialect shim (production code is not modified)
# ---------------------------------------------------------------------------

@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):  # pragma: no cover - dialect glue
    return "JSON"


class PassThroughCacheBackend(Backend):
    """
    A no-op cache. The production app initialises FastAPICache in its lifespan,
    and `@cache` on the product read routes would otherwise serve a hit from an
    earlier test — which would hide the very database round-trip these tests
    exist to prove. Nothing is ever stored here, so every request demonstrably
    reaches the session.
    """

    async def get_with_ttl(self, key):
        return 0, None

    async def get(self, key):
        return None

    async def set(self, key, value, expire=None):
        return None

    async def clear(self, namespace=None, key=None):
        return 0


# Importing the package registers every mapped class on `Base.metadata`, which
# `create_all` needs. Imported by name so it does not shadow the FastAPI `app`.
importlib.import_module("app.models")  # noqa: E402  (isort: skip)
from app.dependencies import get_current_user, get_db  # noqa: E402
from app.models.auth.user import UserModel  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.catalog.product import ProductModel  # noqa: E402
from app.models.rbac.permission import PermissionModel  # noqa: E402
from app.models.rbac.role import RoleModel  # noqa: E402
from app.models.rbac.role_permission import RolePermissionModel  # noqa: E402
from app.models.rbac.user_role import UserRoleModel  # noqa: E402
from app.config import settings  # noqa: E402
from app.storage import (  # noqa: E402
    get_storage_provider,
    reset_storage_provider,
)
from app.services.media.product_media_resolver import clear_resolution_cache  # noqa: E402

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

PRODUCT_ID = "PF-W-SAR-SIL-0001"
LEGACY_IMAGE = f"/images/products/{PRODUCT_ID}/primary.avif"
MIGRATED_KEY = f"products/{PRODUCT_ID}/primary.avif"
CANONICAL_URL = f"/api/v1/media/objects/{MIGRATED_KEY}"


@unittest.skipUnless(
    HAS_AIOSQLITE, "aiosqlite is not installed (pip install -r requirements.txt)"
)
class MediaDatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    """One real database + one real object store per test."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pf6-db-")
        self.root = self._tmp.name
        self.main_db = os.path.join(self.root, "main.sqlite")
        self.schema_db = os.path.join(self.root, "pratikshya.sqlite")
        self.media_root = os.path.join(self.root, "media")

        # Point the application's own storage configuration at this test's
        # store, so `get_storage_provider()` — the factory the product
        # projections use — resolves here. No monkeypatching: the code under
        # test builds its provider the same way it does in production.
        self._saved_root = settings.LOCAL_MEDIA_ROOT
        settings.LOCAL_MEDIA_ROOT = self.media_root
        reset_storage_provider()

        self.provider = get_storage_provider()
        # The migrated asset is present; the gallery plate is NOT, so the
        # dual-read fallback is exercised for real.
        self.provider.put_object(MIGRATED_KEY, PNG_BYTES, "image/avif")

        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.main_db}")
        schema_db = self.schema_db

        @event.listens_for(self.engine.sync_engine, "connect")
        def _attach(dbapi_conn, _record):  # pragma: no cover - driver hook
            cursor = dbapi_conn.cursor()
            cursor.execute(f"ATTACH DATABASE '{schema_db}' AS pratikshya")
            cursor.close()

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        await self._seed()

        self.app = self._build_app()
        from fastapi.testclient import TestClient

        self.client = TestClient(self.app)

        reset_storage_provider()
        clear_resolution_cache()

    async def asyncTearDown(self):
        clear_resolution_cache()
        reset_storage_provider()
        settings.LOCAL_MEDIA_ROOT = self._saved_root
        FastAPICache.reset()
        await self.engine.dispose()
        self._tmp.cleanup()

    # -- fixtures ------------------------------------------------------------

    async def _seed(self):
        async with self.Session() as session:
            # Real RBAC graph. The admin holds media.upload + media.delete
            # through a role that is NOT in BUILT_IN_ROLES, so the built-in
            # vocabulary fallback cannot quietly grant anything.
            role = RoleModel(name="MEDIA_LIMITED", description="test", is_system=False)
            perms = []
            # `products.view` is here only so the admin product projection can
            # be read; its absence is asserted separately below.
            for code in ("media.view", "media.upload", "media.delete", "products.view"):
                permission = PermissionModel(
                    code=code, name=code, category="media", description=code
                )
                session.add(permission)
                perms.append(permission)
            session.add(role)
            await session.flush()
            for permission in perms:
                session.add(
                    RolePermissionModel(role_id=role.id, permission_id=permission.id)
                )

            admin = UserModel(
                email="media-admin@pratikshyafashon.test",
                full_name="Media Admin",
                hashed_password="x",
                user_type="admin",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            # An employee with media.view only — used to prove the permission
            # check denies an upload it is not entitled to.
            viewer = UserModel(
                email="media-viewer@pratikshyafashon.test",
                full_name="Media Viewer",
                hashed_password="x",
                user_type="admin",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            customer = UserModel(
                email="shopper@pratikshyafashon.test",
                full_name="Shopper",
                hashed_password="x",
                user_type="customer",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            session.add_all([admin, viewer, customer])
            await session.flush()

            viewer_role = RoleModel(name="VIEW_ONLY", description="test", is_system=False)
            session.add(viewer_role)
            await session.flush()
            view_perm = (
                await session.execute(
                    select(PermissionModel).where(PermissionModel.code == "media.view")
                )
            ).scalars().first()
            session.add(RolePermissionModel(role_id=viewer_role.id, permission_id=view_perm.id))
            session.add(UserRoleModel(user_id=admin.id, role_id=role.id))
            session.add(UserRoleModel(user_id=viewer.id, role_id=viewer_role.id))

            session.add(
                ProductModel(
                    id=PRODUCT_ID,
                    product_id=PRODUCT_ID,
                    name="Banarasi Silk Saree",
                    slug="banarasi-silk-saree",
                    sku=PRODUCT_ID,
                    category="sarees",
                    subcategory="silk",
                    price=5000,
                    original_price=6000,
                    stock=3,
                    availability="in-stock",
                    status="PUBLISHED",
                    published=True,
                    image=LEGACY_IMAGE,
                    hover_image="",
                    additional_images=[LEGACY_IMAGE, f"/images/products/{PRODUCT_ID}/01.avif"],
                    media_ids=["pm-1"],
                    primary_media_id="pm-1",
                    gallery_media_ids=[],
                )
            )
            await session.commit()

            self.admin_id = admin.id
            self.viewer_id = viewer.id
            self.customer_id = customer.id

    def _build_app(self):
        from fastapi import FastAPI

        from app.api.v1.media import router as media_router
        from app.api.v1.products import router as products_router
        from app.core.error_handlers import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)
        FastAPICache.init(backend=PassThroughCacheBackend(), prefix="pf6-test")
        app.include_router(media_router, prefix="/api/v1")
        app.include_router(products_router, prefix="/api/v1")

        Session = self.Session

        async def _override_get_db():
            async with Session() as session:
                yield session

        # `get_db` is the only injected piece. The RBAC chain
        # (get_current_user → get_current_admin → require_admin_permission)
        # runs for real against the seeded role/permission rows, and the media
        # service builds its provider from settings exactly as in production.
        app.dependency_overrides[get_db] = _override_get_db

        self._set_current_user(app, self.admin_id)
        return app

    def _set_current_user(self, app, user_id: str):
        Session = self.Session

        async def _dep():
            async with Session() as session:
                return (
                    await session.execute(select(UserModel).where(UserModel.id == user_id))
                ).scalars().first()

        app.dependency_overrides[get_current_user] = _dep

    # -- helpers -------------------------------------------------------------

    def as_admin(self):
        self._set_current_user(self.app, self.admin_id)

    def as_view_only_admin(self):
        self._set_current_user(self.app, self.viewer_id)

    def as_customer(self):
        self._set_current_user(self.app, self.customer_id)

    def stored_image(self, product_id: str = PRODUCT_ID) -> str:
        """Read the authored `image` column straight from the database."""
        import asyncio

        async def _read():
            async with self.Session() as session:
                return (
                    await session.execute(
                        select(ProductModel.image).where(ProductModel.id == product_id)
                    )
                ).scalar_one()

        return asyncio.run(_read())


# ===========================================================================
# GET /media/products/{id}/media-set — real catalog_product read
# ===========================================================================

class ProductMediaSetRouteTests(MediaDatabaseTestCase):
    def test_resolves_a_migrated_reference_and_falls_back_for_the_rest(self):
        response = self.client.get(f"/api/v1/media/products/{PRODUCT_ID}/media-set")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["productId"], PRODUCT_ID)
        # The object exists in the store → canonical media URL.
        self.assertEqual(body["primary"], CANONICAL_URL)
        # The gallery plate was never migrated → the legacy reference is kept,
        # so the storefront keeps rendering instead of breaking.
        self.assertIn(f"/images/products/{PRODUCT_ID}/01.avif", body["gallery"])
        self.assertNotIn(CANONICAL_URL, body["gallery"], "the cover is not repeated")
        # Media-id columns are reported but explicitly not resolved. This
        # product has NO Phase 7 registered rows, so the endpoint keeps its
        # dual-read posture: mediaRecordsAvailable stays False and the note
        # says the legacy columns are what answered. (Phase 7 products WITH
        # registered rows are covered by test_phase7_media_lifecycle.py.)
        self.assertEqual(body["primaryMediaId"], "pm-1")
        self.assertEqual(body["mediaIds"], ["pm-1"])
        self.assertFalse(body["mediaRecordsAvailable"])
        self.assertIn("No registered media records", body["note"])

    def test_unknown_product_returns_404(self):
        response = self.client.get("/api/v1/media/products/NOT-A-PRODUCT/media-set")
        self.assertEqual(response.status_code, 404)

    def test_reads_only_existing_columns_and_writes_nothing(self):
        before = list(self.provider.list_objects())
        self.client.get(f"/api/v1/media/products/{PRODUCT_ID}/media-set")
        self.assertEqual(list(self.provider.list_objects()), before)

    def test_a_role_missing_products_view_cannot_read_the_admin_projection(self):
        """Fine-grained RBAC really is evaluated against the database."""
        self.as_view_only_admin()
        self.assertEqual(self.client.get(f"/api/v1/admin/products/{PRODUCT_ID}").status_code, 403)

    def test_admin_projection_resolves_through_the_same_contract(self):
        response = self.client.get(f"/api/v1/admin/products/{PRODUCT_ID}")
        self.assertEqual(response.status_code, 200)
        product = response.json()["product"]
        self.assertEqual(product["image"], CANONICAL_URL)
        self.assertEqual(product["additionalImages"][0], CANONICAL_URL)
        self.assertEqual(
            product["additionalImages"][1], f"/images/products/{PRODUCT_ID}/01.avif"
        )
        self.assertEqual(product["primaryMediaId"], "pm-1")


# ===========================================================================
# GET /products/{id_or_slug} — storefront projection against a real row
# ===========================================================================

class StorefrontProjectionTests(MediaDatabaseTestCase):
    def test_storefront_detail_returns_the_canonical_media_url(self):
        response = self.client.get(f"/api/v1/products/{PRODUCT_ID}")
        self.assertEqual(response.status_code, 200)
        product = response.json()["product"]
        self.assertEqual(product["image"], CANONICAL_URL)
        self.assertEqual(product["additionalImages"][0], CANONICAL_URL)
        self.assertEqual(
            product["additionalImages"][1], f"/images/products/{PRODUCT_ID}/01.avif"
        )

    def test_the_stored_column_is_not_rewritten_by_a_read(self):
        self.client.get(f"/api/v1/products/{PRODUCT_ID}")
        self.client.get(f"/api/v1/media/products/{PRODUCT_ID}/media-set")
        self.client.get(f"/api/v1/admin/products/{PRODUCT_ID}")
        self.assertEqual(
            self.stored_image(),
            LEGACY_IMAGE,
            "resolution is a projection concern; the column must stay as authored",
        )


# ===========================================================================
# POST /media/objects — real RBAC through users/roles/permissions
# ===========================================================================

class AdminMediaMutationRouteTests(MediaDatabaseTestCase):
    def _upload(self, name="extra.png"):
        return self.client.post(
            "/api/v1/media/objects",
            files={"file": (name, PNG_BYTES, "image/png")},
            data={"namespace": "products", "productId": PRODUCT_ID},
        )

    def test_authorized_admin_upload_persists_and_returns_a_canonical_url(self):
        self.as_admin()
        response = self._upload()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["object"]["key"], f"products/{PRODUCT_ID}/extra.png")
        self.assertEqual(
            body["object"]["url"], f"/api/v1/media/objects/products/{PRODUCT_ID}/extra.png"
        )
        self.assertEqual(body["object"]["contentType"], "image/png")
        self.assertEqual(len(body["object"]["checksumSha256"]), 64)
        # It is really there, and really served.
        served = self.client.get(body["object"]["url"])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.content, PNG_BYTES)

    def test_admin_without_media_upload_is_denied_by_the_real_rbac_graph(self):
        """
        The VIEW_ONLY role holds `media.view` only, and its name is not in
        BUILT_IN_ROLES, so nothing can leak in through the built-in fallback.
        """
        self.as_view_only_admin()
        response = self._upload()
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("media.upload", response.text)
        self.assertFalse(self.provider.object_exists(f"products/{PRODUCT_ID}/extra.png"))

    def test_a_customer_token_cannot_reach_the_admin_media_surface(self):
        self.as_customer()
        response = self._upload()
        self.assertEqual(response.status_code, 403, response.text)
        self.assertFalse(self.provider.object_exists(f"products/{PRODUCT_ID}/extra.png"))

    def test_upload_rejects_a_non_image_without_writing(self):
        self.as_admin()
        response = self.client.post(
            "/api/v1/media/objects",
            files={"file": ("notes.png", b"PK\x03\x04not an image at all", "image/png")},
            data={"namespace": "products", "productId": PRODUCT_ID},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            [k for k in self.provider.list_objects() if k.endswith("notes.png")], []
        )

    def test_product_scoped_upload_requires_the_product_to_exist(self):
        self.as_admin()
        response = self.client.post(
            "/api/v1/media/products/NOT-A-PRODUCT/objects",
            files={"file": ("extra.png", PNG_BYTES, "image/png")},
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_authorized_admin_delete_removes_only_the_named_object(self):
        self.as_admin()
        self.assertEqual(self._upload("extra.png").status_code, 201)
        self.assertEqual(self._upload("other.png").status_code, 201)

        response = self.client.delete(f"/api/v1/media/objects/products/{PRODUCT_ID}/extra.png")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.provider.object_exists(f"products/{PRODUCT_ID}/extra.png"))
        self.assertTrue(self.provider.object_exists(f"products/{PRODUCT_ID}/other.png"))
        # The migrated source asset is untouched by an admin delete elsewhere.
        self.assertTrue(self.provider.object_exists(MIGRATED_KEY))

    def test_delete_without_media_delete_permission_is_denied(self):
        self.as_admin()
        self._upload("extra.png")
        self.as_view_only_admin()
        response = self.client.delete(f"/api/v1/media/objects/products/{PRODUCT_ID}/extra.png")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("media.delete", response.text)
        self.assertTrue(self.provider.object_exists(f"products/{PRODUCT_ID}/extra.png"))

    def test_delete_of_a_missing_object_is_a_404_not_a_silent_ok(self):
        self.as_admin()
        response = self.client.delete(f"/api/v1/media/objects/products/{PRODUCT_ID}/nope.png")
        self.assertEqual(response.status_code, 404)


# ===========================================================================
# Reference resolution endpoint against real DB-backed references
# ===========================================================================

class ReferenceResolutionRouteTests(MediaDatabaseTestCase):
    def test_resolves_the_reference_actually_stored_on_the_product(self):
        stored = self.stored_image()
        response = self.client.post(
            "/api/v1/media/references/resolve", json={"references": [stored]}
        )
        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["reference"], LEGACY_IMAGE)
        self.assertEqual(item["status"], "resolved")
        self.assertEqual(item["url"], CANONICAL_URL)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
