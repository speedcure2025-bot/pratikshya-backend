"""
ReturnService — admin returns desk business logic.

Covers:
  GET  /admin/returns
  GET  /admin/returns/{id}
  POST /admin/returns/{id}/approve
  POST /admin/returns/{id}/reject        ReturnRejectRequest
  POST /admin/returns/{id}/schedule-pickup SchedulePickupRequest
  POST /admin/returns/{id}/receive       ReceiveReturnRequest
  POST /admin/returns/{id}/inspect       InspectReturnRequest
  POST /admin/returns/{id}/refund/initiate
  POST /admin/returns/{id}/refund/complete

Valid return transitions:
  RETURN_REQUESTED → APPROVED | REJECTED
  APPROVED         → PICKUP_SCHEDULED
  PICKUP_SCHEDULED → RECEIVED
  RECEIVED         → INSPECTED
  INSPECTED        → REFUND_INITIATED
  REFUND_INITIATED → REFUNDED
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BusinessLogicException, NotFoundException
from app.models.orders.return_order import ReturnOrderModel
from app.models.orders.return_item import ReturnItemModel
from app.schemas.orders.order import (
    InspectReturnRequest,
    ReceiveReturnRequest,
    ReturnRejectRequest,
    SchedulePickupRequest,
)

RETURN_TRANSITIONS: Dict[str, set] = {
    "RETURN_REQUESTED": {"APPROVED", "REJECTED"},
    "APPROVED":         {"PICKUP_SCHEDULED"},
    "PICKUP_SCHEDULED": {"RECEIVED"},
    "RECEIVED":         {"INSPECTED"},
    "INSPECTED":        {"REFUND_INITIATED"},
    "REFUND_INITIATED": {"REFUNDED"},
    "REJECTED":         set(),
    "REFUNDED":         set(),
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _can_transition(current: str, next_s: str) -> bool:
    return next_s in RETURN_TRANSITIONS.get(current, set())


def _timeline_event(event: str, actor_id: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"event": event, "at": _now_utc().isoformat()}
    if actor_id:
        entry["actorId"] = actor_id
    if note:
        entry["note"] = note
    return entry


async def _load_return(db: AsyncSession, return_id: str) -> ReturnOrderModel:
    stmt = (
        select(ReturnOrderModel)
        .where(ReturnOrderModel.id == return_id)
        .options(selectinload(ReturnOrderModel.items))
    )
    result = await db.execute(stmt)
    ret = result.scalars().first()
    if not ret:
        raise NotFoundException(f"Return '{return_id}' not found.")
    return ret


class ReturnService:
    """Admin-side business logic for the returns desk."""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    # ── List returns ──────────────────────────────────────────────────────────

    async def list_returns(
        self,
        status: Optional[str] = None,
        order_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """GET /admin/returns."""
        conditions = []
        if status:
            conditions.append(ReturnOrderModel.status == status)
        if order_id:
            conditions.append(ReturnOrderModel.order_id == order_id)
        if customer_id:
            conditions.append(ReturnOrderModel.customer_id == customer_id)

        base_query = select(ReturnOrderModel)
        if conditions:
            base_query = base_query.where(*conditions)

        count_stmt = select(func.count()).select_from(base_query.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        paginated = (
            base_query
            .options(selectinload(ReturnOrderModel.items))
            .order_by(ReturnOrderModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.db.execute(paginated)
        returns = result.scalars().all()

        return {"returns": returns, "total": total}

    # ── Get single return ─────────────────────────────────────────────────────

    async def get_return(self, return_id: str) -> ReturnOrderModel:
        """GET /admin/returns/{id}."""
        return await _load_return(self.db, return_id)

    # ── Approve return ────────────────────────────────────────────────────────

    async def approve_return(self, return_id: str, actor_id: str) -> ReturnOrderModel:
        """POST /admin/returns/{id}/approve → APPROVED."""
        ret = await _load_return(self.db, return_id)
        if not _can_transition(ret.status, "APPROVED"):
            raise BusinessLogicException(
                f"Cannot approve return in status '{ret.status}'."
            )
        ret.status = "APPROVED"
        ret.reviewed_by = actor_id
        ret.reviewed_at = _now_utc()
        timeline = list(ret.timeline or [])
        timeline.append(_timeline_event("RETURN_APPROVED", actor_id=actor_id))
        ret.timeline = timeline
        await self.db.flush()
        return await _load_return(self.db, ret.id)

    # ── Reject return ─────────────────────────────────────────────────────────

    async def reject_return(
        self, return_id: str, req: ReturnRejectRequest, actor_id: str
    ) -> ReturnOrderModel:
        """POST /admin/returns/{id}/reject → REJECTED."""
        ret = await _load_return(self.db, return_id)
        if not _can_transition(ret.status, "REJECTED"):
            raise BusinessLogicException(
                f"Cannot reject return in status '{ret.status}'."
            )
        ret.status = "REJECTED"
        ret.rejection_reason = req.reason
        ret.rejection_reason_customer = req.customer_message or _customer_facing_rejection(req.reason)
        ret.reviewed_by = actor_id
        ret.reviewed_at = _now_utc()
        timeline = list(ret.timeline or [])
        timeline.append(_timeline_event("RETURN_REJECTED", actor_id=actor_id, note=req.reason))
        ret.timeline = timeline
        await self.db.flush()
        return await _load_return(self.db, ret.id)

    # ── Schedule pickup ───────────────────────────────────────────────────────

    async def schedule_pickup(
        self, return_id: str, req: SchedulePickupRequest, actor_id: str
    ) -> ReturnOrderModel:
        """POST /admin/returns/{id}/schedule-pickup → PICKUP_SCHEDULED."""
        ret = await _load_return(self.db, return_id)
        if not _can_transition(ret.status, "PICKUP_SCHEDULED"):
            raise BusinessLogicException(
                f"Cannot schedule pickup in status '{ret.status}'."
            )
        ret.status = "PICKUP_SCHEDULED"
        ret.pickup_scheduled_at = req.scheduled_at
        if req.pickup_address:
            ret.pickup_address = req.pickup_address
        timeline = list(ret.timeline or [])
        timeline.append(_timeline_event("RETURN_PICKUP_SCHEDULED", actor_id=actor_id))
        ret.timeline = timeline
        await self.db.flush()
        return await _load_return(self.db, ret.id)

    # ── Receive return ────────────────────────────────────────────────────────

    async def receive_return(
        self, return_id: str, req: ReceiveReturnRequest, actor_id: str
    ) -> ReturnOrderModel:
        """POST /admin/returns/{id}/receive → RECEIVED."""
        ret = await _load_return(self.db, return_id)
        if not _can_transition(ret.status, "RECEIVED"):
            raise BusinessLogicException(
                f"Cannot receive return in status '{ret.status}'."
            )
        ret.status = "RECEIVED"
        ret.package_condition = req.package_condition
        if req.notes:
            ret.inspection_notes = req.notes
        timeline = list(ret.timeline or [])
        timeline.append(_timeline_event("RETURN_RECEIVED", actor_id=actor_id))
        ret.timeline = timeline
        await self.db.flush()
        return await _load_return(self.db, ret.id)

    # ── Inspect return ────────────────────────────────────────────────────────

    async def inspect_return(
        self, return_id: str, req: InspectReturnRequest, actor_id: str
    ) -> ReturnOrderModel:
        """POST /admin/returns/{id}/inspect → INSPECTED."""
        ret = await _load_return(self.db, return_id)
        if not _can_transition(ret.status, "INSPECTED"):
            raise BusinessLogicException(
                f"Cannot inspect return in status '{ret.status}'."
            )
        ret.status = "INSPECTED"
        ret.inspection_condition = req.inspection_condition
        if req.notes:
            ret.inspection_notes = req.notes
        ret.reviewed_by = actor_id
        ret.reviewed_at = _now_utc()
        timeline = list(ret.timeline or [])
        timeline.append(_timeline_event("RETURN_INSPECTED", actor_id=actor_id))
        ret.timeline = timeline
        await self.db.flush()
        return await _load_return(self.db, ret.id)

    # ── Initiate refund ───────────────────────────────────────────────────────

    async def initiate_refund(self, return_id: str, actor_id: str) -> ReturnOrderModel:
        """POST /admin/returns/{id}/refund/initiate → REFUND_INITIATED."""
        ret = await _load_return(self.db, return_id)
        if not _can_transition(ret.status, "REFUND_INITIATED"):
            raise BusinessLogicException(
                f"Cannot initiate refund in status '{ret.status}'."
            )
        ret.status = "REFUND_INITIATED"
        ret.refund_status = "REQUESTED"
        ret.refund_initiated_at = _now_utc()
        timeline = list(ret.timeline or [])
        timeline.append(_timeline_event("REFUND_INITIATED", actor_id=actor_id))
        ret.timeline = timeline
        await self.db.flush()
        return await _load_return(self.db, ret.id)

    # ── Complete refund ───────────────────────────────────────────────────────

    async def complete_refund(self, return_id: str, actor_id: str) -> ReturnOrderModel:
        """POST /admin/returns/{id}/refund/complete → REFUNDED."""
        ret = await _load_return(self.db, return_id)
        if not _can_transition(ret.status, "REFUNDED"):
            raise BusinessLogicException(
                f"Cannot complete refund in status '{ret.status}'."
            )
        ret.status = "REFUNDED"
        ret.refund_status = "REFUNDED"
        ret.refund_completed_at = _now_utc()
        timeline = list(ret.timeline or [])
        timeline.append(_timeline_event("REFUND_COMPLETED", actor_id=actor_id))
        ret.timeline = timeline
        await self.db.flush()
        return await _load_return(self.db, ret.id)


# ── Helpers ───────────────────────────────────────────────────────────────────

_REJECTION_COPY: Dict[str, str] = {
    "ITEM_NOT_RETURNED":   "We did not receive the item(s) in your return package.",
    "ITEM_USED":           "The returned item appears to have been used and does not meet our return policy.",
    "OUTSIDE_WINDOW":      "Your return request is outside the permitted return window.",
    "MISSING_TAGS":        "The item was returned without original tags or packaging.",
    "WRONG_ITEM":          "The item received does not match what was originally ordered.",
}


def _customer_facing_rejection(reason: str) -> str:
    """Map an internal rejection code to customer-facing copy."""
    return _REJECTION_COPY.get(reason, "We are unable to process your return at this time.")
