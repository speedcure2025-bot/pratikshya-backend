"""
AI Business Assistant — REAL, authenticated, read-only (admin consolidation).

Replaces the production mock provider for the admin Business Assistant:
the browser no longer ships business data to a client-side "AI" — the
question goes to ONE authenticated endpoint which answers from the DATABASE
through bounded, read-only queries over the existing models.

Contract:
  * Auth: admin token + `analytics.view` permission (401/403 otherwise).
  * The request carries a QUESTION and a period preset — never data. The
    client cannot push orders/inventory/workforce into the answer, so no
    fabricated numbers can enter through the payload.
  * Answers are computed from the same canonical definitions the analytics
    engine uses (revenue statuses, stock thresholds). Figures are real; when
    a register is empty the answer says so (NO_DATA) instead of inventing.
  * Every query is bounded (windows ≤ 90 days, lists LIMIT ≤ 10). No client
    SQL, no DB credentials in the frontend, no arbitrary queries — the
    assistant is a retrieval surface over fixed, read-only questions.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessLogicException
from app.core.constants import REVENUE_ORDER_STATUSES as _REVENUE_STATUSES
from app.dependencies import get_current_admin, get_db, require_admin_permission
from app.models.auth.user import UserModel
from app.models.catalog.product import ProductModel
from app.models.orders.order import OrderModel
from app.models.orders.order_item import OrderItemModel
from app.models.orders.return_order import ReturnOrderModel

router = APIRouter(prefix="/ai", tags=["AI Assistant"])

MAX_QUESTION_CHARS = 500
MAX_LIST_ROWS = 10


class PeriodPreset(str, Enum):
    LAST_7 = "LAST_7"
    LAST_30 = "LAST_30"
    LAST_90 = "LAST_90"


PRESET_DAYS = {PeriodPreset.LAST_7: 7, PeriodPreset.LAST_30: 30, PeriodPreset.LAST_90: 90}


class AiBusinessAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    preset: PeriodPreset = PeriodPreset.LAST_30

    model_config = ConfigDict(extra="ignore")  # client payload keys are ignored

    @field_validator("question")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question must not be blank")
        return cleaned


# ---------------------------------------------------------------------------
# Topic resolution — deterministic keyword rules (the same vocabulary the
# admin quick-prompts use). No LLM, so the answer can never be invented.
# ---------------------------------------------------------------------------

TOPIC_RULES = [
    ("RESTOCK", ["restock", "replenish", "reorder"]),
    ("ATTENTION", ["focus on", "needs attention", "need attention", "priorit", "what should i do", "where should i"]),
    ("SUMMARY", ["summary", "overview", "how is the business", "how is business", "today", "recap", "status of the business", "daily report"]),
    ("RETURNS", ["return", "refund", "exchanged"]),
    ("OFFERS", ["offer", "coupon", "promo", "discount code", "campaign"]),
    ("INVENTORY", ["stock", "inventory", "warehouse"]),
    ("FULFILLMENT", ["fulfil", "fulfill", "dispatch", "delayed", "delivery performance", "shipping", "queue", "packed", "picked"]),
    ("CUSTOMERS", ["customer", "high value", "repeat purchase", "retention", "shoppers", "clients"]),
    ("WORKFORCE", ["attendance", "employee", "staff", "workforce", "team performance", "absent", "present", "leave"]),
    ("CATEGORIES", ["category", "categories", "which category"]),
    ("PRODUCTS", ["product", "selling the most", "top seller", "best selling", "which piece"]),
    ("SALES", ["sales", "revenue", "turnover", "aov", "average order"]),
]


def resolve_topic(question: str) -> str:
    flat = re.sub(r"\s+", " ", question.lower()).strip()
    for topic, patterns in TOPIC_RULES:
        if any(pattern in flat for pattern in patterns):
            return topic
    return "UNCLEAR"


# ---------------------------------------------------------------------------
# Bounded read-only helpers (SELECT only — the assistant never writes)
# ---------------------------------------------------------------------------

async def _period_metrics(db: AsyncSession, since: datetime) -> Dict[str, Any]:
    totals = (
        await db.execute(
            select(
                func.coalesce(func.sum(OrderModel.total), 0),
                func.count(OrderModel.id),
                func.coalesce(func.sum(
                    case(
                        (OrderModel.status.in_(_REVENUE_STATUSES), OrderModel.total), else_=0
                    )
                ), 0),
            ).where(OrderModel.created_at >= since)
        )
    ).first() or (0, 0, 0)
    cancelled = (
        await db.execute(
            select(func.count()).select_from(OrderModel).where(
                OrderModel.status == "CANCELLED", OrderModel.created_at >= since
            )
        )
    ).scalar() or 0
    return {
        "orders": int(totals[1] or 0),
        "gross": int(totals[0] or 0),
        "revenue": int(totals[2] or 0),
        "cancelled": int(cancelled),
        "aov": int(totals[2] or 0) // max(1, int(totals[1] or 0)),
    }


async def _inventory_counts(db: AsyncSession) -> Dict[str, Any]:
    row = (
        await db.execute(
            select(
                func.count(ProductModel.id),
                func.sum(
                    case(
                        (ProductModel.stock <= ProductModel.low_stock_threshold, 1), else_=0
                    )
                ),
                func.sum(
                    case((ProductModel.stock == 0, 1), else_=0)
                ),
            ).where(ProductModel.published.is_(True))
        )
    ).first() or (0, 0, 0)
    return {"products": int(row[0] or 0), "low": int(row[1] or 0), "out": int(row[2] or 0)}


async def _low_stock_rows(db: AsyncSession, limit: int = 5) -> List[Dict[str, Any]]:
    rows = (
        await db.execute(
            select(ProductModel.name, ProductModel.stock, ProductModel.low_stock_threshold)
            .where(
                ProductModel.published.is_(True),
                ProductModel.stock <= ProductModel.low_stock_threshold,
            )
            .order_by(ProductModel.stock.asc())
            .limit(limit)
        )
    ).all()
    return [{"label": r[0], "value": f"{int(r[1] or 0)} in stock (threshold {int(r[2] or 0)})"} for r in rows]


async def _top_products(db: AsyncSession, since: datetime, limit: int = 5) -> List[Dict[str, Any]]:
    rows = (
        await db.execute(
            select(
                OrderItemModel.product_name,
                func.sum(OrderItemModel.line_total),
                func.sum(OrderItemModel.quantity),
            )
            .join(OrderModel, OrderModel.id == OrderItemModel.order_id)
            .where(OrderModel.created_at >= since, OrderModel.status.in_(_REVENUE_STATUSES))
            .group_by(OrderItemModel.product_name)
            .order_by(func.sum(OrderItemModel.line_total).desc())
            .limit(limit)
        )
    ).all()
    return [{"label": r[0], "value": f"₹{int(r[1] or 0):,} · {int(r[2] or 0)} pcs"} for r in rows]


async def _category_revenue(db: AsyncSession, since: datetime, limit: int = 5) -> List[Dict[str, Any]]:
    rows = (
        await db.execute(
            select(
                func.coalesce(func.nullif(ProductModel.category, ""), "unassigned"),
                func.sum(OrderItemModel.line_total),
            )
            .join(OrderModel, OrderModel.id == OrderItemModel.order_id)
            .join(ProductModel, ProductModel.id == OrderItemModel.product_id)
            .where(OrderModel.created_at >= since, OrderModel.status.in_(_REVENUE_STATUSES))
            .group_by(func.coalesce(func.nullif(ProductModel.category, ""), "unassigned"))
            .order_by(func.sum(OrderItemModel.line_total).desc())
            .limit(limit)
        )
    ).all()
    return [{"label": str(r[0]), "value": f"₹{int(r[1] or 0):,}"} for r in rows]


async def _top_customers(db: AsyncSession, since: datetime, limit: int = 5) -> List[Dict[str, Any]]:
    rows = (
        await db.execute(
            select(OrderModel.customer_id, func.sum(OrderModel.total), func.count())
            .where(
                OrderModel.created_at >= since,
                OrderModel.customer_id.isnot(None),
                OrderModel.status.in_(_REVENUE_STATUSES),
            )
            .group_by(OrderModel.customer_id)
            .order_by(func.sum(OrderModel.total).desc())
            .limit(limit)
        )
    ).all()
    out = []
    for customer_id, spend, orders in rows:
        name = (
            await db.execute(
                select(UserModel.full_name).where(UserModel.id == customer_id)
            )
        ).scalar()
        out.append({
            "label": name or customer_id[:8],
            "value": f"₹{int(spend or 0):,} · {int(orders)} orders",
        })
    return out


async def _returns_summary(db: AsyncSession, since: datetime) -> Dict[str, Any]:
    rows = (
        await db.execute(
            select(ReturnOrderModel.status, func.count(), func.coalesce(func.sum(ReturnOrderModel.refund_amount), 0))
            .where(ReturnOrderModel.created_at >= since)
            .group_by(ReturnOrderModel.status)
        )
    ).all()
    counts = {r[0]: int(r[1]) for r in rows}
    refunded_value = next((int(r[2]) for r in rows if r[0] == "REFUNDED"), 0)
    return {"counts": counts, "total": sum(counts.values()), "refunded": refunded_value}


async def _offers_summary(db: AsyncSession) -> Dict[str, Any]:
    from app.models.commerce.coupon import CouponModel

    active = (
        await db.execute(
            select(func.count()).select_from(CouponModel).where(CouponModel.is_active.is_(True))
        )
    ).scalar() or 0
    redemptions = (
        await db.execute(select(func.coalesce(func.sum(CouponModel.usage_count), 0)))
    ).scalar() or 0
    return {"active": int(active), "redemptions": int(redemptions)}


async def _fulfillment_pipeline(db: AsyncSession) -> Dict[str, int]:
    rows = (await db.execute(select(OrderModel.status, func.count()).group_by(OrderModel.status))).all()
    return {r[0]: int(r[1]) for r in rows}


async def _customer_counts(db: AsyncSession) -> Dict[str, int]:
    total = (
        await db.execute(
            select(func.count()).select_from(UserModel).where(UserModel.user_type == "customer")
        )
    ).scalar() or 0
    return {"total": int(total)}


def _inr(value: int) -> str:
    return f"₹{int(value):,}"


# ---------------------------------------------------------------------------
# Answer builders — every figure comes from the queries above; empty
# registers produce truthful NO_DATA answers, never placeholders.
# ---------------------------------------------------------------------------

def _period_label(days: int) -> str:
    return f"Last {days} days"


def _answer(
    kind: str,
    headline: str,
    text: str,
    *,
    metrics: Optional[List[Dict[str, str]]] = None,
    rows: Optional[List[Dict[str, str]]] = None,
    suggestions: Optional[List[str]] = None,
    actions: Optional[List[Dict[str, str]]] = None,
    period_label: str = "",
) -> Dict[str, Any]:
    return {
        "type": kind,
        "headline": headline,
        "text": text,
        "metrics": metrics or [],
        "rows": rows or [],
        "suggestions": suggestions or [],
        "actions": actions or [],
        "periodLabel": period_label,
        "source": "Live business data — read from the database just now",
    }


async def build_business_answer(db: AsyncSession, question: str, preset: PeriodPreset) -> Dict[str, Any]:
    topic = resolve_topic(question)
    days = PRESET_DAYS[preset]
    label = _period_label(days)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    if topic == "UNCLEAR":
        return _answer(
            "NO_DATA",
            "I couldn't map that to a business question",
            "I can answer from the live registers about sales, products, categories, "
            "customers, inventory, restocking, returns, offers, fulfilment and the "
            "workforce. Try one of the suggestions below.",
            suggestions=[
                "Give me the business summary for this period.",
                "Which products are low in stock?",
                "How are returns trending?",
            ],
        )

    if topic in ("SUMMARY", "ATTENTION"):
        metrics = await _period_metrics(db, since)
        inventory = await _inventory_counts(db)
        if metrics["orders"] == 0 and inventory["products"] == 0:
            return _answer(
                "NO_DATA",
                "No business activity recorded yet",
                "There are no orders and no published products in the database for this period. "
                "As real orders arrive, this summary will reflect them — I never show placeholder numbers.",
                period_label=label,
            )
        parts = [
            f"{_inr(metrics['revenue'])} revenue across {metrics['orders']} orders "
            f"(AOV {_inr(metrics['aov'])}); {inventory['low']} low-stock and "
            f"{inventory['out']} out-of-stock products."
        ]
        kind = "BUSINESS_SUMMARY"
        if topic == "ATTENTION":
            kind = "ALERT"
            parts.append(
                "Priority: restock the low-stock products below before they go out of stock."
                if inventory["low"]
                else "No stock alerts right now — the catalogue is healthy."
            )
        rows = await _low_stock_rows(db)
        return _answer(
            kind,
            "The business, in brief",
            " ".join(parts),
            metrics=[
                {"label": "Revenue", "value": _inr(metrics["revenue"])},
                {"label": "Orders", "value": str(metrics["orders"])},
                {"label": "AOV", "value": _inr(metrics["aov"])},
                {"label": "Low stock", "value": str(inventory["low"])},
            ],
            rows=rows,
            period_label=label,
        )

    if topic == "SALES":
        metrics = await _period_metrics(db, since)
        if metrics["orders"] == 0:
            return _answer(
                "NO_DATA",
                "No sales in this period",
                "No orders were recorded in this window, so there is no revenue to report yet.",
                period_label=label,
            )
        return _answer(
            "SALES_INSIGHT",
            "Sales performance",
            f"{_inr(metrics['revenue'])} revenue from {metrics['orders']} orders "
            f"(gross {_inr(metrics['gross'])}, {_inr(metrics['aov'])} average order value). "
            f"{metrics['cancelled']} orders were cancelled in the same window.",
            metrics=[
                {"label": "Revenue", "value": _inr(metrics["revenue"])},
                {"label": "Orders", "value": str(metrics["orders"])},
                {"label": "AOV", "value": _inr(metrics["aov"])},
                {"label": "Cancelled", "value": str(metrics["cancelled"])},
            ],
            period_label=label,
        )

    if topic == "PRODUCTS":
        rows = await _top_products(db, since)
        if not rows:
            return _answer(
                "NO_DATA",
                "No product sales in this period",
                "No revenue-eligible order lines were recorded in this window.",
                period_label=label,
            )
        return _answer(
            "PRODUCT_INSIGHT",
            "Top products by revenue",
            "The strongest products this period, by revenue-eligible line totals:",
            rows=rows,
            period_label=label,
        )

    if topic == "CATEGORIES":
        rows = await _category_revenue(db, since)
        if not rows:
            return _answer(
                "NO_DATA",
                "No category sales in this period",
                "No revenue-eligible order lines were recorded in this window.",
                period_label=label,
            )
        return _answer(
            "CATEGORY_INSIGHT",
            "Revenue by category",
            "Category revenue for revenue-eligible orders in this period:",
            rows=rows,
            period_label=label,
        )

    if topic == "CUSTOMERS":
        counts = await _customer_counts(db)
        rows = await _top_customers(db, since)
        if counts["total"] == 0:
            return _answer(
                "NO_DATA",
                "No customers registered yet",
                "The customer directory is empty, so there is nothing to analyse yet.",
                period_label=label,
            )
        if not rows:
            return _answer(
                "NO_DATA",
                "No customer purchases in this period",
                f"{counts['total']} customers are registered, but none placed an order in this window.",
                period_label=label,
            )
        return _answer(
            "CUSTOMER_INSIGHT",
            "Top customers by spend",
            "Highest-spending registered customers in this period:",
            metrics=[{"label": "Registered customers", "value": str(counts["total"])}],
            rows=rows,
            period_label=label,
        )

    if topic in ("INVENTORY", "RESTOCK"):
        inventory = await _inventory_counts(db)
        rows = await _low_stock_rows(db)
        if inventory["products"] == 0:
            return _answer(
                "NO_DATA",
                "No published products yet",
                "The catalogue has no published products, so there is no stock to assess.",
                period_label=label,
            )
        if topic == "RESTOCK" and inventory["low"] == 0:
            return _answer(
                "INVENTORY_INSIGHT",
                "Nothing needs restocking",
                f"All {inventory['products']} published products are above their low-stock thresholds.",
                period_label=label,
            )
        return _answer(
            "INVENTORY_INSIGHT",
            "Stock health",
            f"{inventory['low']} of {inventory['products']} published products are at or below "
            f"their low-stock threshold; {inventory['out']} are out of stock.",
            metrics=[
                {"label": "Low stock", "value": str(inventory["low"])},
                {"label": "Out of stock", "value": str(inventory["out"])},
                {"label": "Products", "value": str(inventory["products"])},
            ],
            rows=rows,
            actions=([{"label": "Open low stock desk", "to": "/admin/inventory"}] if inventory["low"] else None),
            period_label=label,
        )

    if topic == "RETURNS":
        summary = await _returns_summary(db, since)
        if summary["total"] == 0:
            return _answer(
                "NO_DATA",
                "No returns in this period",
                "No return requests were recorded in this window.",
                period_label=label,
            )
        pending = summary["counts"].get("RETURN_REQUESTED", 0) + summary["counts"].get("UNDER_REVIEW", 0)
        return _answer(
            "RETURN_INSIGHT",
            "Returns activity",
            f"{summary['total']} return requests in this period; {_inr(summary['refunded'])} has "
            f"actually been refunded. {pending} await review.",
            metrics=[
                {"label": "Requests", "value": str(summary["total"])},
                {"label": "Awaiting review", "value": str(pending)},
                {"label": "Refunded", "value": _inr(summary["refunded"])},
            ],
            actions=[{"label": "Open the returns desk", "to": "/admin/returns"}],
            period_label=label,
        )

    if topic == "OFFERS":
        summary = await _offers_summary()
        if summary["active"] == 0 and summary["redemptions"] == 0:
            return _answer(
                "NO_DATA",
                "No offer activity",
                "No coupons exist yet — create one on the offers desk and its redemptions "
                "will show up here.",
                period_label=label,
            )
        return _answer(
            "OFFER_INSIGHT",
            "Offers at a glance",
            f"{summary['active']} active coupons with {summary['redemptions']} lifetime redemptions. "
            f"Per-order discount effects live on the offers desk.",
            metrics=[
                {"label": "Active coupons", "value": str(summary["active"])},
                {"label": "Lifetime redemptions", "value": str(summary["redemptions"])},
            ],
            actions=[{"label": "Open offers", "to": "/admin/offers"}],
            period_label=label,
        )

    if topic == "FULFILLMENT":
        pipeline = await _fulfillment_pipeline(db)
        if not pipeline:
            return _answer(
                "NO_DATA",
                "No orders to fulfil",
                "There are no orders in the pipeline yet.",
                period_label=label,
            )
        rows = [{"label": status.replace("_", " ").title(), "value": str(count)} for status, count in pipeline.items()]
        return _answer(
            "FULFILLMENT_INSIGHT",
            "Fulfilment pipeline",
            "Live order counts by fulfilment stage:",
            rows=rows,
            period_label=label,
        )

    if topic == "WORKFORCE":
        total = (
            await db.execute(
                select(func.count()).select_from(UserModel).where(UserModel.user_type == "employee")
            )
        ).scalar() or 0
        if total == 0:
            return _answer(
                "NO_DATA",
                "No employees registered yet",
                "No employee accounts exist, so workforce analytics have nothing to read.",
                period_label=label,
            )
        return _answer(
            "WORKFORCE_INSIGHT",
            "Workforce register",
            f"{total} employee accounts exist. Attendance and performance records are not "
            f"populated by any backend flow yet, so I won't quote attendance or performance "
            f"figures until real data exists.",
            metrics=[{"label": "Employees", "value": str(total)}],
            period_label=label,
        )

    raise BusinessLogicException("Unhandled assistant topic.")


@router.post(
    "/business/ask",
    summary="Ask the AI Business Assistant (admin, read-only)",
    description=(
        "Authorization: `analytics.view` permission. The question is answered from "
        "live database reads through bounded, read-only queries. The request never "
        "accepts business data from the client, and the assistant never fabricates "
        "figures — empty registers produce explicit 'no data' answers."
    ),
)
async def ask_business_assistant(
    payload: AiBusinessAskRequest,
    current_user: UserModel = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await require_admin_permission(current_user, db, "analytics.view")
    answer = await build_business_answer(db, payload.question, payload.preset)
    return {"ok": True, "response": answer}
