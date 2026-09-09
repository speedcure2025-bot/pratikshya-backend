import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.v1.products import submit_for_review as submit_for_review_route
from app.core.exceptions import ForbiddenException
from app.schemas.catalog.product import ProductListQuery
from app.services.catalog.product_service import ProductService


def product_stub(**overrides):
    base = dict(
        id="PF-TEST-001",
        product_id="PF-TEST-001",
        name="Phase 1 Test Product",
        slug="phase-1-test-product",
        sku="PF-TEST-SKU",
        brand="Pratikshya Fashon",
        product_type="fashion",
        category="active-category",
        subcategory="",
        gender="Women",
        short_description="",
        description="A real product record for unit projection.",
        highlights=[],
        care_instructions=[],
        delivery_info="",
        return_info="",
        fabric="",
        material="",
        primary_color="",
        secondary_color="",
        colors=[],
        patterns=[],
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
        pricing=None,
        price=1000,
        original_price=None,
        currency="INR",
        stock=5,
        availability="in-stock",
        rating=None,
        review_count=0,
        image="/images/test.webp",
        hover_image="",
        additional_images=[],
        primary_media_id=None,
        status="PUBLISHED",
        published=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeScalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class FakeResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return FakeScalars(self.values)


class Phase1SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_customer_cannot_submit_product_for_review(self):
        customer = SimpleNamespace(id="customer-1", user_type="customer")

        with self.assertRaises(ForbiddenException):
            await submit_for_review_route("PF-TEST-001", current_user=customer, db=AsyncMock())

    async def test_storefront_product_list_excludes_archived_category_products(self):
        active_product = product_stub(id="active", category="active-category")
        archived_product = product_stub(id="archived", category="archived-category")
        db = AsyncMock()
        db.execute.return_value = FakeResult([active_product, archived_product])

        service = ProductService(db)
        result = await service.list_storefront_products(
            ProductListQuery(page=1, pageSize=20),
            category_status_map={
                "active-category": "ACTIVE",
                "archived-category": "ARCHIVED",
            },
        )

        self.assertEqual([item.id for item in result["items"]], ["active"])
        self.assertEqual(result["total"], 1)

    async def test_assigned_employee_submit_uses_employee_code_actor(self):
        draft = product_stub(
            id="draft",
            category="active-category",
            status="DRAFT",
            published=False,
            assigned_employee_id="PF-EMP-00001",
            review={"state": "NONE"},
            review_flags=[],
            history=[],
            price_history=[],
            compare_at_price=None,
            product_code="",
            barcode="",
            internal_reference="",
            specifications={},
            work=[],
            flags={},
            compareAtPrice=None,
            inventory_tracked=False,
            low_stock_threshold=5,
            seo=None,
            media_ids=[],
            gallery_media_ids=[],
            created_by=None,
            created_at=None,
            updated_by=None,
            updated_at=None,
            published_by=None,
            published_at=None,
        )
        db = AsyncMock()
        service = ProductService(db)
        service._get_or_404 = AsyncMock(return_value=draft)

        product = await service.submit_for_review(
            "draft",
            actor="PF-EMP-00001",
            require_assignment=True,
            employee_user_id="user-uuid-1",
        )

        self.assertEqual(product.review.submitted_by, "PF-EMP-00001")
        self.assertEqual(draft.updated_by, "PF-EMP-00001")


if __name__ == "__main__":
    unittest.main()
