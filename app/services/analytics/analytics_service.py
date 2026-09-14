"""Canonical business metrics — single source of truth for analytics.py and ai_assistant.py.

Every aggregate query the analytics dashboard and AI Business Assistant need is
defined here once. Both API routers call into this service instead of re-implementing
the same SQLAlchemy queries independently.

All methods are bounded, read-only, and never modify data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import REVENUE_ORDER_STATUSES as _REVENUE_STATUSES
from app.models.auth.user import UserModel
from app.models.catalog.product import ProductModel
from app.models.customer.customer import CustomerProfileModel
from app.models.employee.employee import EmployeeProfileModel
from app.models.orders.order import OrderModel
from app.models.orders.order_item import OrderItemModel
from app.models.orders.return_order import ReturnOrderModel


class AnalyticsService:
    """Business logic service for bounded, read-only analytics queries."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Headline metrics ──────────────────────────────────────────────────

    async def revenue_total(self, *, since: Optional[datetime] = None) -> int:
        """Total revenue from revenue-eligible orders, optionally bounded by date."""
        stmt = select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            OrderModel.status.in_(_REVENUE_STATUSES)
        )
        if since:
            stmt = stmt.where(OrderModel.created_at >= since)
        return int((await self.db.execute(stmt)).scalar() or 0)

    async def order_counts(self, *, since: Optional[datetime] = None) -> Dict[str, int]:
        """Total orders, cancelled count, and revenue-eligible count."""
        total_stmt = select(func.count()).select_from(OrderModel)
        cancelled_stmt = select(func.count()).select_from(OrderModel).where(
            OrderModel.status == "CANCELLED"
        )
        if since:
            total_stmt = total_stmt.where(OrderModel.created_at >= since)
            cancelled_stmt = cancelled_stmt.where(OrderModel.created_at >= since)

        total = int((await self.db.execute(total_stmt)).scalar() or 0)
        cancelled = int((await self.db.execute(cancelled_stmt)).scalar() or 0)
        return {"total": total, "cancelled": cancelled}

    async def customer_count(self) -> int:
        """Total registered customer profiles."""
        return int(
            (await self.db.execute(
                select(func.count()).select_from(CustomerProfileModel)
            )).scalar() or 0
        )

    # ── Product & Inventory ───────────────────────────────────────────────

    async def product_stock_summary(self) -> Dict[str, int]:
        """Product count, low stock count, and out-of-stock count."""
        row = (
            await self.db.execute(
                select(
                    func.count(ProductModel.id),
                    func.sum(case((ProductModel.stock <= ProductModel.low_stock_threshold, 1), else_=0)),
                    func.sum(case((ProductModel.stock == 0, 1), else_=0)),
                ).select_from(ProductModel)
            )
        ).first() or (0, 0, 0)
        return {
            "product_count": int(row[0] or 0),
            "low_stock": int(row[1] or 0),
            "out_of_stock": int(row[2] or 0),
        }

    async def product_stock_summary_published(self) -> Dict[str, int]:
        """Same as product_stock_summary but only for published products."""
        row = (
            await self.db.execute(
                select(
                    func.count(ProductModel.id),
                    func.sum(case((ProductModel.stock <= ProductModel.low_stock_threshold, 1), else_=0)),
                    func.sum(case((ProductModel.stock == 0, 1), else_=0)),
                ).where(ProductModel.published.is_(True))
            )
        ).first() or (0, 0, 0)
        return {
            "product_count": int(row[0] or 0),
            "low_stock": int(row[1] or 0),
            "out_of_stock": int(row[2] or 0),
        }

    async def pending_review_count(self) -> int:
        """Products in draft/review states."""
        return int(
            (await self.db.execute(
                select(func.count()).select_from(ProductModel).where(
                    ProductModel.status.in_(["DRAFT", "PENDING_REVIEW", "IN_REVIEW"])
                )
            )).scalar() or 0
        )

    async def low_stock_rows(self, *, limit: int = 6, published_only: bool = False) -> List[Dict[str, Any]]:
        """Lowest-stock product rows for alert panels."""
        stmt = (
            select(ProductModel.id, ProductModel.product_id, ProductModel.name,
                   ProductModel.sku, ProductModel.stock, ProductModel.low_stock_threshold)
            .where(ProductModel.stock <= ProductModel.low_stock_threshold)
            .order_by(ProductModel.stock.asc())
            .limit(limit)
        )
        if published_only:
            stmt = stmt.where(ProductModel.published.is_(True))
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "id": row.id,
                "productId": row.product_id or row.id,
                "name": row.name,
                "sku": row.sku,
                "stock": int(row.stock or 0),
                "threshold": int(row.low_stock_threshold or 0),
                # AI assistant label/value format
                "label": row.name,
                "value": f"{int(row.stock or 0)} in stock (threshold {int(row.low_stock_threshold or 0)})",
            }
            for row in rows
        ]

    async def total_stock_units(self) -> int:
        """Total stock units across all products."""
        return int(
            (await self.db.execute(
                select(func.coalesce(func.sum(ProductModel.stock), 0))
            )).scalar() or 0
        )

    # ── Sales series ──────────────────────────────────────────────────────

    async def sales_series(
        self, *, since: datetime, use_date_trunc: bool = True
    ) -> List[Dict[str, Any]]:
        """Daily revenue + order count series for a given window."""
        day_expr = (
            func.date_trunc("day", OrderModel.created_at)
            if use_date_trunc
            else func.date(OrderModel.created_at)
        )
        rows = (
            await self.db.execute(
                select(
                    day_expr.label("day"),
                    func.sum(OrderModel.total).label("revenue"),
                    func.count(OrderModel.id).label("orders"),
                )
                .where(OrderModel.created_at >= since, OrderModel.status.in_(_REVENUE_STATUSES))
                .group_by("day")
                .order_by("day")
            )
        ).all()
        result = []
        for row in rows:
            day_val = row.day
            if day_val is None:
                continue
            day_key = day_val[:10] if isinstance(day_val, str) else (
                day_val.date().isoformat() if hasattr(day_val, 'date') else str(day_val)[:10]
            )
            result.append({
                "date": day_key,
                "revenue": int(row.revenue or 0),
                "orders": int(row.orders or 0),
            })
        return result

    # ── Category breakdown ────────────────────────────────────────────────

    async def category_revenue(
        self, *, since: datetime, limit: int = 8
    ) -> List[Dict[str, Any]]:
        """Revenue by product category for revenue-eligible orders."""
        rows = (
            await self.db.execute(
                select(
                    func.coalesce(func.nullif(ProductModel.category, ""), "unassigned").label("category"),
                    func.sum(OrderItemModel.line_total).label("revenue"),
                    func.sum(OrderItemModel.quantity).label("units"),
                )
                .select_from(OrderItemModel)
                .join(OrderModel, OrderModel.id == OrderItemModel.order_id)
                .join(ProductModel, ProductModel.id == OrderItemModel.product_id)
                .where(OrderModel.created_at >= since, OrderModel.status.in_(_REVENUE_STATUSES))
                .group_by("category")
                .order_by(desc("revenue"))
                .limit(limit)
            )
        ).all()
        return [
            {
                "name": str(row.category or "unassigned"),
                "label": str(row.category or "unassigned"),
                "revenue": int(row.revenue or 0),
                "units": int(row.units or 0),
            }
            for row in rows
        ]

    # ── Top products ──────────────────────────────────────────────────────

    async def top_products(
        self, *, since: Optional[datetime] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Top products by revenue from revenue-eligible order items."""
        stmt = (
            select(
                OrderItemModel.product_id.label("productId"),
                OrderItemModel.product_name.label("name"),
                OrderItemModel.product_image.label("image"),
                func.sum(OrderItemModel.quantity).label("units"),
                func.sum(OrderItemModel.line_total).label("revenue"),
            )
            .join(OrderModel, OrderModel.id == OrderItemModel.order_id)
            .where(OrderModel.status.in_(_REVENUE_STATUSES))
            .group_by(OrderItemModel.product_id, OrderItemModel.product_name, OrderItemModel.product_image)
            .order_by(desc("revenue"))
            .limit(limit)
        )
        if since:
            stmt = stmt.where(OrderModel.created_at >= since)
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "productId": row.productId,
                "name": row.name,
                "image": row.image,
                "units": int(row.units or 0),
                "revenue": int(row.revenue or 0),
                # AI assistant label/value format
                "label": row.name,
                "value": f"₹{int(row.revenue or 0):,} · {int(row.units or 0)} pcs",
            }
            for row in rows
        ]

    # ── Top customers ─────────────────────────────────────────────────────

    async def top_customers(
        self, *, since: Optional[datetime] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Top customers by spend from revenue-eligible orders."""
        stmt = (
            select(
                OrderModel.customer_id.label("customerId"),
                func.count(OrderModel.id).label("orders"),
                func.sum(OrderModel.total).label("spend"),
            )
            .where(OrderModel.customer_id.isnot(None), OrderModel.status.in_(_REVENUE_STATUSES))
            .group_by(OrderModel.customer_id)
            .order_by(desc("spend"))
            .limit(limit)
        )
        if since:
            stmt = stmt.where(OrderModel.created_at >= since)
        rows = (await self.db.execute(stmt)).all()

        items = []
        for row in rows:
            profile = (
                await self.db.execute(
                    select(CustomerProfileModel).where(CustomerProfileModel.user_id == row.customerId)
                )
            ).scalars().first()
            user = None
            if profile:
                user = (
                    await self.db.execute(
                        select(UserModel).where(UserModel.id == row.customerId)
                    )
                ).scalars().first()
            name = (
                f"{profile.first_name or ''} {profile.last_name or ''}".strip()
                if profile
                else (user.full_name if user else str(row.customerId)[:8])
            )
            items.append({
                "customerId": row.customerId,
                "name": name,
                "email": user.email if user else None,
                "orders": int(row.orders or 0),
                "spend": int(row.spend or 0),
                # AI assistant label/value format
                "label": name,
                "value": f"₹{int(row.spend or 0):,} · {int(row.orders or 0)} orders",
            })
        return items

    # ── Order status breakdown ────────────────────────────────────────────

    async def orders_by_status(self) -> List[Dict[str, Any]]:
        """Order counts grouped by status."""
        rows = (
            await self.db.execute(
                select(OrderModel.status, func.count(OrderModel.id))
                .group_by(OrderModel.status)
                .order_by(OrderModel.status)
            )
        ).all()
        return [{"status": row[0], "count": int(row[1])} for row in rows]

    async def fulfillment_pipeline(self) -> Dict[str, int]:
        """Order counts by status as a dict (for AI assistant)."""
        rows = (
            await self.db.execute(
                select(OrderModel.status, func.count()).group_by(OrderModel.status)
            )
        ).all()
        return {r[0]: int(r[1]) for r in rows}

    # ── Returns ───────────────────────────────────────────────────────────

    async def returns_summary(self, *, since: datetime) -> Dict[str, Any]:
        """Return requests, refunded value, and status breakdown for a window."""
        rows = (
            await self.db.execute(
                select(
                    ReturnOrderModel.status,
                    func.count(),
                    func.coalesce(func.sum(ReturnOrderModel.refund_amount), 0),
                )
                .where(ReturnOrderModel.created_at >= since)
                .group_by(ReturnOrderModel.status)
            )
        ).all()
        counts = {r[0]: int(r[1]) for r in rows}
        refunded_value = next((int(r[2]) for r in rows if r[0] == "REFUNDED"), 0)
        return {"counts": counts, "total": sum(counts.values()), "refunded": refunded_value}

    # ── Offers / Coupons ──────────────────────────────────────────────────

    async def offers_summary(self) -> Dict[str, int]:
        """Active coupon count and total lifetime redemptions."""
        from app.models.commerce.coupon import CouponModel

        active = int(
            (await self.db.execute(
                select(func.count()).select_from(CouponModel).where(CouponModel.is_active.is_(True))
            )).scalar() or 0
        )
        redemptions = int(
            (await self.db.execute(
                select(func.coalesce(func.sum(CouponModel.usage_count), 0))
            )).scalar() or 0
        )
        return {"active": active, "redemptions": redemptions}

    # ── Customers ─────────────────────────────────────────────────────────

    async def customer_counts(self) -> Dict[str, int]:
        """Total registered customer-type users."""
        total = int(
            (await self.db.execute(
                select(func.count()).select_from(UserModel).where(UserModel.user_type == "customer")
            )).scalar() or 0
        )
        return {"total": total}

    # ── Workforce ─────────────────────────────────────────────────────────

    async def employee_headcount(self) -> Dict[str, int]:
        """Employee total and active counts."""
        total = int(
            (await self.db.execute(
                select(func.count()).select_from(EmployeeProfileModel)
            )).scalar() or 0
        )
        active = int(
            (await self.db.execute(
                select(func.count())
                .select_from(EmployeeProfileModel)
                .join(UserModel, UserModel.id == EmployeeProfileModel.user_id)
                .where(UserModel.status == "ACTIVE")
            )).scalar() or 0
        )
        return {"total": total, "active": active}

    async def employee_user_count(self) -> int:
        """Employee-type user count (for AI assistant workforce topic)."""
        return int(
            (await self.db.execute(
                select(func.count()).select_from(UserModel).where(UserModel.user_type == "employee")
            )).scalar() or 0
        )

    # ── Period metrics (AI assistant compound helper) ──────────────────────

    async def period_metrics(self, *, since: datetime) -> Dict[str, int]:
        """Compound period metrics: orders, gross, revenue, cancelled, aov."""
        totals = (
            await self.db.execute(
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
        cancelled = int(
            (await self.db.execute(
                select(func.count()).select_from(OrderModel).where(
                    OrderModel.status == "CANCELLED", OrderModel.created_at >= since
                )
            )).scalar() or 0
        )
        orders = int(totals[1] or 0)
        revenue = int(totals[2] or 0)
        return {
            "orders": orders,
            "gross": int(totals[0] or 0),
            "revenue": revenue,
            "cancelled": cancelled,
            "aov": revenue // max(1, orders),
        }
