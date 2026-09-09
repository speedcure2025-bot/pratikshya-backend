"""Disposable local-PostgreSQL provisioning for verification runs.

Every schema/integration check in this project needs a real PostgreSQL server,
and none of them may touch a database that belongs to anyone else. This module
is the single place those two rules are enforced:

1. **Loopback only.** The configured ``DATABASE_URL`` must point at
   ``localhost`` / ``127.0.0.1`` / ``::1``. A remote host — the company server
   included — is rejected before a connection is ever opened.
2. **The disposable database only.** The configured database must be
   ``pratikshya_local``, the throwaway development database.

Checks never write to ``pratikshya_local`` itself. They create their own
database next to it (``pf_<prefix>_<random>``), apply the real Alembic chain to
it, and drop it again when the context manager exits. That also means every run
re-proves requirement: a fresh PostgreSQL reaches the complete schema with
``alembic upgrade head`` and nothing else.

Usage::

    from app.testing.local_postgres import throwaway_database, unavailable_reason

    reason = unavailable_reason()          # None when it is safe to proceed
    with throwaway_database("media_it") as db:
        run_checks(db.dsn)                 # psycopg2 conninfo
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Optional

BACKEND_ROOT = Path(__file__).resolve().parents[2]

#: Hosts that count as "this machine".
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

#: The only database a verification run may connect to.
REQUIRED_DATABASE = "pratikshya_local"

URL_RE = re.compile(
    r"^(?P<scheme>postgresql|postgres)(?:\+(?P<driver>[a-z0-9]+))?://"
    r"(?:(?P<user>[^:/@]+)(?::(?P<password>[^@/]*)?)?@)?"
    r"(?P<host>[^/:?]+)?(?::(?P<port>\d+))?(?:/(?P<db>[^?]*))?",
    re.IGNORECASE,
)


class LocalDatabaseUnavailable(RuntimeError):
    """Raised when a verification run must not (or cannot) use PostgreSQL here."""


@dataclass(frozen=True)
class ThrowawayDatabase:
    """A private database created for one verification run."""

    name: str
    #: psycopg2/libpq connection string for the throwaway database.
    dsn: str
    #: SQLAlchemy-style URL for the throwaway database (same driver as configured).
    url: str
    #: psycopg2/libpq connection string for the configured local server database.
    admin_dsn: str


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def env_value(name: str) -> Optional[str]:
    """Process environment first (like ``alembic/env.py``), then ``backend/.env``."""
    value = os.environ.get(name)
    if value:
        return value
    env_file = BACKEND_ROOT / ".env"
    if not env_file.exists():
        return None
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().split(" #", 1)[0].strip().strip("'\"")
    return None


def parse_url(url: str) -> Dict[str, str]:
    match = URL_RE.match(url.strip())
    if not match:
        raise LocalDatabaseUnavailable(f"DATABASE_URL could not be parsed: {redact(url)!r}")
    return {
        "scheme": match.group("scheme"),
        "driver": match.group("driver") or "",
        "user": match.group("user") or "postgres",
        "password": match.group("password") or "",
        "host": match.group("host") or "localhost",
        "port": match.group("port") or "5432",
        "db": match.group("db") or "",
    }


def redact(url: str) -> str:
    """Strip an embedded password before anything is printed or stored."""
    return re.sub(r"(://[^:/@]+):([^@/]+)@", r"\1:***@", url)


def dsn_for(parts: Dict[str, str], database: str) -> str:
    dsn = (
        f"host={parts['host']} port={parts['port']} "
        f"dbname={database} user={parts['user']}"
    )
    if parts["password"]:
        dsn += f" password={parts['password']}"
    return dsn


def url_for(parts: Dict[str, str], database: str) -> str:
    driver = f"+{parts['driver']}" if parts["driver"] else ""
    auth = parts["user"]
    if parts["password"]:
        auth += f":{parts['password']}"
    return f"{parts['scheme']}{driver}://{auth}@{parts['host']}:{parts['port']}/{database}"


# --------------------------------------------------------------------------- #
# The safety gate
# --------------------------------------------------------------------------- #
def unavailable_reason() -> Optional[str]:
    """Why a local-PostgreSQL verification run cannot happen here, else ``None``."""
    url = env_value("DATABASE_URL")
    if not url:
        return "DATABASE_URL is not set"
    try:
        parts = parse_url(url)
    except LocalDatabaseUnavailable as exc:
        return str(exc)
    if parts["host"] not in LOOPBACK_HOSTS:
        return (
            f"DATABASE_URL host {parts['host']!r} is not a loopback address — "
            "refusing to touch a shared or company server"
        )
    if parts["db"] != REQUIRED_DATABASE:
        return (
            f"DATABASE_URL database {parts['db']!r} is not {REQUIRED_DATABASE!r} — "
            "point it at the disposable local development database"
        )
    try:
        import psycopg2
    except ImportError:
        return "psycopg2 is not installed"

    try:
        connection = psycopg2.connect(dsn_for(parts, REQUIRED_DATABASE), connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 - reported verbatim to the caller
        return f"could not connect to the local PostgreSQL server: {exc}"
    connection.close()
    return None


def require_local_target() -> Dict[str, str]:
    """Return the parsed local target, or raise :class:`LocalDatabaseUnavailable`."""
    reason = unavailable_reason()
    if reason is not None:
        raise LocalDatabaseUnavailable(reason)
    url = env_value("DATABASE_URL")
    assert url is not None
    return parse_url(url)


# --------------------------------------------------------------------------- #
# Provisioning
# --------------------------------------------------------------------------- #
@contextmanager
def create_database(prefix: str) -> Iterator[ThrowawayDatabase]:
    """Create an EMPTY private database on the local server and drop it on exit.

    The database is not migrated — call :func:`alembic_upgrade_head` yourself
    when you want to report the migration as a distinct step.

    Raises :class:`LocalDatabaseUnavailable` if the configured target is not the
    disposable local database.
    """
    parts = require_local_target()
    admin_dsn = dsn_for(parts, REQUIRED_DATABASE)
    name = f"pf_{prefix}_{uuid.uuid4().hex[:10]}"

    import psycopg2

    admin = psycopg2.connect(admin_dsn)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()

    try:
        yield ThrowawayDatabase(
            name=name,
            dsn=dsn_for(parts, name),
            url=url_for(parts, name),
            admin_dsn=admin_dsn,
        )
    finally:
        _drop(admin_dsn, name)


@contextmanager
def throwaway_database(prefix: str) -> Iterator[ThrowawayDatabase]:
    """Create, migrate and finally drop a private database on the local server.

    Raises :class:`LocalDatabaseUnavailable` if the configured target is not the
    disposable local database, or if ``alembic upgrade head`` fails against it.
    """
    with create_database(prefix) as database:
        alembic_upgrade_head(database)
        yield database


def alembic_upgrade_head(database: ThrowawayDatabase) -> str:
    """Run the real migration chain into ``database``; raise on failure."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_ROOT),
        env={**os.environ, "DATABASE_URL": database.url},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _drop(database.admin_dsn, database.name)
        raise LocalDatabaseUnavailable(
            "`alembic upgrade head` did not apply to a fresh PostgreSQL:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def alembic_current(database: ThrowawayDatabase) -> str:
    """``alembic current`` output for ``database``."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=str(BACKEND_ROOT),
        env={**os.environ, "DATABASE_URL": database.url},
        capture_output=True,
        text=True,
    )
    return (result.stdout or "") + (result.stderr or "")


def alembic_heads() -> str:
    """``alembic heads`` output for the repository (no database involved)."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
    )
    return (result.stdout or "") + (result.stderr or "")


def _drop(admin_dsn: str, name: str) -> None:
    import psycopg2

    admin = psycopg2.connect(admin_dsn)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cursor.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        admin.close()


__all__ = [
    "BACKEND_ROOT",
    "LOOPBACK_HOSTS",
    "REQUIRED_DATABASE",
    "LocalDatabaseUnavailable",
    "ThrowawayDatabase",
    "alembic_current",
    "alembic_heads",
    "alembic_upgrade_head",
    "create_database",
    "dsn_for",
    "env_value",
    "parse_url",
    "redact",
    "require_local_target",
    "throwaway_database",
    "unavailable_reason",
    "url_for",
]
