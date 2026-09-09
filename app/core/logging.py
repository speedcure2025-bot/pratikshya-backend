"""
Centralized logging configuration for Pratikshya Fashon backend.

Log output
──────────────────────────────────────────────────────────────────────────────
  Console     — structured JSON via structlog (stdout)
  File        — rotating plain-text logs written to  logs/<name>.log
                (10 MB per file, 5 backups kept, UTF-8)

Log files produced
──────────────────────────────────────────────────────────────────────────────
  logs/app.log        — root / general application events
  logs/access.log     — HTTP request lifecycle (middleware)
  logs/auth.log       — authentication & identity events
  logs/orders.log     — order lifecycle events
  logs/payments.log   — payment & webhook events
  logs/errors.log     — WARNING and above from every logger

Usage (any module)
──────────────────────────────────────────────────────────────────────────────
  from app.core.logging import get_logger

  logger = get_logger(__name__)           # module-level logger
  logger.info("user_registered", user_id=user.id, email=user.email)
  logger.warning("rate_limit_hit", ip=ip)
  logger.error("db_error", error=str(exc), exc_info=True)
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Project root is two levels above this file: app/core/logging.py  → project/
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = _PROJECT_ROOT / "logs"

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Rotating file: 10 MB per file, keep 5 backups
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_rotating_handler(filename: str, level: int = logging.DEBUG) -> logging.Handler:
    """Return a RotatingFileHandler for *filename* inside LOGS_DIR."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOGS_DIR / filename,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    return handler


def _make_stream_handler() -> logging.Handler:
    """Return a stdout StreamHandler (used as the structlog sink)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


# ---------------------------------------------------------------------------
# Public setup function — called once at application startup
# ---------------------------------------------------------------------------

def setup_logging(log_level: int = logging.INFO) -> None:
    """
    Configure the root logger, named loggers, and structlog.

    Call this once inside the FastAPI lifespan handler (``app/main.py``).
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Root / catch-all logger ────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(log_level)
    # Avoid adding handlers multiple times on hot-reload
    if not root.handlers:
        root.addHandler(_make_stream_handler())
    root.addHandler(_make_rotating_handler("app.log", level=log_level))

    # ── Error-only log (WARNING+) ──────────────────────────────────────────
    error_handler = _make_rotating_handler("errors.log", level=logging.WARNING)
    root.addHandler(error_handler)

    # ── Named loggers with dedicated log files ─────────────────────────────
    _named: dict[str, str] = {
        "app.access":   "access.log",
        "app.auth":     "auth.log",
        "app.orders":   "orders.log",
        "app.payments": "payments.log",
    }
    for logger_name, filename in _named.items():
        lg = logging.getLogger(logger_name)
        lg.setLevel(log_level)
        lg.addHandler(_make_rotating_handler(filename, level=log_level))
        lg.propagate = True   # also flows to root (app.log + errors.log)

    # ── Third-party noise reduction ────────────────────────────────────────
    for noisy in ("sqlalchemy.engine", "httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # ── structlog: structured JSON on stdout ───────────────────────────────
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.getLogger("app").info(
        "Logging initialised — writing to %s", LOGS_DIR
    )


# ---------------------------------------------------------------------------
# Module-level helper — use this in every module
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# Auto-initialize logging on module load
setup_logging()

