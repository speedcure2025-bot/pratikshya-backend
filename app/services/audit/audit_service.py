"""
AuditService — the ONE centralized writer for the shared audit diary.

`audit_activity_log` (`ActivityLogModel`) is the single house log for every
domain mutation (its model docstring: "never create a second log — every
domain appends here"). Readers already exist (`GET /audit/logs`,
`GET /admin/activity`); this service is the counterpart writer, so no router
or service hand-rolls its own log insert ever again.

Conventions (kept identical to the frontend diary that already feeds the same
view):
  • ``action`` uses the shared SCREAMING_SNAKE vocabulary
    (``EMPLOYEE_CREATED``, ``ROLE_CHANGED``, ``PASSWORD_RESET`` …) —
    ``/audit/logs?action=`` filters on exact equality.
  • ``actor_employee_id`` holds the actor's PF employee code for
    employee-domain actors and ``None`` for admin-workspace actors, whose
    identity renders through ``actor_name`` as ``"Name · adminId"``.
  • Sensitive material never enters the log. ``redact()`` strips passwords,
    hashes, temporary passwords, tokens, secrets and OTPs from any detail
    mapping before it can reach ``summary``; the password-reset flow records
    only THAT a reset happened.

Transactional placement:
  • successful mutations append with the caller's session — the audit row
    commits or rolls back WITH the mutation it describes (no orphan trails);
  • refusals (403 before any write) are recorded via ``record_detached`` on
    a short-lived session, because the request's own transaction is rolled
    back when the exception unwinds. A failed audit write never masks the
    original error (logged, swallowed).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("pfv.audit")

#: Any key or value marker whose credential material must never reach the
#: diary (substring match, lower-cased — passwordHash/new_password/
#: temporaryPassword/access_token/refresh_token/secret/otp all hit).
SENSITIVE_KEY_MARKERS = (
    "password",
    "token",
    "secret",
    "otp",
    "pin",
    "credential",
    "hash",
    "authorization",
    "cookie",
)

REDACTED = "[redacted]"


def is_sensitive_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def redact(details: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Recursively strip credential material from a detail mapping."""
    if not details:
        return {}
    out: Dict[str, Any] = {}
    for key, value in details.items():
        if is_sensitive_key(key):
            out[str(key)] = REDACTED
        elif isinstance(value, dict):
            out[str(key)] = redact(value)
        elif isinstance(value, (list, tuple)):
            out[str(key)] = [redact(item) if isinstance(item, dict) else item for item in value]
        else:
            out[str(key)] = value
    return out


def format_summary(details: Optional[Dict[str, Any]] = None) -> str:
    """Compact human line for the diary: `k=v, k=v` of redacted details."""
    clean = redact(details)
    parts = ", ".join(f"{k}={v}" for k, v in clean.items() if v not in (None, "", [], {}))
    return parts


class AuditService:
    """Writes the shared audit diary on a caller-provided session."""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def _actor_context(self, actor_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """(actor_employee_code_or_None, 'Name · id') for a users.id.

        No relationship access — one profile lookup, never a lazy greenlet.
        """
        from app.models.auth.user import UserModel
        from app.models.employee.employee import EmployeeProfileModel

        if not actor_id:
            return None, None
        actor = await self.db.get(UserModel, actor_id)
        if not actor:
            return None, str(actor_id)
        name = actor.full_name or actor.email or str(actor_id)
        if actor.user_type == "employee":
            code = (
                await self.db.execute(
                    select(EmployeeProfileModel.employee_code).where(
                        EmployeeProfileModel.user_id == actor.id
                    )
                )
            ).scalars().first()
            if code:
                return code, f"{name} · {code}"
            return None, name
        return None, f"{name} · {actor.id}"

    async def record(
        self,
        *,
        action: str,
        actor_id: Optional[str] = None,
        summary: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        target_employee_id: Optional[str] = None,
        target_product_id: Optional[str] = None,
        target_offer_id: Optional[str] = None,
        target_category_id: Optional[str] = None,
        target_collection_id: Optional[str] = None,
        target_order_id: Optional[str] = None,
        target_return_id: Optional[str] = None,
        target_media_id: Optional[str] = None,
    ) -> None:
        """Append one diary entry on the caller's session (commits with it).

        ``target_employee_id`` carries the PF employee code (matching the
        frontend diary), not the internal UUID. Never call with credential
        values in ``details`` — anything password/token-shaped is redacted
        here anyway, but the intent is to not pass it at all.
        """
        from app.models.audit.activity_log import ActivityLogModel

        actor_employee_code, actor_name = await self._actor_context(actor_id)
        row = ActivityLogModel(
            actor_employee_id=actor_employee_code,
            actor_name=actor_name or "System",
            target_employee_id=target_employee_id,
            target_product_id=target_product_id,
            target_offer_id=target_offer_id,
            target_category_id=target_category_id,
            target_collection_id=target_collection_id,
            target_order_id=target_order_id,
            target_return_id=target_return_id,
            target_media_id=target_media_id,
            action=str(action)[:100],
            summary=(summary if summary is not None else format_summary(details) or str(action))[:2000],
        )
        self.db.add(row)
        # No commit here: the row rides the caller's transaction so an audit
        # trail can never describe a mutation that rolled back.


async def record_detached(**kwargs) -> None:
    """Audit a refusal/standalone event on a short-lived session.

    The caller's transaction is about to roll back (or never existed), so the
    entry is committed independently. Any failure is logged, never raised —
    an audit problem must not mask the original business exception.
    """
    try:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await AuditService(session).record(**kwargs)
            await session.commit()
    except Exception:  # noqa: BLE001 — audit must never break the request
        logger.exception("detached audit write failed for action=%s", kwargs.get("action"))
