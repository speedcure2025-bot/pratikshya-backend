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
from app.api.v1.iclock import router as iclock_router

# Main application entry point — PRATIKSHYA FASHION API (schema synced)
logger = get_logger("app.main")


async def _check_pending_migrations() -> None:
    """
    Startup guard — fails fast if any mapped table is missing from the DB.

    Derives the expected table list directly from Base.metadata so the check
    stays in sync automatically as new models are added; no table names are
    hardcoded here.

    Raises RuntimeError with a clear message when tables are missing so the
    server refuses to start rather than silently returning 503 to users at
    runtime.
    """
    from sqlalchemy import text
    from app.core.database import engine
    from app.models.base import Base

    # Collect every (schema, table_name) pair registered across all ORM models.
    # Base.metadata is already populated because app.models was imported above.
    expected: list[tuple[str, str]] = []
    for table in Base.metadata.tables.values():
        schema = table.schema or "public"
        expected.append((schema, table.name))

    if not expected:
        logger.warning("Migration check skipped — no ORM tables found in metadata.")
        return

    # Build a single query that checks all tables at once using
    # information_schema.tables — one round-trip regardless of table count.
    # Each row returns the schema+table only if it EXISTS in the DB.
    conditions = " OR ".join(
        f"(table_schema = '{s}' AND table_name = '{t}')"
        for s, t in expected
    )
    query = text(
        f"SELECT table_schema, table_name "
        f"FROM information_schema.tables "
        f"WHERE {conditions}"
    )

    try:
        async with engine.connect() as conn:
            result = await conn.execute(query)
            found = {(row.table_schema, row.table_name) for row in result}
    except Exception as exc:
        # DB itself is unreachable — let the normal connection error surface
        # naturally so we don't mask the real cause with a migration message.
        logger.error("Migration check could not reach the database: %s", exc)
        raise

    missing = sorted(
        f"{s}.{t}" for s, t in expected if (s, t) not in found
    )

    if missing:
        missing_list = "\n  ".join(missing)
        raise RuntimeError(
            f"\n\n{'='*60}\n"
            f"STARTUP ABORTED — {len(missing)} table(s) missing from the database.\n"
            f"Run:  alembic upgrade heads\n\n"
            f"Missing tables:\n  {missing_list}\n"
            f"{'='*60}\n"
        )

    logger.info(
        "Migration check passed — all %d mapped tables present in the database.",
        len(expected),
    )


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

    # --- Migration guard: abort startup if any ORM table is missing ---
    await _check_pending_migrations()

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
    description="PRATIKSHYA FASHION — Feature-Based Backend API for Customer, Employee, and Admin surfaces.",
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
# Punching machines call fixed root paths (/iclock/cdata ...), outside /api/v1.
app.include_router(iclock_router)


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
