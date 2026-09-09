"""Phase 3 Block 8 — collection and employee product contracts.

These tests stay at the service/schema boundary so the relational lookups and
response projection are covered even when PostgreSQL is not available.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.core.exceptions import BusinessLogicException, NotFoundException
from app.main import app
from app.schemas.catalog.collection import (
    AssignProductsRequest,
    CollectionCreateRequest,
    CollectionUpdateRequest,
)
from app.schemas.catalog.product import (
    AssignEmployeeRequest,
    EmployeeProduct,
    EmployeeProductUpdateRequest,
    ProductCreateRequest,
    ProductListQuery,
)
from app.services.catalog.collection_service import CollectionService
from app.services.catalog.product_service import ProductService


class _Scalars:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)

    def first(self):
        return self.values[0] if self.values else None


class _Result:
    def __init__(self, values):
        self.values = list(values)

    def scalars(self):
        return _Scalars(self.values)


class _DB:
    def __init__(self, *results):
        self.results = list(results)
        self.flush_count = 0
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, statement, *args, **kwargs):
        return self.results.pop(0) if self.results else _Result([])

    async def flush(self):
        self.flush_count += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = "COL-NEW"

    async def refresh(self, obj):
        return None


class CollectionAssociationContractTests(unittest.IsolatedAsyncioTestCase):
    def _collection(self, ids=None, kind="MANUAL"):
        return SimpleNamespace(
            id="COL-1", slug="collection", name="Editorial Edit", type=kind,
            status="DRAFT", explicit_product_ids=list(ids or []), rule={},
            updated_by=None, eyebrow="", description="", image="",
            hero_media_id=None, thumbnail_media_id=None, featured=False,
            sort_order=0, start_date=None, end_date=None,
        )

    async def test_put_validates_all_product_ids_and_deduplicates_in_order(self):
        collection = self._collection()
        db = _DB(_Result(["P-1", "P-2"]))
        service = CollectionService(db)
        service._get_or_404 = AsyncMock(return_value=collection)
        service._resolved_count = AsyncMock(return_value=2)
        with patch("app.services.catalog.collection_service.invalidate_response_cache", new=AsyncMock()):
            result = await service.assign_products(
                "COL-1", AssignProductsRequest(productIds=["P-2", "P-1", "P-2"]), "admin"
            )
        self.assertEqual(collection.explicit_product_ids, ["P-2", "P-1"])
        self.assertEqual(result.explicitProductIds, ["P-2", "P-1"])
        self.assertEqual(db.flush_count, 1)

    async def test_put_unknown_product_is_atomic_canonical_422(self):
        collection = self._collection(["P-1"])
        db = _DB(_Result(["P-1"]))
        service = CollectionService(db)
        service._get_or_404 = AsyncMock(return_value=collection)
        with self.assertRaises(BusinessLogicException) as caught:
            await service.assign_products(
                "COL-1", AssignProductsRequest(productIds=["P-1", "UNKNOWN"]), "admin"
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.error_code, "BUSINESS_RULE_VIOLATION")
        self.assertEqual(caught.exception.details, {"field": "productIds", "unknown": ["UNKNOWN"]})
        self.assertEqual(collection.explicit_product_ids, ["P-1"])
        self.assertEqual(db.flush_count, 0)

    async def test_create_and_update_explicit_ids_use_the_same_lookup(self):
        service = CollectionService(_DB(_Result(["P-1"])))
        service._assert_slug_unique = AsyncMock()
        with patch("app.services.catalog.collection_service.invalidate_response_cache", new=AsyncMock()):
            result = await service.create_collection(
                # A request with an unknown id is rejected before the row is added.
                # The valid path below proves the persisted list is canonical.
                CollectionCreateRequest(name="Edit", explicitProductIds=["P-1", "P-1"]),
                "admin",
            )
        self.assertEqual(result.explicitProductIds, ["P-1"])
        collection = result
        self.assertEqual(collection.explicitProductIds, ["P-1"])

        existing = self._collection(["P-1"])
        update_db = _DB(_Result([existing]), _Result(["P-2"]))
        update_service = CollectionService(update_db)
        with self.assertRaises(BusinessLogicException):
            await update_service.update_collection(
                "COL-1", CollectionUpdateRequest(explicitProductIds=["P-2", "UNKNOWN"]), "admin"
            )
        self.assertEqual(existing.explicit_product_ids, ["P-1"])

    async def test_rule_based_assignment_keeps_the_existing_409(self):
        from app.core.exceptions import ConflictException

        service = CollectionService(_DB())
        service._get_or_404 = AsyncMock(return_value=self._collection(kind="RULE_BASED"))
        with self.assertRaises(ConflictException) as caught:
            await service.assign_products("COL-1", AssignProductsRequest(productIds=[]), "admin")
        self.assertEqual(caught.exception.status_code, 409)

    async def test_removal_and_repeated_removal_are_authoritative_no_ops(self):
        collection = self._collection(["P-1", "P-2"])
        db = _DB()
        service = CollectionService(db)
        service._get_or_404 = AsyncMock(return_value=collection)
        with patch("app.services.catalog.collection_service.invalidate_response_cache", new=AsyncMock()):
            await service.assign_products("COL-1", AssignProductsRequest(productIds=[]), "admin")
            await service.assign_products("COL-1", AssignProductsRequest(productIds=[]), "admin")
        self.assertEqual(collection.explicit_product_ids, [])
        self.assertEqual(db.flush_count, 2)

    async def test_draft_archived_and_missing_collections_keep_the_existing_404(self):
        for status in ("DRAFT", "ARCHIVED"):
            service = CollectionService(_DB())
            service._get_or_404 = AsyncMock(return_value=self._collection(kind="MANUAL"))
            service._get_or_404.return_value.status = status
            with self.assertRaises(NotFoundException) as caught:
                await service.get_collection_product_ids("COL-1")
            self.assertEqual(caught.exception.status_code, 404)

        service = CollectionService(_DB())
        service._get_or_404 = AsyncMock(side_effect=NotFoundException("Collection missing"))
        with self.assertRaises(NotFoundException) as caught:
            await service.get_collection_product_ids("MISSING")
        self.assertEqual(caught.exception.status_code, 404)


class EmployeeContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_employee_code_does_not_mutate_assignment(self):
        product = SimpleNamespace(id="P-1", assigned_employee_id="EMP-OLD")
        service = ProductService(_DB(_Result([])))
        service._get_or_404 = AsyncMock(return_value=product)
        with self.assertRaises(BusinessLogicException) as caught:
            await service.assign_employee(
                "P-1", AssignEmployeeRequest(employeeId="EMP-UNKNOWN"), "admin"
            )
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.error_code, "BUSINESS_RULE_VIOLATION")
        self.assertEqual(caught.exception.details, {"field": "employeeId", "value": "EMP-UNKNOWN"})
        self.assertEqual(product.assigned_employee_id, "EMP-OLD")
        self.assertEqual(service.db.flush_count, 0)

    async def test_valid_reassignment_and_null_unassignment_are_persisted(self):
        first = SimpleNamespace(id="P-1", slug="p-1", assigned_employee_id="EMP-OLD", history=[], updated_by=None)
        service = ProductService(_DB(_Result(["EMP-NEW"])))
        service._get_or_404 = AsyncMock(return_value=first)
        service._to_admin_current = AsyncMock(return_value=object())
        service.invalidate_product_cache = AsyncMock()
        reassigned = await service.assign_employee(
            "P-1", AssignEmployeeRequest(employeeId="EMP-NEW"), "admin"
        )
        self.assertIsNotNone(reassigned)
        self.assertEqual(first.assigned_employee_id, "EMP-NEW")

        second = SimpleNamespace(id="P-1", slug="p-1", assigned_employee_id="EMP-NEW", history=[], updated_by=None)
        service.db = _DB()
        service._get_or_404 = AsyncMock(return_value=second)
        unassigned = await service.assign_employee(
            "P-1", AssignEmployeeRequest(employeeId=None), "admin"
        )
        self.assertIsNotNone(unassigned)
        self.assertIsNone(second.assigned_employee_id)

    def test_employee_projection_cannot_expose_admin_workflow_or_audit_fields(self):
        payload = {
            "id": "P-1", "name": "Saree", "review": {"state": "APPROVED"},
            "reviewFlags": ["MISSING_DESCRIPTION"], "history": [{"field": "name"}],
            "priceHistory": [{"from": 1, "to": 2}], "createdBy": "admin",
            "updatedBy": "admin", "publishedBy": "admin", "publishedAt": "now",
        }
        employee = EmployeeProduct.model_validate(payload)
        dumped = employee.model_dump(by_alias=True)
        for key in ("review", "reviewFlags", "history", "priceHistory", "createdBy", "updatedBy", "publishedBy", "publishedAt"):
            self.assertNotIn(key, dumped)
        self.assertEqual(dumped["id"], "P-1")

    def test_employee_collection_writes_are_rejected_not_discarded(self):
        with self.assertRaises(ValidationError):
            EmployeeProductUpdateRequest.model_validate({"collectionIds": ["COL-1"]})
        with self.assertRaises(ValidationError):
            ProductCreateRequest.model_validate({"name": "Saree", "collections": ["Editorial Edit"]})

    def test_assignment_request_accepts_canonical_alias_and_rejects_empty_code(self):
        self.assertEqual(AssignEmployeeRequest.model_validate({"employeeId": "EMP-1"}).employee_id, "EMP-1")
        with self.assertRaises(ValidationError):
            AssignEmployeeRequest.model_validate({"employeeId": ""})

    def test_collection_route_membership_restriction_is_a_real_field(self):
        query = ProductListQuery(collection_product_ids=["P-1"])
        self.assertEqual(query.collection_product_ids, ["P-1"])

    def test_openapi_declares_employee_projection_and_strict_collection_writes(self):
        spec = app.openapi()
        employee_get = spec["paths"]["/api/v1/employee/products/{id}"]["get"]
        employee_patch = spec["paths"]["/api/v1/employee/products/{id}"]["patch"]
        self.assertEqual(
            employee_get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/SingleEmployeeProductResponse",
        )
        self.assertEqual(
            employee_patch["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/SingleEmployeeProductResponse",
        )
        self.assertFalse(spec["components"]["schemas"]["CollectionCreateRequest"]["additionalProperties"])
        self.assertFalse(spec["components"]["schemas"]["CollectionUpdateRequest"]["additionalProperties"])
        self.assertNotIn("review", spec["components"]["schemas"]["EmployeeProduct"]["properties"])
        self.assertNotIn("history", spec["components"]["schemas"]["EmployeeProduct"]["properties"])
        self.assertTrue(spec["paths"]["/api/v1/admin/collections/{collection_id}/products"]["put"]["security"])
        self.assertIsNone(spec["paths"]["/api/v1/collections/{collection_id}/products"]["get"].get("security"))
        self.assertTrue(spec["paths"]["/api/v1/employee/products/{id}"]["get"]["security"])


if __name__ == "__main__":
    unittest.main()
