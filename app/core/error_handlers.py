from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppException
from app.core.logging import get_logger

logger = get_logger("app.error_handlers")


def _json_safe(value):
    """Coerce a value to JSON-serialisable primitives.

    `RequestValidationError.errors()` embeds live exception objects inside
    ``ctx.error`` whenever a validator raises (e.g. the ``ValueError`` raised
    by a lifecycle-key blocker or an id regex validator). Handing those
    objects straight to ``JSONResponse`` makes the 422 handler itself raise
    ``TypeError``, which ``ServerErrorMiddleware`` then turns into a 500.
    Walking the payload here keeps every field-level detail (``loc``, ``msg``,
    ``type``, ``input``, ``ctx``, ``url``) while reducing non-primitives to
    their string form so the envelope is always renderable.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    # Everything else — ValueError, enum members, arbitrary objects — becomes
    # its readable representation so the field-level detail is never lost.
    return str(value)


_HTTP_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    408: "REQUEST_TIMEOUT",
    409: "CONFLICT",
    410: "GONE",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "UNPROCESSABLE_ENTITY",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_SERVER_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


def register_error_handlers(app: FastAPI) -> None:
    """Register custom exception handlers on FastAPI instance."""

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        # Log 5xx errors as ERROR, 4xx as WARNING (expected business errors)
        if exc.status_code >= 500:
            logger.error(
                "AppException status=%s code=%s path=%s message=%s",
                exc.status_code, exc.error_code, request.url.path, exc.message,
            )
        else:
            logger.warning(
                "AppException status=%s code=%s path=%s message=%s",
                exc.status_code, exc.error_code, request.url.path, exc.message,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "details": exc.details,
                },
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        details = _json_safe(exc.errors())
        logger.warning(
            "Validation error path=%s errors=%s",
            request.url.path, details,
        )
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Invalid request payload or parameters",
                    "details": details,
                },
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        if exc.status_code >= 500:
            logger.error(
                "HTTPException status=%s path=%s detail=%s",
                exc.status_code, request.url.path, exc.detail,
            )
        else:
            logger.warning(
                "HTTPException status=%s path=%s detail=%s",
                exc.status_code, request.url.path, exc.detail,
            )
        code = _HTTP_STATUS_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
        message = str(exc.detail) if exc.detail else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {
                    "code": code,
                    "message": message,
                    "details": {},
                },
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error(
            "Unhandled exception path=%s method=%s error=%s",
            request.url.path, request.method, str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected error occurred. Please try again later.",
                    "details": {},
                },
            },
        )
