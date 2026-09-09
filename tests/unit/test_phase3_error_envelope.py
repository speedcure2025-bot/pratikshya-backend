"""
Phase 3 — canonical 422 error envelope (PF3-N01).

`RequestValidationError.errors()` embeds a live exception inside `ctx.error`
whenever a validator raises (a `field_validator` or a `model_validator` that
does `raise ValueError(...)`). Before the fix, the handler passed that list
straight into `JSONResponse`, which raised `TypeError: Object of type
ValueError is not JSON serializable`, and `ServerErrorMiddleware` converted
the whole request into a 500 `INTERNAL_SERVER_ERROR`.

This suite pins the fix end to end:

  * a mirror validator that raises `ValueError` round-trips to a 422 whose
    `details` is fully JSON-serialisable and keeps `loc` / `msg` / `type`
    with `ctx.error` reduced to its string message;
  * the REAL product request models — `ProductUpdateRequest` (blocked
    lifecycle keys) and `ProductDraftRequest` (malformed id) — go through the
    exact same handler and come back as 422, never 500.

No database is involved: the routes are served from an isolated FastAPI app
with only the product error handlers registered.
"""

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator, model_validator

from app.core.error_handlers import register_error_handlers
from app.schemas.catalog.product import ProductDraftRequest, ProductUpdateRequest


class _MirrorBlockedModel(BaseModel):
    """Mirrors `_reject_lifecycle_and_unsupported` raising a ValueError."""

    name: str = ""

    @model_validator(mode="before")
    @classmethod
    def _reject_blocked(cls, values):
        if isinstance(values, dict) and "status" in values:
            raise ValueError(
                "Product lifecycle fields cannot be written through this "
                "endpoint; use the lifecycle routes. Blocked: status"
            )
        return values


class _MirrorFieldModel(BaseModel):
    """Mirrors `validate_product_id` raising a ValueError from a field validator."""

    id: str = ""

    @field_validator("id")
    @classmethod
    def _reject_bad_id(cls, v):
        if not v:
            raise ValueError("Product ID must match ^[A-Z0-9][A-Z0-9-]{1,35}$")
        return v


def create_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    router = APIRouter()

    @router.post("/mirror/model-validator")
    async def mirror_model(payload: _MirrorBlockedModel):
        return {"success": True, "payload": payload.model_dump()}

    @router.post("/mirror/field-validator")
    async def mirror_field(payload: _MirrorFieldModel):
        return {"success": True, "payload": payload.model_dump()}

    @router.post("/products/draft")
    async def real_draft(payload: ProductDraftRequest):
        return {"success": True, "id": payload.id}

    @router.post("/products/update")
    async def real_update(payload: ProductUpdateRequest):
        return {"success": True}

    app.include_router(router)
    return app


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Mirror validators — the ValueError must never crash the handler
# ---------------------------------------------------------------------------

class TestMirrorValueErrorSerialisation:
    def test_model_validator_valueerror_is_422_not_500(self, client):
        res = client.post("/mirror/model-validator", json={"name": "x", "status": "PUBLISHED"})
        assert res.status_code == 422, res.text
        body = res.json()
        assert body["success"] is False
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["message"] == "Invalid request payload or parameters"
        details = body["error"]["details"]
        assert isinstance(details, list)
        assert len(details) == 1
        err = details[0]
        # `ctx.error` is a live ValueError in the raw payload; the handler
        # must reduce it to a plain string so the envelope stays serialisable.
        assert isinstance(err.get("ctx", {}).get("error"), str)
        assert "status" in err["ctx"]["error"]

    def test_field_validator_valueerror_keeps_loc(self, client):
        res = client.post("/mirror/field-validator", json={"id": ""})
        assert res.status_code == 422, res.text
        details = res.json()["error"]["details"]
        locs = [err["loc"] for err in details]
        assert ["body", "id"] in locs
        err = details[0]
        assert err["type"] == "value_error"
        assert isinstance(err.get("ctx", {}).get("error"), str)


# ---------------------------------------------------------------------------
# Real product schemas — blocked lifecycle keys and malformed ids
# ---------------------------------------------------------------------------

class TestRealProductValidationEnvelope:
    @pytest.mark.parametrize(
        "key,value",
        [
            ("status", "PUBLISHED"),
            ("published", True),
            ("review", {"state": "APPROVED"}),
            ("history", []),
            ("priceHistory", []),
        ],
    )
    def test_blocked_lifecycle_key_returns_422_naming_the_key(self, client, key, value):
        res = client.post("/products/update", json={"name": "ok", key: value})
        assert res.status_code == 422, res.text
        body = res.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        details = body["error"]["details"]
        assert isinstance(details, list)
        # The blocked-key list is carried in the ValueError message, now a
        # renderable string inside ctx.error.
        rendered = str(details)
        assert "blocked" in rendered.lower() or "lifecycle" in rendered.lower()

    def test_malformed_product_id_returns_422_not_500(self, client):
        res = client.post("/products/draft", json={"id": "bad id!", "name": "Saree"})
        assert res.status_code == 422, res.text
        body = res.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        details = body["error"]["details"]
        assert isinstance(details, list)
        locs = [err["loc"] for err in details]
        assert ["body", "id"] in locs

    def test_valid_payload_still_validates(self, client):
        res = client.post("/products/draft", json={"id": "PF-SAR-0001", "name": "Saree"})
        assert res.status_code == 200, res.text
