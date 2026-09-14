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

All queries are delegated to the shared AnalyticsService (single source of truth).
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_admin, get_db, require_admin_permission
from app.models.auth.user import UserModel
from app.services.analytics.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["Analytics & Reporting"])


@router.get("/health", summary="Module health check")
async def health_check():
    return {"module": "analytics", "status": "active"}


@router.get("/overview", summary="Headline dashboard metrics (admin)")
async def analytics_overview(
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    svc = AnalyticsService(db)

    total_revenue = await svc.revenue_total()
    counts = await svc.order_counts()
    customer_count = await svc.customer_count()
    stock = await svc.product_stock_summary()
    pending_review = await svc.pending_review_count()
    avg_order_value = round(float(total_revenue) / counts["total"], 2) if counts["total"] else 0

    return {
        "totalRevenue":       total_revenue,
        "orderCount":         counts["total"],
        "customerCount":      customer_count,
        "productCount":       stock["product_count"],
        "avgOrderValue":      avg_order_value,
        "lowStockCount":      stock["low_stock"],
        "pendingReviewCount": pending_review,
        "cancelledCount":     counts["cancelled"],
    }


@router.get("/sales", summary="Revenue + order series (admin)")
async def analytics_sales(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    series = await AnalyticsService(db).sales_series(since=since, use_date_trunc=True)
    return {"days": days, "series": series}


@router.get("/products", summary="Top products (admin)")
async def analytics_top_products(
    limit: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    items = await AnalyticsService(db).top_products(limit=limit)
    # Return only the fields the analytics endpoint uses
    return {
        "items": [
            {
                "productId": row["productId"],
                "name":      row["name"],
                "image":     row["image"],
                "units":     row["units"],
                "revenue":   row["revenue"],
            }
            for row in items
        ],
    }


@router.get("/customers", summary="Top customers by spend (admin)")
async def analytics_top_customers(
    limit: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    items = await AnalyticsService(db).top_customers(limit=limit)
    return {
        "items": [
            {
                "customerId": row["customerId"],
                "name":       row["name"],
                "email":      row["email"],
                "orders":     row["orders"],
                "spend":      row["spend"],
            }
            for row in items
        ],
    }


@router.get("/orders", summary="Orders grouped by status (admin)")
async def analytics_orders(
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    items = await AnalyticsService(db).orders_by_status()
    return {"items": items}


@router.get("/inventory-summary", summary="Inventory aggregates from catalog products (admin)")
async def analytics_inventory_summary(
    db: AsyncSession = Depends(get_db),
    _admin: UserModel = Depends(get_current_admin),
):
    await require_admin_permission(_admin, db, "analytics.view")
    stock = await AnalyticsService(db).product_stock_summary()
    total_units = await AnalyticsService(db).total_stock_units()
    return {
        "productCount":    stock["product_count"],
        "totalUnits":      total_units,
        "lowStockCount":   stock["low_stock"],
        "outOfStockCount": stock["out_of_stock"],
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
    svc = AnalyticsService(db)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ── 1. Headline metrics ──
    total_revenue = await svc.revenue_total()
    counts = await svc.order_counts()
    customer_count = await svc.customer_count()
    stock = await svc.product_stock_summary()
    pending_review = await svc.pending_review_count()
    avg_order_value = round(float(total_revenue) / counts["total"], 2) if counts["total"] else 0

    metrics = {
        "totalRevenue":       total_revenue,
        "orderCount":         counts["total"],
        "customerCount":      customer_count,
        "productCount":       stock["product_count"],
        "avgOrderValue":      avg_order_value,
        "lowStockCount":      stock["low_stock"],
        "outOfStockCount":    stock["out_of_stock"],
        "pendingReviewCount": pending_review,
        "cancelledCount":     counts["cancelled"],
    }

    # ── 2. Sales series (bounded window) ──
    # Use func.date for the dashboard (portable across PostgreSQL and SQLite)
    sales_series = await svc.sales_series(since=since, use_date_trunc=False)

    # ── 3. Revenue by product category ──
    categories = await svc.category_revenue(since=since)
    # Return only the dashboard format
    categories_formatted = [
        {"name": row["name"], "revenue": row["revenue"], "units": row["units"]}
        for row in categories
    ]

    # ── 4. Recent orders ──
    from app.schemas.orders.order import AdminOrderResponse
    from app.services.orders.order_service import OrderService

    orders_result = await OrderService(db).admin_list_orders(page=1, page_size=recent_limit)
    recent_orders = [
        AdminOrderResponse.model_validate(order).model_dump(by_alias=True)
        for order in orders_result["orders"]
    ]

    # ── 5. Stock summary + lowest rows for alerts panel ──
    low_items = await svc.low_stock_rows(limit=6)
    inventory_summary = {
        "productCount":    stock["product_count"],
        "lowStockCount":   stock["low_stock"],
        "outOfStockCount": stock["out_of_stock"],
        "note": "Aggregated from catalog_product stock fields; dedicated inventory "
                "tables do not yet carry business columns in the existing schema.",
        "items": [
            {
                "id": row["id"],
                "productId": row["productId"],
                "name": row["name"],
                "sku": row["sku"],
                "stock": row["stock"],
            }
            for row in low_items
        ],
    }

    # ── 6. Employee head-count ──
    employees = await svc.employee_headcount()

    return {
        "ok": True,
        "metrics": metrics,
        "salesSeries": sales_series,
        "salesDays": days,
        "categories": categories_formatted,
        "recentOrders": recent_orders,
        "inventorySummary": inventory_summary,
        "employees": employees,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
