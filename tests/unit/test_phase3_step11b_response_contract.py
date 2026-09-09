"""Phase 3 Step 11B response DTO and OpenAPI contract regressions."""

import unittest

from app.main import app
from app.schemas.catalog.collection import (
    TaxonomyMetricsResponse,
    TaxonomyProductCountsResponse,
)
from app.schemas.common import ErrorResponse
from app.schemas.media.media import (
    MediaAssetListResponse,
    MediaRegistrationResponse,
)


class Step11BResponseContractTests(unittest.TestCase):
    def test_media_register_preserves_unassigned_and_assigned_wire_shapes(self):
        unassigned = {
            "ok": True,
            "media": {
                "id": "media-1",
                "objectKey": "products/PF-W-0001/cover.png",
                "url": "/api/v1/media/objects/products/PF-W-0001/cover.png",
                "mimeType": "image/png",
                "title": None,
                "altText": None,
                "status": "uploaded",
            },
            "assigned": False,
            "assignment": None,
        }
        self.assertEqual(
            MediaRegistrationResponse.model_validate(unassigned).model_dump(by_alias=True),
            unassigned,
        )

        assigned = {
            **unassigned,
            "assigned": True,
            "assignment": {
                "productId": "PF-W-0001",
                "mediaId": "media-1",
                "role": "gallery",
                "sortOrder": 2,
                "isPrimary": True,
            },
        }
        self.assertEqual(
            MediaRegistrationResponse.model_validate(assigned).model_dump(by_alias=True),
            assigned,
        )

    def test_media_assets_preserves_empty_and_populated_lists_without_pagination(self):
        empty = {"ok": True, "items": []}
        self.assertEqual(
            MediaAssetListResponse.model_validate(empty).model_dump(by_alias=True),
            empty,
        )

        populated = {
            "ok": True,
            "items": [
                {
                    "id": "media-1",
                    "objectKey": "products/PF-W-0001/cover.png",
                    "url": "/api/v1/media/objects/products/PF-W-0001/cover.png",
                    "status": "legacy-status-is-preserved",
                    "mimeType": "image/png",
                }
            ],
        }
        self.assertEqual(
            MediaAssetListResponse.model_validate(populated).model_dump(by_alias=True),
            populated,
        )
        self.assertNotIn("page", populated)
        self.assertNotIn("pageSize", populated)

    def test_taxonomy_metrics_preserves_empty_and_populated_status_counts(self):
        empty = {
            "ok": True,
            "collections": {"total": 0, "byStatus": {}},
            "categories": {"total": 0},
            "subcategories": {"total": 0},
        }
        self.assertEqual(
            TaxonomyMetricsResponse.model_validate(empty).model_dump(by_alias=True),
            empty,
        )

        populated = {
            "ok": True,
            "collections": {
                "total": 5,
                "byStatus": {"ACTIVE": 3, "DRAFT": 2},
            },
            "categories": {"total": 12},
            "subcategories": {"total": 24},
        }
        self.assertEqual(
            TaxonomyMetricsResponse.model_validate(populated).model_dump(by_alias=True),
            populated,
        )

    def test_taxonomy_product_counts_preserves_empty_and_populated_lists(self):
        empty = {"ok": True, "counts": []}
        self.assertEqual(
            TaxonomyProductCountsResponse.model_validate(empty).model_dump(by_alias=True),
            empty,
        )

        populated = {
            "ok": True,
            "counts": [
                {"collectionId": "collection-1", "name": "Silk", "productCount": 7},
                {"collectionId": "collection-2", "name": "Archive", "productCount": 0},
            ],
        }
        self.assertEqual(
            TaxonomyProductCountsResponse.model_validate(populated).model_dump(by_alias=True),
            populated,
        )

    def test_canonical_error_dto_documents_runtime_envelope(self):
        error = {
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request payload or parameters",
                "details": [
                    {"loc": ["body", "object_key"], "msg": "Field required", "type": "missing"}
                ],
            },
        }
        self.assertEqual(ErrorResponse.model_validate(error).model_dump(), error)

        business_error = {
            "success": False,
            "error": {
                "code": "NOT_FOUND",
                "message": "Media object not found.",
                "details": {},
            },
        }
        self.assertEqual(ErrorResponse.model_validate(business_error).model_dump(), business_error)

    def test_required_routes_have_typed_success_and_canonical_error_responses(self):
        spec = app.openapi()
        required_routes = {
            "/api/v1/media/register": (
                "post",
                "201",
                "MediaRegistrationResponse",
                {"201", "401", "403", "404", "422", "500"},
            ),
            "/api/v1/media/assets": (
                "get",
                "200",
                "MediaAssetListResponse",
                {"200", "401", "403", "500"},
            ),
            "/api/v1/admin/taxonomy/metrics": (
                "get",
                "200",
                "TaxonomyMetricsResponse",
                {"200", "401", "403", "500"},
            ),
            "/api/v1/admin/taxonomy/product-counts": (
                "get",
                "200",
                "TaxonomyProductCountsResponse",
                {"200", "401", "403", "500"},
            ),
        }

        for path, (method, success_status, success_schema, expected_statuses) in required_routes.items():
            with self.subTest(path=path):
                operation = spec["paths"][path][method]
                self.assertEqual(set(operation["responses"]), expected_statuses)
                self.assertEqual(
                    operation["responses"][success_status]["content"]["application/json"]["schema"]["$ref"],
                    f"#/components/schemas/{success_schema}",
                )
                for status_code, response in operation["responses"].items():
                    if status_code == "200" or status_code == "201":
                        continue
                    schema = response.get("content", {}).get("application/json", {}).get("schema", {})
                    self.assertEqual(schema.get("$ref"), "#/components/schemas/ErrorResponse")
                    self.assertNotIn("HTTPValidationError", str(response))

        error_schema = spec["components"]["schemas"]["ErrorResponse"]
        self.assertEqual(set(error_schema["required"]), {"success", "error"})
        detail_schema = spec["components"]["schemas"]["ErrorDetail"]
        self.assertEqual(set(detail_schema["required"]), {"code", "message", "details"})

    def test_workflow_metrics_route_is_retained_as_compatibility_alias(self):
        spec = app.openapi()
        self.assertIn("/api/v1/admin/products/metrics", spec["paths"])
        self.assertIn("/api/v1/admin/workflow/metrics", spec["paths"])

        products_operation = spec["paths"]["/api/v1/admin/products/metrics"]["get"]
        workflow_operation = spec["paths"]["/api/v1/admin/workflow/metrics"]["get"]
        products_ref = products_operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        workflow_ref = workflow_operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        self.assertEqual(products_ref, "#/components/schemas/CatalogMetricsResponse")
        self.assertEqual(workflow_ref, products_ref)
        self.assertIn("compatibility alias", workflow_operation["summary"].lower())
        self.assertIn("compatibility alias", workflow_operation["description"].lower())


if __name__ == "__main__":
    unittest.main()
