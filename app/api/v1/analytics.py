"""
Analytics — API router.

Read-only aggregates over the existing orders / order items / products /
customers tables. No schema changes; no hardcoded numbers.

  GET /analytics/overview            → headline metrics
  GET /analytics/sales?days=30       → revenue + order series by day
  GET /analytics/products?limit=10   → top products by units / revenue
  GET /analytics/customers?limit=10  → top customers by spend
  GET /analytics/orders              → orders by status
  GET /analytics/inventory-summary   → product stock aggregates (from catalog_product)
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_admin, get_db, require_admin_permission
from app.models.auth.user import UserModel
from app.models.catalog.product import ProductModel
from app.models.customer.customer import CustomerProfileModel
from app.models.employee.employee import EmployeeProfileModel
from app.models.orders.order import OrderModel
from app.models.orders.order_item import OrderItemModel

router = APIRouter(prefix="/analytics", tags=["Analytics & Reporting"])

# Revenue counting only orders that were not cancelled / refunded wholesale.
# Canonical definition: app.core.constants.REVENUE_ORDER_STATUSES.
from app.core.constants import REVENUE_ORDER_STATUSES as _REVENUE_STATUSES


def _iso_day(dt: datetime) -> str:
    return dt.date().isoformat()


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "analytics", "status": "active"}


@router.get("/overview", summary="Headline dashboard metrics (admin)")
async def analytics_overview(
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    total_revenue = (
        await db.execute(
            select(func.coalesce(func.sum(OrderModel.total), 0)).where(
                OrderModel.status.in_(_REVENUE_STATUSES)
            )
        )
    ).scalar() or 0
    order_count = (
        await db.execute(select(func.count()).select_from(OrderModel))
    ).scalar() or 0
    customer_count = (
        await db.execute(select(func.count()).select_from(CustomerProfileModel))
    ).scalar() or 0
    products = (
        await db.execute(
            select(func.count(), func.sum(case((ProductModel.stock <= ProductModel.low_stock_threshold, 1), else_=0)))
            .select_from(ProductModel)
        )
    ).first() or (0, 0)
    product_count, low_stock_count = int(products[0] or 0), int(products[1] or 0)

    pending_review = (
        await db.execute(
            select(func.count()).select_from(ProductModel).where(
                ProductModel.status.in_(["DRAFT", "PENDING_REVIEW", "IN_REVIEW"])
            )
        )
    ).scalar() or 0

    cancelled_count = (
        await db.execute(
            select(func.count()).select_from(OrderModel).where(OrderModel.status == "CANCELLED")
        )
    ).scalar() or 0

    avg_order_value = round(float(total_revenue) / order_count, 2) if order_count else 0

    return {
        "totalRevenue":       int(total_revenue),
        "orderCount":         int(order_count),
        "customerCount":      int(customer_count),
        "productCount":       product_count,
        "avgOrderValue":      avg_order_value,
        "lowStockCount":      low_stock_count,
        "pendingReviewCount": pending_review,
        "cancelledCount":     cancelled_count,
    }


@router.get("/sales", summary="Revenue + order series (admin)")
async def analytics_sales(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            select(
                func.date_trunc("day", OrderModel.created_at).label("day"),
                func.sum(OrderModel.total).label("revenue"),
                func.count(OrderModel.id).label("orders"),
            )
            .where(OrderModel.created_at >= since, OrderModel.status.in_(_REVENUE_STATUSES))
            .group_by("day")
            .order_by("day")
        )
    ).all()
    return {
        "days":    days,
        "series": [
            {"date": row.day.date().isoformat(), "revenue": int(row.revenue or 0), "orders": int(row.orders or 0)}
            for row in rows
        ],
    }


@router.get("/products", summary="Top products (admin)")
async def analytics_top_products(
    limit: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    rows = (
        await db.execute(
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
    ).all()
    return {
        "items": [
            {
                "productId": row.productId,
                "name":     row.name,
                "image":    row.image,
                "units":    int(row.units or 0),
                "revenue":  int(row.revenue or 0),
            }
            for row in rows
        ],
    }


@router.get("/customers", summary="Top customers by spend (admin)")
async def analytics_top_customers(
    limit: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    rows = (
        await db.execute(
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
    ).all()

    items = []
    for row in rows:
        profile = (
            await db.execute(
                select(CustomerProfileModel).where(CustomerProfileModel.user_id == row.customerId)
            )
        ).scalars().first()
        user = None
        if profile:
            user = (
                await db.execute(
                    select(UserModel).where(UserModel.id == row.customerId)
                )
            ).scalars().first()
        items.append({
            "customerId": row.customerId,
            "name":       f"{profile.first_name or ''} {profile.last_name or ''}".strip() or (user.full_name if user else ""),
            "email":      user.email if user else None,
            "orders":     int(row.orders or 0),
            "spend":      int(row.spend or 0),
        })
    return {"items": items}


@router.get("/orders", summary="Orders grouped by status (admin)")
async def analytics_orders(
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    rows = (
        await db.execute(
            select(OrderModel.status, func.count(OrderModel.id))
            .group_by(OrderModel.status)
            .order_by(OrderModel.status)
        )
    ).all()
    return {"items": [{"status": row[0], "count": int(row[1])} for row in rows]}


@router.get("/inventory-summary", summary="Inventory aggregates from catalog products (admin)")
async def analytics_inventory_summary(
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    totals = (
        await db.execute(
            select(
                func.count(ProductModel.id),
                func.sum(ProductModel.stock),
                func.sum(case((ProductModel.stock <= ProductModel.low_stock_threshold, 1), else_=0)),
                func.sum(case((ProductModel.stock == 0, 1), else_=0)),
            ).select_from(ProductModel)
        )
    ).first() or (0, 0, 0, 0)
    return {
        "productCount":  int(totals[0] or 0),
        "totalUnits":    int(totals[1] or 0),
        "lowStockCount": int(totals[2] or 0),
        "outOfStockCount": int(totals[3] or 0),
        "note": "Aggregated from catalog_product stock fields; dedicated inventory "
                "tables do not yet carry business columns in the existing schema.",
    }


# ===========================================================================
# ADMIN DASHBOARD — consolidated summary
# ===========================================================================

@router.get(
    "/admin/dashboard/summary",
    summary="One consolidated admin-dashboard read (admin)",
    description=(
        "Single request feeding the Admin dashboard: headline metrics, the "
        "7-day sales series, revenue by product category, recent orders and "
        "the stock summary. Every figure is a bounded database aggregate — "
        "the dashboard no longer fans out into 7+ independent requests and "
        "no longer re-reads the employee list twice.  \n"
        "Authorization: `analytics.view`."
    ),
)
async def admin_dashboard_summary(
    days: int = Query(default=7, ge=1, le=90),
    recent_limit: int = Query(default=5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    current_user: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(current_user, db, "analytics.view")
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ── 1. Headline metrics (same definitions as GET /analytics/overview,
    #      with out-of-stock resolved in the SAME product aggregate). ──
    total_revenue = (
        await db.execute(
            select(func.coalesce(func.sum(OrderModel.total), 0)).where(
                OrderModel.status.in_(_REVENUE_STATUSES)
            )
        )
    ).scalar() or 0
    order_count = (
        await db.execute(select(func.count()).select_from(OrderModel))
    ).scalar() or 0
    customer_count = (
        await db.execute(select(func.count()).select_from(CustomerProfileModel))
    ).scalar() or 0
    product_row = (
        await db.execute(
            select(
                func.count(ProductModel.id),
                func.sum(case((ProductModel.stock <= ProductModel.low_stock_threshold, 1), else_=0)),
                func.sum(case((ProductModel.stock == 0, 1), else_=0)),
            ).select_from(ProductModel)
        )
    ).first() or (0, 0, 0)
    product_count = int(product_row[0] or 0)
    low_stock_count = int(product_row[1] or 0)
    out_of_stock_count = int(product_row[2] or 0)
    pending_review = (
        await db.execute(
            select(func.count()).select_from(ProductModel).where(
                ProductModel.status.in_(["DRAFT", "PENDING_REVIEW", "IN_REVIEW"])
            )
        )
    ).scalar() or 0
    cancelled_count = (
        await db.execute(
            select(func.count()).select_from(OrderModel).where(OrderModel.status == "CANCELLED")
        )
    ).scalar() or 0
    avg_order_value = round(float(total_revenue) / order_count, 2) if order_count else 0

    metrics = {
        "totalRevenue": int(total_revenue),
        "orderCount": int(order_count),
        "customerCount": int(customer_count),
        "productCount": product_count,
        "avgOrderValue": avg_order_value,
        "lowStockCount": low_stock_count,
        "outOfStockCount": out_of_stock_count,
        "pendingReviewCount": pending_review,
        "cancelledCount": cancelled_count,
    }

    # ── 2. Sales series (bounded window, one group-by). ──
    # func.date() is portable across PostgreSQL and the SQLite test harness
    # (the public /analytics/sales endpoint keeps its date_trunc form).
    series_rows = (
        await db.execute(
            select(
                func.date(OrderModel.created_at).label("day"),
                func.sum(OrderModel.total).label("revenue"),
                func.count(OrderModel.id).label("orders"),
            )
            .where(OrderModel.created_at >= since, OrderModel.status.in_(_REVENUE_STATUSES))
            .group_by("day")
            .order_by("day")
        )
    ).all()

    def _day_key(value):
        if value is None:
            return None
        if isinstance(value, str):
            return value[:10]
        return value.isoformat()

    sales_series = [
        {"date": _day_key(row.day), "revenue": int(row.revenue or 0), "orders": int(row.orders or 0)}
        for row in series_rows
        if _day_key(row.day)
    ]

    # ── 3. Revenue by product category (true catalogue category join). ──
    # The dashboard previously regrouped a top-100-products payload in the
    # browser by id prefix — a single degenerate bucket under the PF- id
    # scheme. Categories are now one bounded server aggregate.
    category_rows = (
        await db.execute(
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
            .limit(8)
        )
    ).all()
    categories = [
        {"name": row.category or "unassigned", "revenue": int(row.revenue or 0), "units": int(row.units or 0)}
        for row in category_rows
    ]

    # ── 4. Recent orders (bounded page through the existing order service,
    #      projected by the SAME AdminOrderResponse schema as GET /admin/orders). ──
    from app.schemas.orders.order import AdminOrderResponse
    from app.services.orders.order_service import OrderService

    orders_result = await OrderService(db).admin_list_orders(page=1, page_size=recent_limit)
    recent_orders = [
        AdminOrderResponse.model_validate(order).model_dump(by_alias=True)
        for order in orders_result["orders"]
    ]

    # ── 5. Stock summary + the few lowest rows for the alerts panel. ──
    inventory_summary = {
        "productCount": product_count,
        "lowStockCount": low_stock_count,
        "outOfStockCount": out_of_stock_count,
        "note": "Aggregated from catalog_product stock fields; dedicated inventory "
                "tables do not yet carry business columns in the existing schema.",
    }
    low_stock_items_rows = (
        await db.execute(
            select(ProductModel.id, ProductModel.product_id, ProductModel.name, ProductModel.sku, ProductModel.stock)
            .where(ProductModel.stock <= ProductModel.low_stock_threshold)
            .order_by(ProductModel.stock.asc())
            .limit(6)
        )
    ).all()
    inventory_summary["items"] = [
        {
            "id": row.id,
            "productId": row.product_id or row.id,
            "name": row.name,
            "sku": row.sku,
            "stock": int(row.stock or 0),
        }
        for row in low_stock_items_rows
    ]

    # ── 6. Employee head-count (two COUNTs, not a 100-row list read). ──
    employee_total = (
        await db.execute(select(func.count()).select_from(EmployeeProfileModel))
    ).scalar() or 0
    employee_active = (
        await db.execute(
            select(func.count())
            .select_from(EmployeeProfileModel)
            .join(UserModel, UserModel.id == EmployeeProfileModel.user_id)
            .where(UserModel.status == "ACTIVE")
        )
    ).scalar() or 0

    return {
        "ok": True,
        "metrics": metrics,
        "salesSeries": sales_series,
        "salesDays": days,
        "categories": categories,
        "recentOrders": recent_orders,
        "inventorySummary": inventory_summary,
        "employees": {"total": int(employee_total), "active": int(employee_active)},
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
