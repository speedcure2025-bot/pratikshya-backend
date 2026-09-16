from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend

from app.config import settings
from app.core.error_handlers import register_error_handlers
from app.core.logging import get_logger, setup_logging
from app.core.middleware import setup_middleware
from app.core.redis import close_redis, init_redis
import app.models  # noqa: F401 — ensures all SQLAlchemy models are registered before mapper config
from app.api.v1.router import api_router

# Main application entry point — PRATIKSHYA FASHON API (schema synced)
logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    setup_logging()
    logger.info(
        "Starting %s (env=%s, debug=%s)",
        settings.APP_NAME,
        settings.APP_ENV,
        settings.DEBUG,
    )

    # --- In-process LRU cache store ---
    await init_redis()
    logger.info("Redis connection initialised")

    # --- HTTP response cache (fastapi-cache2 with in-memory backend) ---
    FastAPICache.init(
        backend=InMemoryBackend(),
        prefix="pratikshya:cache",
    )
    logger.info("FastAPI in-memory response cache initialised")

    yield

    # --- Graceful shutdown ---
    logger.info("Shutting down — closing Redis connection")
    await close_redis()
    logger.info("Application shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    description="PRATIKSHYA FASHON — Feature-Based Backend API for Customer, Employee, and Admin surfaces.",
    version="1.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

from fastapi.responses import JSONResponse
from sqlalchemy import text
from app.core.database import engine

# Configure Middlewares & Error Handlers
setup_middleware(app)
register_error_handlers(app)

# Include API v1 Router
# The mount prefix is read from settings so the media-URL builder
# (settings.media_url_prefix_absolute) and the router cannot drift apart.
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["System"])
async def root_health_check():
    db_status = "connected"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"disconnected ({type(e).__name__})"

    is_healthy = db_status == "connected"
    return JSONResponse(
        status_code=200 if is_healthy else 503,
        content={
            "status": "online" if is_healthy else "degraded",
            "app_name": settings.APP_NAME,
            "environment": settings.APP_ENV,
            "database": db_status,
        },
    )
