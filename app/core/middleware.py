"""
Application middleware setup.

Registers:
  - CORS
  - SlowAPI rate limiter (in-memory, single-process)
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.config import settings
from app.core.logging import get_logger

logger = get_logger("app.middleware")

# ---------------------------------------------------------------------------
# Rate limiter — module-level singleton so routes can import it with
#   from app.core.middleware import limiter
#   @limiter.limit("10/minute")
# ---------------------------------------------------------------------------
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://",
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a consistent error envelope for 429 responses."""
    logger.warning("Rate limit exceeded path=%s ip=%s limit=%s", request.url.path, request.client.host if request.client else "unknown", str(exc))
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "error": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": "Too many requests. Please slow down and try again.",
                "details": {"limit": str(exc)},
            },
        },
    )


access_logger = get_logger("app.access")


async def access_log_middleware(request: Request, call_next):
    """Log HTTP request lifecycle (method, path, status code, duration) to logs/access.log."""
    import time
    start_time = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    client_ip = request.client.host if request.client else "unknown"

    access_logger.info(
        "%s %s %s - %sms (IP: %s)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        client_ip,
    )
    return response


def setup_middleware(app: FastAPI) -> None:
    """Register all app-level middleware and exception handlers."""

    # Add HTTP request access logging middleware
    app.middleware("http")(access_log_middleware)

    # Attach the limiter to app state so SlowAPIMiddleware can find it
    app.state.limiter = limiter

    # SlowAPI must be added before CORS so it can intercept early
    app.add_middleware(SlowAPIMiddleware)

    # Handle 429 responses from slowapi with our standard error envelope
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

