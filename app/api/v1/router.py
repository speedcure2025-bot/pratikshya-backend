from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.users import router as users_router
from app.api.v1.roles import router as roles_router
from app.api.v1.permissions import router as permissions_router
from app.api.v1.products import router as products_router
from app.api.v1.categories import router as categories_router
from app.api.v1.collections import router as collections_router
from app.api.v1.variants import router as variants_router
from app.api.v1.attributes import router as attributes_router
from app.api.v1.pricing import router as pricing_router
from app.api.v1.media import router as media_router
from app.api.v1.media_reviews import router as media_reviews_router
from app.api.v1.customers import router as customers_router
from app.api.v1.addresses import router as addresses_router
from app.api.v1.cart import router as cart_router
from app.api.v1.wishlist import router as wishlist_router
from app.api.v1.coupons import router as coupons_router  # handles /offers + /admin/offers
from app.api.v1.checkout import router as checkout_router
from app.api.v1.payments import router as payments_router
from app.api.v1.orders import router as orders_router
from app.api.v1.returns import router as returns_router
from app.api.v1.inventory import router as inventory_router
from app.api.v1.warehouses import router as warehouses_router
from app.api.v1.stock_transfers import router as stock_transfers_router
from app.api.v1.employees import router as employees_router
from app.api.v1.attendance import router as attendance_router
from app.api.v1.performance import router as performance_router
from app.api.v1.leave import router as leave_router
from app.api.v1.admin import router as admin_router
from app.api.v1.audit import router as audit_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.chatbot import router as chatbot_router
from app.api.v1.search import router as search_router
from app.api.v1.explore import router as explore_router
from app.api.v1.marketing_media import router as marketing_media_router

api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(roles_router)
api_router.include_router(permissions_router)
api_router.include_router(products_router)
api_router.include_router(categories_router)
api_router.include_router(collections_router)
api_router.include_router(variants_router)
api_router.include_router(attributes_router)
api_router.include_router(pricing_router)
api_router.include_router(media_router)
api_router.include_router(media_reviews_router)
api_router.include_router(customers_router)
api_router.include_router(addresses_router)
api_router.include_router(cart_router)
api_router.include_router(wishlist_router)
api_router.include_router(coupons_router)
api_router.include_router(checkout_router)
api_router.include_router(payments_router)
api_router.include_router(orders_router)
api_router.include_router(returns_router)
api_router.include_router(inventory_router)
api_router.include_router(warehouses_router)
api_router.include_router(stock_transfers_router)
api_router.include_router(employees_router)
api_router.include_router(attendance_router)
api_router.include_router(performance_router)
api_router.include_router(leave_router)
api_router.include_router(admin_router)
api_router.include_router(audit_router)
api_router.include_router(analytics_router)
api_router.include_router(chatbot_router)
api_router.include_router(search_router)
api_router.include_router(explore_router)
api_router.include_router(marketing_media_router)

from app.api.v1.recommendations import router as recommendations_router
api_router.include_router(recommendations_router)
