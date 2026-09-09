"""Phase 3 Step 11C — one real product/media/taxonomy journey.

This is deliberately one continuous test rather than a collection of isolated
service checks.  It uses the production ``app.main:app`` object with only the
normal database and authenticated-user dependencies overridden to point at a
throwaway SQLite database.  Every business operation remains an HTTP request
to the production router graph.

PostgreSQL is exercised by the separate environment-gated schema tests when a
local DATABASE_URL is available.  This journey uses the repository's existing
SQLite test harness when it is not, and never touches production data.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
import uuid
from typing import Any, Dict, Tuple

from fastapi.testclient import TestClient
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.config import settings
from app.dependencies import get_current_user, get_db
from app.main import app as production_app
from app.models.auth.user import UserModel
from app.models.base import Base
from app.models.catalog.product import ProductModel
from app.models.media.product_media import ProductMediaModel
from app.models.rbac.permission import PermissionModel
from app.models.rbac.role import RoleModel
from app.models.rbac.role_permission import RolePermissionModel
from app.models.rbac.user_role import UserRoleModel
from app.storage import reset_storage_provider
from app.core.redis import close_redis
from app.services.media.product_media_resolver import clear_resolution_cache


# The same signature-valid fixture used by the established Phase 7 media
# harness.  It is intentionally in the test, rather than depending on the
# unavailable cotton-fixture.avif dataset.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw):  # pragma: no cover - dialect glue
    return "JSON"


class Step11CProductMediaTaxonomyFlow(unittest.IsolatedAsyncioTestCase):
    """The complete Step 11C journey against real application wiring."""

    async def asyncSetUp(self) -> None:
        if importlib.util.find_spec("aiosqlite") is None:
            self.skipTest("aiosqlite is not installed")

        importlib.import_module("app.models")

        self._tmp = tempfile.TemporaryDirectory(prefix="pf3-step11c-")
        self.root = self._tmp.name
        self.media_root = os.path.join(self.root, "media")
        self.main_db = os.path.join(self.root, "main.sqlite")
        self.schema_db = os.path.join(self.root, "pratikshya.sqlite")

        self._saved_media_root = settings.LOCAL_MEDIA_ROOT
        self._saved_storage_provider = settings.STORAGE_PROVIDER
        settings.LOCAL_MEDIA_ROOT = self.media_root
        settings.STORAGE_PROVIDER = "local"
        reset_storage_provider()
        clear_resolution_cache()
        await close_redis()

        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.main_db}")
        schema_db = self.schema_db

        @event.listens_for(self.engine.sync_engine, "connect")
        def _attach(dbapi_conn, _record):  # pragma: no cover - driver hook
            cursor = dbapi_conn.cursor()
            cursor.execute(f"ATTACH DATABASE '{schema_db}' AS pratikshya")
            cursor.close()

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        await self._seed_admin()

        # Reuse the production FastAPI object and its complete router graph.
        # Only disposable DB/auth dependencies are injected, as in the
        # repository's existing real-router harnesses.
        self._saved_overrides = production_app.dependency_overrides.copy()
        production_app.dependency_overrides[get_db] = self._override_get_db
        production_app.dependency_overrides[get_current_user] = self._override_current_user
        FastAPICache.init(
            backend=InMemoryBackend(),
            prefix="pf3-step11c",
        )
        self.client = TestClient(production_app)

    async def asyncTearDown(self) -> None:
        clear_resolution_cache()
        await close_redis()
        reset_storage_provider()
        settings.LOCAL_MEDIA_ROOT = self._saved_media_root
        settings.STORAGE_PROVIDER = self._saved_storage_provider
        FastAPICache.reset()

        production_app.dependency_overrides.clear()
        production_app.dependency_overrides.update(self._saved_overrides)

        await self.engine.dispose()
        self._tmp.cleanup()

    async def _seed_admin(self) -> None:
        async with self.Session() as session:
            role = RoleModel(
                name="STEP11C_ADMIN",
                description="Step 11C integration administrator",
                is_system=False,
            )
            permission_codes = (
                "categories.create",
                "categories.edit",
                "categories.archive",
                "products.view",
                "products.manage",
                "media.upload",
            )
            permissions = {
                code: PermissionModel(
                    code=code,
                    name=code,
                    category="step11c",
                    description=code,
                )
                for code in permission_codes
            }
            session.add(role)
            session.add_all(permissions.values())

            admin = UserModel(
                email="step11c-admin@pratikshyafashon.test",
                full_name="Step 11C Admin",
                hashed_password="x",
                user_type="admin",
                status="ACTIVE",
                is_verified=True,
                force_password_change=False,
            )
            session.add(admin)
            await session.flush()

            session.add_all(
                [
                    RolePermissionModel(role_id=role.id, permission_id=permission.id)
                    for permission in permissions.values()
                ]
            )
            session.add(UserRoleModel(user_id=admin.id, role_id=role.id))
            await session.commit()
            self.admin_id = admin.id

    async def _override_get_db(self):
        async with self.Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _override_current_user(self):
        async with self.Session() as session:
            return (
                await session.execute(
                    select(UserModel).where(UserModel.id == self.admin_id)
                )
            ).scalars().one()

    def _run(self, coroutine):
        """Run a test-side DB probe outside the TestClient's event loop."""
        return asyncio.get_event_loop().run_until_complete(coroutine)

    def _assert_status(self, response, expected: int) -> Dict[str, Any]:
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def _product_media_columns(self, product_id: str) -> Tuple[Any, ...]:
        async def _read():
            async with self.Session() as session:
                row = (
                    await session.execute(
                        select(ProductModel).where(ProductModel.id == product_id)
                    )
                ).scalars().one()
                return (
                    row.image,
                    row.hover_image,
                    tuple(row.additional_images or []),
                    tuple(row.media_ids or []),
                    row.primary_media_id,
                    tuple(row.gallery_media_ids or []),
                )

        return self._run(_read())

    def _product_media_association(self, product_id: str):
        async def _read():
            async with self.Session() as session:
                return (
                    await session.execute(
                        select(ProductMediaModel).where(
                            ProductMediaModel.product_id == product_id
                        )
                    )
                ).scalars().all()

        return self._run(_read())

    def test_complete_product_media_taxonomy_flow_through_production_app(self):
        """Create → register primary media → publish → archive/restore taxonomy."""

        # 1. Create and activate taxonomy through authoritative admin routes.
        category_created = self._assert_status(
            self.client.post(
                "/api/v1/admin/categories",
                json={
                    "name": "Step 11C Sarees",
                    "slug": "sarees",
                    "description": "Taxonomy created by the Step 11C journey",
                },
            ),
            201,
        )
        category = category_created["category"]
        category_id = category["id"]
        self.assertNotEqual(category_id, "sarees")
        uuid.UUID(category_id)  # server-generated durable ID
        self.assertEqual(category["slug"], "sarees")
        self.assertEqual(category["status"], "DRAFT")

        category = self._assert_status(
            self.client.post(f"/api/v1/admin/categories/{category_id}/activate"),
            200,
        )["category"]
        self.assertEqual(category["id"], category_id)
        self.assertEqual(category["status"], "ACTIVE")

        subcategory_created = self._assert_status(
            self.client.post(
                f"/api/v1/admin/categories/{category_id}/subcategories",
                json={
                    "name": "Step 11C Silk",
                    "slug": "silk",
                    "description": "Subcategory created by the Step 11C journey",
                },
            ),
            201,
        )
        subcategory = subcategory_created["subcategory"]
        subcategory_id = subcategory["id"]
        self.assertEqual(subcategory["categoryId"], category_id)
        self.assertEqual(subcategory_id, "sarees-silk")
        self.assertEqual(subcategory["status"], "DRAFT")

        subcategory = self._assert_status(
            self.client.post(f"/api/v1/admin/subcategories/{subcategory_id}/activate"),
            200,
        )["subcategory"]
        self.assertEqual(subcategory["id"], subcategory_id)
        self.assertEqual(subcategory["categoryId"], category_id)
        self.assertEqual(subcategory["status"], "ACTIVE")

        # 2. Ask the server for the canonical ID, using the category slug that
        # the allocator maps to SAR.  The created category's returned ID is
        # used for product taxonomy; no client taxonomy snapshot is involved.
        next_id_response = self._assert_status(
            self.client.get("/api/v1/admin/products/next-id?category=sarees"),
            200,
        )
        product_id = next_id_response["nextId"]
        self.assertRegex(product_id, r"^PF-SAR-\d{4}$")

        # 3. Draft write: the product request owns content/taxonomy only.  In
        # particular, no lifecycle key or legacy media association key is
        # sent, and the server canonicalises the taxonomy to durable IDs.
        draft_payload = {
            "id": product_id,
            "name": "Step 11C Handwoven Silk Saree",
            "sku": f"SKU-{product_id}",
            "category": category_id,
            "subcategory": subcategory_id,
            "price": 7500,
            "description": "A real product created through the Step 11C admin route.",
            "gender": "Women",
            "stock": 3,
            "availability": "in-stock",
        }
        forbidden_write_keys = {
            "status",
            "published",
            "review",
            "reviewFlags",
            "history",
            "mediaIds",
            "primaryMediaId",
            "galleryMediaIds",
        }
        self.assertFalse(forbidden_write_keys.intersection(draft_payload))

        draft_created = self._assert_status(
            self.client.post("/api/v1/admin/products/draft", json=draft_payload),
            201,
        )
        draft_product = draft_created["product"]
        self.assertEqual(draft_product["id"], product_id)
        self.assertEqual(draft_product["productId"], product_id)
        self.assertEqual(draft_product["category"], category_id)
        self.assertEqual(draft_product["subcategory"], subcategory_id)
        self.assertEqual(draft_product["status"], "DRAFT")
        self.assertFalse(draft_product["published"])
        legacy_media_before = self._product_media_columns(product_id)
        self.assertEqual(
            legacy_media_before,
            ("", "", (), (), None, ()),
        )

        # 4. An immediate authoritative admin read uses the camelCase DTO.
        admin_read = self._assert_status(
            self.client.get(f"/api/v1/admin/products/{product_id}"),
            200,
        )["product"]
        self.assertEqual(admin_read["id"], product_id)
        self.assertEqual(admin_read["productId"], product_id)
        self.assertNotIn("product_id", admin_read)
        self.assertEqual(admin_read["category"], category_id)
        self.assertEqual(admin_read["subcategory"], subcategory_id)
        self.assertEqual(admin_read["status"], "DRAFT")
        self.assertFalse(admin_read["published"])

        # 5. Upload real signature-valid bytes through the product-scoped
        # storage route.  This creates no fabricated media metadata.
        uploaded = self._assert_status(
            self.client.post(
                f"/api/v1/media/products/{product_id}/objects",
                files={"file": ("step11c-cover.png", PNG_BYTES, "image/png")},
            ),
            201,
        )
        object_key = uploaded["object"]["key"]
        object_url = uploaded["object"]["url"]
        self.assertEqual(object_key, f"products/{product_id}/step11c-cover.png")
        self.assertEqual(object_url, f"/api/v1/media/objects/{object_key}")
        self.assertEqual(uploaded["object"]["contentType"], "image/png")

        # 6. Registration is the authoritative association path.  There is
        # deliberately no product PATCH in this journey.
        registered = self._assert_status(
            self.client.post(
                "/api/v1/media/register",
                data={
                    "object_key": object_key,
                    "product_id": product_id,
                    "role": "COVER",
                    "sort_order": "0",
                    "is_primary": "true",
                },
            ),
            201,
        )
        self.assertEqual(
            set(registered), {"ok", "media", "assigned", "assignment"}
        )
        self.assertTrue(registered["ok"])
        self.assertTrue(registered["assigned"])
        media = registered["media"]
        assignment = registered["assignment"]
        media_id = media["id"]
        self.assertEqual(media["objectKey"], object_key)
        self.assertEqual(media["url"], object_url)
        self.assertEqual(media["mimeType"], "image/png")
        self.assertEqual(assignment["productId"], product_id)
        self.assertEqual(assignment["mediaId"], media_id)
        self.assertEqual(assignment["role"], "COVER")
        self.assertTrue(assignment["isPrimary"])
        self.assertEqual(
            self._product_media_columns(product_id),
            legacy_media_before,
            "registration must not mutate legacy product media columns",
        )
        associations = self._product_media_association(product_id)
        self.assertEqual(len(associations), 1)
        self.assertEqual(associations[0].media_id, media_id)
        self.assertTrue(associations[0].is_primary)

        # 7. The durable product media-set is the authoritative read model.
        media_set = self._assert_status(
            self.client.get(f"/api/v1/media/products/{product_id}/media-set"),
            200,
        )
        self.assertTrue(media_set["mediaRecordsAvailable"])
        self.assertEqual(media_set["productId"], product_id)
        self.assertEqual(media_set["primaryMediaUrl"], object_url)
        self.assertEqual(len(media_set["mediaItems"]), 1)
        media_item = media_set["mediaItems"][0]
        self.assertEqual(media_item["mediaId"], media_id)
        self.assertEqual(media_item["objectKey"], object_key)
        self.assertEqual(media_item["url"], object_url)
        self.assertTrue(media_item["isPrimary"])

        # The URL returned by the registered read model serves the exact bytes.
        served = self.client.get(object_url)
        self.assertEqual(served.status_code, 200, served.text)
        self.assertEqual(served.content, PNG_BYTES)
        self.assertEqual(served.headers["content-type"], "image/png")

        # 8. Registered primary media removes the canonical cover blocker.
        issues = self._assert_status(
            self.client.get(f"/api/v1/admin/products/{product_id}/publish-issues"),
            200,
        )
        self.assertNotIn(
            "At least one cover image is required before publishing.",
            issues["issues"],
        )

        # The lifecycle route is intentionally explicit: publish itself still
        # requires the real submit → approve state transition.
        submitted = self._assert_status(
            self.client.post(f"/api/v1/products/{product_id}/submit-review"),
            200,
        )["product"]
        self.assertEqual(submitted["status"], "PENDING_REVIEW")
        approved = self._assert_status(
            self.client.post(f"/api/v1/admin/products/{product_id}/approve"),
            200,
        )["product"]
        self.assertEqual(approved["review"]["state"], "APPROVED")

        # 9. The real gated publish route retains the registered association.
        published = self._assert_status(
            self.client.post(f"/api/v1/admin/products/{product_id}/publish"),
            200,
        )["product"]
        self.assertEqual(published["id"], product_id)
        self.assertEqual(published["status"], "PUBLISHED")
        self.assertTrue(published["published"])
        self.assertEqual(published["mediaIds"], [media_id])
        self.assertEqual(published["primaryMediaId"], media_id)

        # 10. Warm both public caches before the taxonomy transition.  Public
        # reads are unauthenticated and expose the registered representation,
        # but not admin workflow/audit fields.
        storefront_list = self._assert_status(
            self.client.get("/api/v1/products"),
            200,
        )
        list_item = next(
            (item for item in storefront_list["items"] if item["id"] == product_id),
            None,
        )
        self.assertIsNotNone(list_item, "published product must be visible in the storefront list")
        self.assertEqual(list_item["category"], category_id)
        self.assertEqual(list_item["subcategory"], subcategory_id)
        self.assertEqual(list_item["image"], object_url)
        self.assertEqual(list_item["primaryMediaId"], media_id)

        storefront_detail_response = self.client.get(f"/api/v1/products/{product_id}")
        storefront_detail = self._assert_status(storefront_detail_response, 200)["product"]
        self.assertEqual(storefront_detail["id"], product_id)
        self.assertEqual(storefront_detail["category"], category_id)
        self.assertEqual(storefront_detail["subcategory"], subcategory_id)
        self.assertEqual(storefront_detail["image"], object_url)
        self.assertEqual(storefront_detail["primaryMediaId"], media_id)
        self.assertFalse(
            {
                "review",
                "reviewFlags",
                "history",
                "createdBy",
                "updatedBy",
                "publishedBy",
                "publishedAt",
                "internalReference",
            }.intersection(storefront_detail)
        )

        # 11. Archive the exact subcategory through its lifecycle route.  The
        # prior list/detail reads are intentionally cached, so this asserts
        # both the visibility gate and the taxonomy cache invalidation.  A
        # finally block restores the taxonomy after the transition.
        archived = self._assert_status(
            self.client.post(f"/api/v1/admin/subcategories/{subcategory_id}/archive"),
            200,
        )["subcategory"]
        self.assertEqual(archived["status"], "ARCHIVED")
        try:
            archived_list = self._assert_status(
                self.client.get("/api/v1/products"),
                200,
            )
            self.assertNotIn(
                product_id,
                {item["id"] for item in archived_list["items"]},
                "archived subcategory must disappear from a fresh storefront list",
            )
            archived_detail = self.client.get(f"/api/v1/products/{product_id}")
            self.assertEqual(archived_detail.status_code, 404, archived_detail.text)
        finally:
            restored = self._assert_status(
                self.client.post(f"/api/v1/admin/subcategories/{subcategory_id}/restore"),
                200,
            )["subcategory"]
            self.assertEqual(restored["status"], "ACTIVE")

        restored_list = self._assert_status(self.client.get("/api/v1/products"), 200)
        self.assertIn(product_id, {item["id"] for item in restored_list["items"]})
        restored_detail = self._assert_status(
            self.client.get(f"/api/v1/products/{product_id}"),
            200,
        )["product"]
        self.assertEqual(restored_detail["image"], object_url)
        self.assertEqual(restored_detail["subcategory"], subcategory_id)
        self.assertEqual(restored_detail["primaryMediaId"], media_id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
