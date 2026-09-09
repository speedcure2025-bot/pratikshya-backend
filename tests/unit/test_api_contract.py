"""
Contract tests for Phase 1 backend error envelopes and HTTP status codes.
"""

import pytest
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.error_handlers import register_error_handlers
from app.core.exceptions import (
    AppException,
    NotFoundException,
    UnauthorizedException,
    ForbiddenException,
    ConflictException,
    BusinessLogicException,
)
from app.main import app as main_app


def create_test_app() -> FastAPI:
    """Create an isolated test FastAPI instance with contract error handlers registered."""
    test_app = FastAPI()
    register_error_handlers(test_app)

    router = APIRouter()

    class SamplePayload(BaseModel):
        email: str = Field(..., min_length=5)
        age: int = Field(..., ge=18)

    @router.get("/test/not-found")
    async def route_not_found():
        raise NotFoundException(message="Item not found on test route", details={"itemId": 123})

    @router.get("/test/unauthorized")
    async def route_unauthorized():
        raise UnauthorizedException(message="Token missing or expired")

    @router.get("/test/forbidden")
    async def route_forbidden():
        raise ForbiddenException(message="Admin rights required")

    @router.get("/test/conflict")
    async def route_conflict():
        raise ConflictException(message="SKU already exists", details={"sku": "SKU-99"})

    @router.get("/test/business-logic")
    async def route_business():
        raise BusinessLogicException(message="Coupon expired", details={"coupon": "EXPIRED10"})

    @router.post("/test/validation")
    async def route_validation(payload: SamplePayload):
        return {"success": True, "data": payload.model_dump()}

    @router.get("/test/unhandled")
    async def route_unhandled():
        raise RuntimeError("Unexpected internal crash")

    test_app.include_router(router)
    return test_app


@pytest.fixture
def client():
    test_app = create_test_app()
    return TestClient(test_app, raise_server_exceptions=False)


def test_not_found_exception_contract(client):
    res = client.get("/test/not-found")
    assert res.status_code == 404
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "Item not found on test route"
    assert body["error"]["details"] == {"itemId": 123}


def test_unauthorized_exception_contract(client):
    res = client.get("/test/unauthorized")
    assert res.status_code == 401
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["message"] == "Token missing or expired"


def test_forbidden_exception_contract(client):
    res = client.get("/test/forbidden")
    assert res.status_code == 403
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "FORBIDDEN"
    assert body["error"]["message"] == "Admin rights required"


def test_conflict_exception_contract(client):
    res = client.get("/test/conflict")
    assert res.status_code == 409
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "CONFLICT"
    assert body["error"]["message"] == "SKU already exists"
    assert body["error"]["details"] == {"sku": "SKU-99"}


def test_business_logic_exception_contract(client):
    res = client.get("/test/business-logic")
    assert res.status_code == 422
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "BUSINESS_RULE_VIOLATION"
    assert body["error"]["message"] == "Coupon expired"
    assert body["error"]["details"] == {"coupon": "EXPIRED10"}


def test_pydantic_validation_error_preserves_details_array(client):
    res = client.post("/test/validation", json={"email": "a", "age": 10})
    assert res.status_code == 422
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"] == "Invalid request payload or parameters"
    assert isinstance(body["error"]["details"], list)
    assert len(body["error"]["details"]) >= 2
    # Check that location and error message fields are preserved
    locs = [err["loc"] for err in body["error"]["details"]]
    assert ["body", "email"] in locs
    assert ["body", "age"] in locs


def test_starlette_http_exception_404_route_contract(client):
    res = client.get("/this-route-does-not-exist")
    assert res.status_code == 404
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"
    assert "detail" not in body  # Must NOT be bare {"detail": "Not Found"}
    assert "error" in body


def test_unhandled_exception_500_contract(client):
    res = client.get("/test/unhandled")
    assert res.status_code == 500
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert body["error"]["message"] == "An unexpected error occurred. Please try again later."
    assert body["error"]["details"] == {}


def test_main_app_health_check_contract():
    main_client = TestClient(main_app)
    res = main_client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "online"
