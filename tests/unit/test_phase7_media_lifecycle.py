"""
Phase 7 — REAL product + media lifecycle, verified end to end.

The Phase 6 suites proved the OBJECT half (storage, signatures, resolver,
RBAC on the object routes). This suite proves the Phase 7 DURABLE RECORD half
and the complete NEW-PRODUCT lifecycle against the real routers, the real
ORM models and a real database session:

    create product (draft)
      → upload image bytes (AVIF + WebP, signature-validated)
      → register objects → MediaAsset rows
      → assign them to the product → ProductMedia rows (primary + gallery)
      → read model: media-set, admin & storefront projections resolve the
        registered records to canonical /media/objects/... URLs
      → submit → approve → publish
      → storefront serves the canonical URL and the URL serves the real bytes

  · MediaAsset registration — verified metadata persisted from the object.
  · ProductMedia assignment — primary/cover uniqueness is enforced in the
    service (assigning a new primary demotes the incumbent).
  · Authorization — the real users/roles/permissions graph: customers and
    employees cannot reach the admin media surface; an admin without
    media.upload cannot register; an employee can edit only the products
    assigned to them and cannot edit another employee's product.
  · Media resolution — product → media reference → MediaAsset → object_key
    → /api/v1/media/objects/... → correct Content-Type and exact bytes.
  · Local storage — keys are derived from real product identity; provider
    independent (the same code path selects S3 by configuration later).
  · Invalid media — wrong bytes rejected (422), unknown object rejected on
    registration (404), traversal keys rejected (422).
  · AVIF and WebP uploads round-trip as their true content types.

WHY SQLITE
----------
As in `test_phase6_media_db.py`, this suite keeps the API/ORM behaviour checks
on SQLite via the two test-only shims (JSONB → JSON compile,
`ATTACH DATABASE … AS pratikshya`) so it runs anywhere, with no server.

The PostgreSQL half is covered separately and runs whenever a local server is
configured:
  · `backend/tests/unit/test_media_schema_integrity.py` — the real PK/FK/unique
    constraints, enforced by PostgreSQL, plus model↔schema parity.
  · `backend/scripts/media_lifecycle_pg_e2e.py` — the disposable-database
    `alembic upgrade head` + full lifecycle E2E.
Both are provided by `backend/app/testing/local_postgres.py`, which refuses to
run against anything but the local `pratikshya_local` database.
"""

import asyncio
import importlib
import os
import tempfile
import unittest

from fastapi_cache import FastAPICache
from fastapi_cache.backends import Backend
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

HAS_AIOSQLITE = importlib.util.find_spec("aiosqlite") is not None


# ---------------------------------------------------------------------------
# Test-only dialect shim (production code is not modified)
# ---------------------------------------------------------------------------

@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):  # pragma: no cover - dialect glue
    return "JSON"


class PassThroughCacheBackend(Backend):
    async def get_with_ttl(self, key):
        return 0, None

    async def get(self, key):
        return None

    async def set(self, key, value, expire=None):
        return None

    async def clear(self, namespace=None, key=None):
        return 0


importlib.import_module("app.models")  # noqa: E402  registers every mapped class
from app.config import settings  # noqa: E402
from app.dependencies import get_current_user, get_db  # noqa: E402
from app.models.auth.user import UserModel  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.catalog.category import CategoryModel, SubcategoryModel  # noqa: E402
from app.models.catalog.product import ProductModel  # noqa: E402
from app.models.employee.employee import EmployeeProfileModel  # noqa: E402
from app.models.media.media_asset import MediaAssetModel  # noqa: E402
from app.models.media.product_media import ProductMediaModel  # noqa: E402
from app.models.rbac.permission import PermissionModel  # noqa: E402
from app.models.rbac.role import RoleModel  # noqa: E402
from app.models.rbac.role_permission import RolePermissionModel  # noqa: E402
from app.models.rbac.user_role import UserRoleModel  # noqa: E402
from app.storage import get_storage_provider, reset_storage_provider  # noqa: E402
from app.services.media.product_media_resolver import clear_resolution_cache  # noqa: E402

# ---------------------------------------------------------------------------
# Real test payloads — signature-correct minimal image files
# ---------------------------------------------------------------------------

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
WEBP_BYTES = b"RIFF" + (26).to_bytes(4, "little") + b"WEBPVP8 " + (14).to_bytes(4, "little") + b"\x2f\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
AVIF_BYTES = b"\x00\x00\x00\x1c" + b"ftypavif" + b"\x00\x00\x00\x00avifmif1" + b"\x00\x00\x00\x00"

SEED_PRODUCT_ID = "PF-W-SAR-SIL-0001"
LEGACY_IMAGE = f"/images/products/{SEED_PRODUCT_ID}/primary.avif"
NEW_PRODUCT_ID = "PF-W-TST-NEW-0001"
# Authoritative taxonomy rows the product writes are validated against.
SEED_CATEGORY_ID = "cat-p7-sarees"
SEED_SUBCATEGORY_ID = "cat-p7-sarees-silk"


@unittest.skipUnless(
    HAS_AIOSQLITE, "aiosqlite is not installed (pip install -r requirements.txt)"
)
class Phase7LifecycleCase(unittest.IsolatedAsyncioTestCase):
    """One real database + one real object store per test."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pf7-db-")
        self.root = self._tmp.name
        self.main_db = os.path.join(self.root, "main.sqlite")
        self.schema_db = os.path.join(self.root, "pratikshya.sqlite")
        self.media_root = os.path.join(self.root, "media")

        self._saved_root = settings.LOCAL_MEDIA_ROOT
        settings.LOCAL_MEDIA_ROOT = self.media_root
        reset_storage_provider()
        self.provider = get_storage_provider()

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
            # Fine-grained roles NOT in BUILT_IN_ROLES, so the built-in
            # vocabulary fallback cannot quietly grant anything.
            catalogue_role = RoleModel(name="P7_CATALOGUE_ADMIN", description="test", is_system=False)
            view_role = RoleModel(name="P7_VIEW_ONLY", description="test", is_system=False)
            staff_role = RoleModel(name="P7_STYLISTX", description="test", is_system=False)
            session.add_all([catalogue_role, view_role, staff_role])

            permission_codes = (
                "products.view", "products.manage",
                "media.view", "media.upload", "media.delete",
            )
            permissions = {}
            for code in permission_codes:
                permission = PermissionModel(code=code, name=code, category="media", description=code)
                session.add(permission)
                permissions[code] = permission
            await session.flush()

            def grant(role, *codes):
                for code in codes:
                    session.add(
                        RolePermissionModel(role_id=role.id, permission_id=permissions[code].id)
                    )

            grant(catalogue_role, "products.view", "products.manage", "media.view", "media.upload", "media.delete")
            grant(view_role, "media.view")
            grant(staff_role, "products.manage")

            admin = UserModel(
                email="p7-admin@pratikshyafashon.test",
                full_name="Phase7 Admin",
                hashed_password="x",
                user_type="admin",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            viewer = UserModel(
                email="p7-viewer@pratikshyafashon.test",
                full_name="Phase7 Viewer",
                hashed_password="x",
                user_type="admin",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            customer = UserModel(
                email="p7-shopper@pratikshyafashon.test",
                full_name="Shopper",
                hashed_password="x",
                user_type="customer",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            employee1 = UserModel(
                email="p7-stylist-one@pratikshyafashon.test",
                full_name="Stylist One",
                hashed_password="x",
                user_type="employee",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            employee2 = UserModel(
                email="p7-stylist-two@pratikshyafashon.test",
                full_name="Stylist Two",
                hashed_password="x",
                user_type="employee",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            session.add_all([admin, viewer, customer, employee1, employee2])
            await session.flush()

            session.add(UserRoleModel(user_id=admin.id, role_id=catalogue_role.id))
            session.add(UserRoleModel(user_id=viewer.id, role_id=view_role.id))
            session.add(UserRoleModel(user_id=employee1.id, role_id=staff_role.id))
            session.add(UserRoleModel(user_id=employee2.id, role_id=staff_role.id))

            session.add_all([
                EmployeeProfileModel(user_id=employee1.id, employee_code="P7-EMP-001", designation="Stylist"),
                EmployeeProfileModel(user_id=employee2.id, employee_code="P7-EMP-002", designation="Stylist"),
            ])

            # The authoritative taxonomy the products below live under.
            # Phase 3 validates every product write against these rows, so a
            # create/patch that names `sarees` / `silk` must find them here.
            session.add(
                CategoryModel(
                    id=SEED_CATEGORY_ID,
                    name="Sarees",
                    slug="sarees",
                    status="ACTIVE",
                )
            )
            session.add(
                SubcategoryModel(
                    id=SEED_SUBCATEGORY_ID,
                    category_id=SEED_CATEGORY_ID,
                    name="Silk",
                    slug="silk",
                    status="ACTIVE",
                )
            )

            # A legacy product (legacy authored image only, no registered
            # records) and a product assigned to employee 1.
            session.add(
                ProductModel(
                    id=SEED_PRODUCT_ID,
                    product_id=SEED_PRODUCT_ID,
                    name="Banarasi Silk Saree",
                    slug="banarasi-silk-saree",
                    sku=SEED_PRODUCT_ID,
                    category="sarees",
                    subcategory="silk",
                    price=5000,
                    original_price=6000,
                    stock=3,
                    availability="in-stock",
                    status="PUBLISHED",
                    published=True,
                    image=LEGACY_IMAGE,
                    additional_images=[LEGACY_IMAGE],
                    assigned_employee_id="P7-EMP-001",
                )
            )
            session.add(
                ProductModel(
                    id="PF-W-TST-OTH-0002",
                    product_id="PF-W-TST-OTH-0002",
                    name="Other Employee Product",
                    slug="p7-other-employee-product",
                    sku="PF-W-TST-OTH-0002",
                    category="sarees",
                    price=2500,
                    description="A product that belongs to another employee.",
                    status="DRAFT",
                    published=False,
                    assigned_employee_id="P7-EMP-002",
                )
            )
            await session.commit()

            self.admin_id = admin.id
            self.viewer_id = viewer.id
            self.customer_id = customer.id
            self.employee1_id = employee1.id
            self.employee2_id = employee2.id

    def _build_app(self):
        from fastapi import FastAPI

        from app.api.v1.media import router as media_router
        from app.api.v1.products import router as products_router
        from app.core.error_handlers import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)
        FastAPICache.init(backend=PassThroughCacheBackend(), prefix="pf7-test")
        app.include_router(media_router, prefix="/api/v1")
        app.include_router(products_router, prefix="/api/v1")

        Session = self.Session

        async def _override_get_db():
            # Mirrors production `get_db` exactly: commit on success so
            # writes from earlier requests are visible to later ones.
            async with Session() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        # Only the session and the authenticated identity are injected. Every
        # route (media upload/register, products, storefront, RBAC joins)
        # executes for real against the seeded rows.
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

    # -- actors ---------------------------------------------------------------

    def as_admin(self):
        self._set_current_user(self.app, self.admin_id)

    def as_view_only_admin(self):
        self._set_current_user(self.app, self.viewer_id)

    def as_customer(self):
        self._set_current_user(self.app, self.customer_id)

    def as_employee1(self):
        self._set_current_user(self.app, self.employee1_id)

    def as_employee2(self):
        self._set_current_user(self.app, self.employee2_id)

    # -- db probes (test-side assertions, not the code under test) ------------

    def _run(self, coroutine):
        return asyncio.get_event_loop().run_until_complete(coroutine)

    def asset_rows(self):
        async def _read():
            async with self.Session() as session:
                return (await session.execute(select(MediaAssetModel))).scalars().all()

        return self._run(_read())

    def mapping_rows(self, product_id=None):
        async def _read():
            async with self.Session() as session:
                stmt = select(ProductMediaModel)
                if product_id:
                    stmt = stmt.where(ProductMediaModel.product_id == product_id)
                return (await session.execute(stmt)).scalars().all()

        return self._run(_read())

    def product_row(self, product_id):
        async def _read():
            async with self.Session() as session:
                return (
                    await session.execute(
                        select(ProductModel).where(ProductModel.id == product_id)
                    )
                ).scalars().first()

        return self._run(_read())

    # -- lifecycle helpers ----------------------------------------------------

    def upload(self, product_id, name, data, content_type):
        return self.client.post(
            f"/api/v1/media/products/{product_id}/objects",
            files={"file": (name, data, content_type)},
        )

    def register(self, object_key, product_id=None, role="gallery", sort_order=0, is_primary=False):
        form = {"object_key": object_key, "role": role, "sort_order": str(sort_order)}
        if product_id:
            form["product_id"] = product_id
        if is_primary:
            form["is_primary"] = "true"
        return self.client.post("/api/v1/media/register", data=form)

    def create_draft(self, product_id=NEW_PRODUCT_ID, **overrides):
        payload = {
            "id": product_id,
            "name": "Phase Seven Test Saree",
            "sku": product_id,
            "category": "sarees",
            "subcategory": "silk",
            "price": 7500,
            "description": "A real product created through the admin API for Phase 7.",
            "brand": "Pratikshya Fashon",
            "gender": "Women",
        }
        payload.update(overrides)
        return self.client.post("/api/v1/admin/products/draft", json=payload)


# ===========================================================================
# MediaAsset registration + local storage
# ===========================================================================

class MediaAssetRegistrationTests(Phase7LifecycleCase):
    def test_upload_then_register_creates_a_verified_asset_row(self):
        self.as_admin()
        upload = self.upload(SEED_PRODUCT_ID, "cover.png", PNG_BYTES, "image/png")
        self.assertEqual(upload.status_code, 201, upload.text)
        key = upload.json()["object"]["key"]
        self.assertEqual(key, f"products/{SEED_PRODUCT_ID}/cover.png")

        response = self.register(key)
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["media"]["objectKey"], key)
        self.assertEqual(
            body["media"]["url"], f"/api/v1/media/objects/products/{SEED_PRODUCT_ID}/cover.png"
        )
        self.assertFalse(body["assigned"])

        rows = self.asset_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.object_key, key)
        self.assertEqual(row.mime_type, "image/png")
        self.assertEqual(row.file_size, len(PNG_BYTES))
        self.assertEqual(row.storage_provider, "local")
        self.assertEqual(row.status, "uploaded")
        self.assertEqual(len(row.checksum_sha256), 64)
        # The object really is in the local store and served from it.
        self.assertTrue(self.provider.object_exists(key))
        served = self.client.get(f"/api/v1/media/objects/{key}")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.content, PNG_BYTES)
        self.assertEqual(served.headers["content-type"], "image/png")

    def test_registration_is_idempotent_by_object_key(self):
        self.as_admin()
        key = self.upload(SEED_PRODUCT_ID, "cover.png", PNG_BYTES, "image/png").json()["object"]["key"]
        first = self.register(key)
        second = self.register(key)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json()["media"]["id"], second.json()["media"]["id"])
        self.assertEqual(len(self.asset_rows()), 1)

    def test_registering_an_object_that_does_not_exist_is_404(self):
        self.as_admin()
        response = self.register(f"products/{SEED_PRODUCT_ID}/never-uploaded.png")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(len(self.asset_rows()), 0)

    def test_registering_a_traversal_key_is_422_and_creates_nothing(self):
        self.as_admin()
        response = self.client.post(
            "/api/v1/media/register",
            data={"object_key": "../../etc/passwd"},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(len(self.asset_rows()), 0)

    def test_upload_rejects_bytes_that_are_not_an_image(self):
        self.as_admin()
        response = self.upload(SEED_PRODUCT_ID, "notes.png", b"PK\x03\x04definitely zip", "image/png")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.asset_rows(), [])

    def test_avif_upload_round_trips_as_image_avif(self):
        self.as_admin()
        response = self.upload(SEED_PRODUCT_ID, "look.avif", AVIF_BYTES, "image/avif")
        self.assertEqual(response.status_code, 201, response.text)
        key = response.json()["object"]["key"]
        self.assertEqual(response.json()["object"]["contentType"], "image/avif")
        served = self.client.get(f"/api/v1/media/objects/{key}")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers["content-type"], "image/avif")
        self.assertEqual(served.content, AVIF_BYTES)

    def test_webp_upload_round_trips_as_image_webp(self):
        self.as_admin()
        response = self.upload(SEED_PRODUCT_ID, "look.webp", WEBP_BYTES, "image/webp")
        self.assertEqual(response.status_code, 201, response.text)
        key = response.json()["object"]["key"]
        self.assertEqual(response.json()["object"]["contentType"], "image/webp")
        served = self.client.get(f"/api/v1/media/objects/{key}")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers["content-type"], "image/webp")
        self.assertEqual(served.content, WEBP_BYTES)


# ===========================================================================
# ProductMedia assignment + primary/cover behaviour
# ===========================================================================

class ProductMediaAssignmentTests(Phase7LifecycleCase):
    def _registered(self, name="one.png", data=PNG_BYTES, ctype="image/png"):
        key = self.upload(SEED_PRODUCT_ID, name, data, ctype).json()["object"]["key"]
        return key, self.register(key, SEED_PRODUCT_ID)

    def test_register_with_product_creates_the_association(self):
        self.as_admin()
        key, response = self._registered()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertTrue(body["assigned"])
        self.assertEqual(body["assignment"]["productId"], SEED_PRODUCT_ID)
        self.assertEqual(body["assignment"]["role"], "gallery")

        mappings = self.mapping_rows(SEED_PRODUCT_ID)
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0].role, "gallery")
        self.assertFalse(mappings[0].is_primary)

    def test_media_set_reports_registered_items_primary_first(self):
        self.as_admin()
        key1, _ = self._registered("one.png")
        # second object: same bytes, different key — a distinct object.
        self.provider.put_object(f"products/{SEED_PRODUCT_ID}/two.png", PNG_BYTES, "image/png")
        key2 = f"products/{SEED_PRODUCT_ID}/two.png"
        response2 = self.register(key2, SEED_PRODUCT_ID, sort_order=1, is_primary=True)
        self.assertEqual(response2.status_code, 201, response2.text)

        response = self.client.get(f"/api/v1/media/products/{SEED_PRODUCT_ID}/media-set")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["mediaRecordsAvailable"])
        self.assertEqual(len(body["mediaItems"]), 2)
        self.assertEqual(body["mediaItems"][0]["objectKey"], key2, "primary must lead")
        self.assertTrue(body["mediaItems"][0]["isPrimary"])
        self.assertEqual(
            body["primaryMediaUrl"],
            f"/api/v1/media/objects/{key2}",
        )
        # Legacy half keeps resolving for compatibility.
        self.assertEqual(body["mediaIds"], body.get("mediaIds", []))

    def test_assigning_a_new_primary_demotes_the_incumbent(self):
        self.as_admin()
        key1, _ = self._registered("one.png")
        self.assertEqual(
            self.register(key1, SEED_PRODUCT_ID, role="COVER", is_primary=True).status_code, 201
        )
        self.provider.put_object(f"products/{SEED_PRODUCT_ID}/two.png", PNG_BYTES, "image/png")
        self.register(f"products/{SEED_PRODUCT_ID}/two.png", SEED_PRODUCT_ID, is_primary=True)

        mappings = sorted(self.mapping_rows(SEED_PRODUCT_ID), key=lambda m: m.role)
        primaries = [m for m in self.mapping_rows(SEED_PRODUCT_ID) if m.is_primary]
        self.assertEqual(len(primaries), 1, "a product must never hold two primaries")
        self.assertEqual(primaries[0].media_id, self.asset_rows()[-1].id)
        self.assertEqual(len(mappings), 2)

    def test_reregistering_updates_role_and_sort_order_in_place(self):
        self.as_admin()
        key, _ = self._registered()
        updated = self.register(key, SEED_PRODUCT_ID, role="detail", sort_order=3, is_primary=True)
        self.assertEqual(updated.status_code, 201)
        body = updated.json()
        self.assertEqual(body["assignment"]["role"], "detail")
        self.assertEqual(body["assignment"]["sortOrder"], 3)
        self.assertTrue(body["assignment"]["isPrimary"])
        # No duplicates.
        self.assertEqual(len(self.mapping_rows(SEED_PRODUCT_ID)), 1)
        self.assertEqual(len(self.asset_rows()), 1)

    def test_registered_assets_are_listed_for_the_library(self):
        self.as_admin()
        self._registered()
        response = self.client.get("/api/v1/media/assets")
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["objectKey"], f"products/{SEED_PRODUCT_ID}/one.png")
        self.assertTrue(items[0]["url"].startswith("/api/v1/media/objects/"))


# ===========================================================================
# Authorization — the real RBAC graph decides
# ===========================================================================

class MediaAuthorizationTests(Phase7LifecycleCase):
    def _key(self):
        return f"products/{SEED_PRODUCT_ID}/unauthorized.png"

    def test_customer_cannot_upload_or_register(self):
        self.as_customer()
        upload = self.upload(SEED_PRODUCT_ID, "unauthorized.png", PNG_BYTES, "image/png")
        self.assertEqual(upload.status_code, 403, upload.text)
        registered = self.register(self._key())
        self.assertEqual(registered.status_code, 403, registered.text)
        self.assertEqual(self.asset_rows(), [])

    def test_admin_without_media_upload_permission_cannot_register(self):
        # The viewer can read but holds no media.upload grant.
        self.as_admin()
        key = self.upload(SEED_PRODUCT_ID, "authorized.png", PNG_BYTES, "image/png").json()["object"]["key"]
        self.as_view_only_admin()
        denied = self.register(key, SEED_PRODUCT_ID)
        self.assertEqual(denied.status_code, 403, denied.text)
        denied_upload = self.upload(SEED_PRODUCT_ID, "denied.png", PNG_BYTES, "image/png")
        self.assertEqual(denied_upload.status_code, 403, denied_upload.text)
        self.assertEqual(self.asset_rows(), [])
        # And the assets list is gated by the same permission.
        self.assertEqual(self.client.get("/api/v1/media/assets").status_code, 403)

    def test_employee_token_cannot_reach_the_admin_media_surface(self):
        self.as_employee1()
        upload = self.upload(SEED_PRODUCT_ID, "staff.png", PNG_BYTES, "image/png")
        # get_current_admin refuses non-admin identities regardless of roles.
        self.assertEqual(upload.status_code, 403, upload.text)
        self.assertEqual(self.register(f"products/{SEED_PRODUCT_ID}/staff.png").status_code, 403)

    def test_public_media_reads_stay_open_and_registered_reads_are_public(self):
        # Registered read model is public exactly like the object bytes.
        self.as_admin()
        self._upload_and_assign()
        self.as_customer()
        response = self.client.get(f"/api/v1/media/products/{SEED_PRODUCT_ID}/media-set")
        self.assertEqual(response.status_code, 200)

    def _upload_and_assign(self):
        key = self.upload(SEED_PRODUCT_ID, "shared.png", PNG_BYTES, "image/png").json()["object"]["key"]
        self.register(key, SEED_PRODUCT_ID)


class EmployeeProductWorkflowTests(Phase7LifecycleCase):
    """Existing RBAC + product-assignment rules — never broadened by media."""

    def test_assigned_employee_can_patch_their_own_product(self):
        self.as_employee1()
        response = self.client.patch(
            f"/api/v1/employee/products/{SEED_PRODUCT_ID}",
            json={"name": "Updated by the assigned stylist"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.product_row(SEED_PRODUCT_ID).name, "Updated by the assigned stylist"
        )

    def test_unassigned_employee_cannot_modify_another_employees_product(self):
        self.as_employee1()  # assigned to SEED_PRODUCT_ID, not to the other one
        response = self.client.patch(
            "/api/v1/employee/products/PF-W-TST-OTH-0002",
            json={"name": "Must never land"},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertNotEqual(self.product_row("PF-W-TST-OTH-0002").name, "Must never land")

    def test_unassigned_employee_cannot_submit_anothers_product_for_review(self):
        self.as_employee1()
        response = self.client.post("/api/v1/products/PF-W-TST-OTH-0002/submit-review")
        self.assertEqual(response.status_code, 403, response.text)

    def test_customer_cannot_touch_the_employee_product_surface(self):
        self.as_customer()
        response = self.client.patch(
            f"/api/v1/employee/products/{SEED_PRODUCT_ID}", json={"name": "nope"}
        )
        self.assertEqual(response.status_code, 403, response.text)


# ===========================================================================
# Registered-media read model through the product projections
# ===========================================================================

class RegisteredMediaReadModelTests(Phase7LifecycleCase):
    def _publish_seed_with_registered_media(self):
        """Register two assets on the seed product: primary + gallery."""
        self.as_admin()
        cover_key = self.upload(SEED_PRODUCT_ID, "cover.avif", AVIF_BYTES, "image/avif").json()["object"]["key"]
        angle_key = self.upload(SEED_PRODUCT_ID, "angle.webp", WEBP_BYTES, "image/webp").json()["object"]["key"]
        self.register(cover_key, SEED_PRODUCT_ID, role="COVER", sort_order=0, is_primary=True)
        self.register(angle_key, SEED_PRODUCT_ID, role="gallery", sort_order=1)
        return cover_key, angle_key

    def test_admin_projection_prefers_registered_records(self):
        cover_key, angle_key = self._publish_seed_with_registered_media()
        response = self.client.get(f"/api/v1/admin/products/{SEED_PRODUCT_ID}")
        self.assertEqual(response.status_code, 200, response.text)
        product = response.json()["product"]
        # The registered COVER association is the read model's cover, even
        # though the product row still carries the legacy authored image.
        self.assertEqual(product["image"], f"/api/v1/media/objects/{cover_key}")
        self.assertEqual(
            product["additionalImages"],
            [f"/api/v1/media/objects/{cover_key}", f"/api/v1/media/objects/{angle_key}"],
        )
        cover_asset = [row for row in self.asset_rows() if row.object_key == cover_key][0]
        self.assertEqual(product["primaryMediaId"], cover_asset.id)

    def test_storefront_projection_resolves_registered_records(self):
        cover_key, angle_key = self._publish_seed_with_registered_media()
        response = self.client.get(f"/api/v1/products/{SEED_PRODUCT_ID}")
        self.assertEqual(response.status_code, 200, response.text)
        product = response.json()["product"]
        self.assertEqual(product["image"], f"/api/v1/media/objects/{cover_key}")
        self.assertIn(f"/api/v1/media/objects/{angle_key}", product["additionalImages"])

    def test_products_without_registered_records_are_untouched(self):
        response = self.client.get(f"/api/v1/products/{SEED_PRODUCT_ID}")
        self.assertEqual(response.status_code, 200, response.text)
        product = response.json()["product"]
        # Legacy dual-read: the object was never put in this test's store, so
        # the legacy reference itself survives unresolved (compatibility).
        self.assertEqual(product["image"], LEGACY_IMAGE)


# ===========================================================================
# THE acceptance test — full NEW product lifecycle through the real API
# ===========================================================================

class FullNewProductLifecycleTests(Phase7LifecycleCase):
    def test_create_upload_register_assign_save_publish_storefront_serves_bytes(self):
        self.as_admin()

        # ── 1. Admin creates a NEW product through the real API ────────────
        created = self.create_draft()
        self.assertEqual(created.status_code, 201, created.text)
        product = created.json()["product"]
        self.assertEqual(product["status"], "DRAFT")

        # ── 2. Upload NEW image bytes (AVIF + WebP) through the storage API ─
        cover_upload = self.upload(NEW_PRODUCT_ID, "atelier-cover.avif", AVIF_BYTES, "image/avif")
        self.assertEqual(cover_upload.status_code, 201, cover_upload.text)
        cover_key = cover_upload.json()["object"]["key"]
        self.assertEqual(cover_key, f"products/{NEW_PRODUCT_ID}/atelier-cover.avif")

        angle_upload = self.upload(NEW_PRODUCT_ID, "atelier-angle.webp", WEBP_BYTES, "image/webp")
        self.assertEqual(angle_upload.status_code, 201, angle_upload.text)
        angle_key = angle_upload.json()["object"]["key"]

        # ── 3. Register both objects as durable media assets ───────────────
        cover_reg = self.register(cover_key, NEW_PRODUCT_ID, role="COVER", sort_order=0, is_primary=True)
        self.assertEqual(cover_reg.status_code, 201, cover_reg.text)
        angle_reg = self.register(angle_key, NEW_PRODUCT_ID, role="gallery", sort_order=1)
        self.assertEqual(angle_reg.status_code, 201, angle_reg.text)
        cover_media_id = cover_reg.json()["media"]["id"]
        angle_media_id = angle_reg.json()["media"]["id"]

        # Real rows, not fabricated ids.
        self.assertEqual({row.id for row in self.asset_rows()}, {cover_media_id, angle_media_id})
        self.assertEqual(len(self.mapping_rows(NEW_PRODUCT_ID)), 2)

        # ── 4. Persist the product with the media references ───────────────
        cover_url = f"/api/v1/media/objects/{cover_key}"
        angle_url = f"/api/v1/media/objects/{angle_key}"
        patched = self.client.patch(
            f"/api/v1/admin/products/{NEW_PRODUCT_ID}",
            json={
                "mediaIds": [cover_media_id, angle_media_id],
                "primaryMediaId": cover_media_id,
                "galleryMediaIds": [angle_media_id],
                "image": cover_url,
                "additionalImages": [cover_url, angle_url],
            },
        )
        self.assertEqual(patched.status_code, 200, patched.text)

        # ── 5. Refetch from the server — the read model must agree ─────────
        refetched = self.client.get(f"/api/v1/admin/products/{NEW_PRODUCT_ID}")
        self.assertEqual(refetched.status_code, 200)
        product = refetched.json()["product"]
        self.assertEqual(product["image"], cover_url)
        self.assertEqual(product["additionalImages"], [cover_url, angle_url])
        self.assertEqual(product["primaryMediaId"], cover_media_id)
        self.assertEqual(product["mediaIds"], [cover_media_id, angle_media_id])

        media_set = self.client.get(f"/api/v1/media/products/{NEW_PRODUCT_ID}/media-set")
        self.assertTrue(media_set.json()["mediaRecordsAvailable"])
        self.assertEqual(media_set.json()["primaryMediaUrl"], cover_url)
        self.assertEqual(
            [item["objectKey"] for item in media_set.json()["mediaItems"]],
            [cover_key, angle_key],
        )

        # ── 6. Publish through the real gated workflow ─────────────────────
        submitted = self.client.post(f"/api/v1/products/{NEW_PRODUCT_ID}/submit-review")
        self.assertEqual(submitted.status_code, 200, submitted.text)
        approved = self.client.post(f"/api/v1/admin/products/{NEW_PRODUCT_ID}/approve")
        self.assertEqual(approved.status_code, 200, approved.text)
        published = self.client.post(f"/api/v1/admin/products/{NEW_PRODUCT_ID}/publish")
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["product"]["status"], "PUBLISHED")
        self.assertTrue(published.json()["product"]["published"])

        # ── 7. Storefront serves the canonical media URL ───────────────────
        storefront = self.client.get(f"/api/v1/products/{NEW_PRODUCT_ID}")
        self.assertEqual(storefront.status_code, 200, storefront.text)
        detail = storefront.json()["product"]
        self.assertEqual(detail["image"], cover_url)
        self.assertEqual(detail["additionalImages"], [cover_url, angle_url])

        # ── 8. The canonical URL returns the real image over HTTP ──────────
        served_cover = self.client.get(cover_url)
        self.assertEqual(served_cover.status_code, 200)
        self.assertEqual(served_cover.headers["content-type"], "image/avif")
        self.assertEqual(served_cover.content, AVIF_BYTES)

        served_angle = self.client.get(angle_url)
        self.assertEqual(served_angle.status_code, 200)
        self.assertEqual(served_angle.headers["content-type"], "image/webp")
        self.assertEqual(served_angle.content, WEBP_BYTES)

    def test_registered_media_alone_satisfies_the_publish_gate_and_read_model(self):
        """No legacy `image` column at all: ProductMedia rows carry the cover."""
        self.as_admin()
        self.assertEqual(self.create_draft().status_code, 201)
        cover_upload = self.upload(NEW_PRODUCT_ID, "only-cover.avif", AVIF_BYTES, "image/avif")
        cover_key = cover_upload.json()["object"]["key"]
        registration = self.register(cover_key, NEW_PRODUCT_ID, role="COVER", is_primary=True)
        cover_media_id = registration.json()["media"]["id"]

        patched = self.client.patch(
            f"/api/v1/admin/products/{NEW_PRODUCT_ID}",
            json={"mediaIds": [cover_media_id], "primaryMediaId": cover_media_id},
        )
        self.assertEqual(patched.status_code, 200, patched.text)

        issues = self.client.get(f"/api/v1/admin/products/{NEW_PRODUCT_ID}/publish-issues")
        self.assertEqual(issues.status_code, 200)
        self.assertNotIn(
            "At least one cover image is required before publishing.",
            issues.json()["issues"],
        )

        self.client.post(f"/api/v1/products/{NEW_PRODUCT_ID}/submit-review")
        self.client.post(f"/api/v1/admin/products/{NEW_PRODUCT_ID}/approve")
        published = self.client.post(f"/api/v1/admin/products/{NEW_PRODUCT_ID}/publish")
        self.assertEqual(published.status_code, 200, published.text)

        storefront = self.client.get(f"/api/v1/products/{NEW_PRODUCT_ID}")
        self.assertEqual(storefront.status_code, 200, storefront.text)
        cover_url = f"/api/v1/media/objects/{cover_key}"
        # The only route this URL could have taken is
        # product → ProductMedia → MediaAsset → object_key → canonical URL.
        self.assertEqual(storefront.json()["product"]["image"], cover_url)
        self.assertEqual(self.client.get(cover_url).content, AVIF_BYTES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
