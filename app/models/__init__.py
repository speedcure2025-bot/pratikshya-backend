"""
Models package — controls SQLAlchemy mapper registration order.

All models must be imported here before any mapper is configured.
Import from leaf (no deps) → core entities → junction tables → child/profile tables.
"""

# --- Auth ---
from app.models.auth.session import UserSessionModel                       # noqa: F401
from app.models.auth.user import UserModel                                 # noqa: F401
from app.models.auth.oauth_account import OAuthAccountModel                # noqa: F401
from app.models.auth.password_reset import PasswordResetModel              # noqa: F401
from app.models.auth.verification_token import VerificationTokenModel      # noqa: F401

# --- RBAC ---
from app.models.rbac.permission import PermissionModel                     # noqa: F401
from app.models.rbac.role import RoleModel                                 # noqa: F401
from app.models.rbac.role_permission import RolePermissionModel            # noqa: F401
from app.models.rbac.user_role import UserRoleModel                        # noqa: F401

# --- Profiles ---
from app.models.customer.customer import CustomerProfileModel              # noqa: F401
from app.models.customer.address import AddressModel                       # noqa: F401
from app.models.customer.preferences import CustomerPreferencesModel       # noqa: F401
from app.models.employee.department import DepartmentModel                 # noqa: F401
from app.models.employee.section import SectionModel                       # noqa: F401
from app.models.employee.employee import EmployeeProfileModel              # noqa: F401
from app.models.employee.attendance import AttendanceModel                 # noqa: F401
from app.models.employee.performance import PerformanceModel               # noqa: F401
from app.models.employee.target import TargetModel                         # noqa: F401

# --- Catalog ---
from app.models.catalog.category import CategoryModel, SubcategoryModel    # noqa: F401
from app.models.catalog.tag import TagModel                                # noqa: F401
from app.models.catalog.collection import CollectionModel                  # noqa: F401
from app.models.catalog.product import ProductModel                        # noqa: F401
from app.models.catalog.product_tag import ProductTagModel                 # noqa: F401

# --- Variants ---
from app.models.variants.attribute import AttributeModel                   # noqa: F401
from app.models.variants.attribute_value import AttributeValueModel        # noqa: F401
from app.models.variants.product_variant import ProductVariantModel        # noqa: F401
from app.models.variants.product_attribute import ProductAttributeModel    # noqa: F401

# --- Pricing ---
from app.models.pricing.tax_rate import TaxRateModel                       # noqa: F401
from app.models.pricing.product_price import ProductPriceModel             # noqa: F401
from app.models.pricing.price_history import PriceHistoryModel             # noqa: F401

# --- Inventory ---
from app.models.inventory.warehouse import WarehouseModel                  # noqa: F401
from app.models.inventory.inventory_location import InventoryLocationModel # noqa: F401
from app.models.inventory.inventory_stock import InventoryStockModel       # noqa: F401
from app.models.inventory.inventory_movement import InventoryMovementModel # noqa: F401
from app.models.inventory.stock_reservation import StockReservationModel   # noqa: F401
from app.models.inventory.stock_transfer import StockTransferModel         # noqa: F401

# --- Media ---
from app.models.media.media_asset import MediaAssetModel                   # noqa: F401
from app.models.media.product_media import ProductMediaModel               # noqa: F401
from app.models.media.media_review import MediaReviewModel                 # noqa: F401
from app.models.media.marketing_media import MarketingMediaModel           # noqa: F401

# --- Commerce ---
from app.models.commerce.cart import CartModel                             # noqa: F401
from app.models.commerce.cart_item import CartItemModel                    # noqa: F401
from app.models.commerce.wishlist import WishlistModel                     # noqa: F401
from app.models.commerce.wishlist_item import WishlistItemModel            # noqa: F401
from app.models.commerce.coupon import CouponModel                         # noqa: F401
from app.models.commerce.coupon_redemption import CouponRedemptionModel    # noqa: F401

# --- Orders ---
from app.models.orders.order import OrderModel                             # noqa: F401
from app.models.orders.order_item import OrderItemModel                    # noqa: F401
from app.models.orders.order_status_history import OrderStatusHistoryModel # noqa: F401
from app.models.orders.return_order import ReturnOrderModel                # noqa: F401
from app.models.orders.return_item import ReturnItemModel                  # noqa: F401

# --- Payments (Razorpay) ---
from app.models.payments.payment_session import PaymentSessionModel        # noqa: F401

# --- Checkout ---
from app.models.checkout.checkout import CheckoutModel                     # noqa: F401
from app.models.checkout.payment import PaymentModel                       # noqa: F401
from app.models.checkout.payment_transaction import PaymentTransactionModel # noqa: F401

# --- Notification ---
from app.models.notification.notification import NotificationModel         # noqa: F401

# --- Admin settings ---
from app.models.admin.setting import SettingModel                          # noqa: F401

# --- Audit ---
from app.models.audit.activity_log import ActivityLogModel                 # noqa: F401

# --- Chatbot ---
from app.models.chatbot.knowledge_document import KnowledgeDocumentModel   # noqa: F401
from app.models.chatbot.knowledge_chunk import KnowledgeChunkModel         # noqa: F401
from app.models.chatbot.conversation import ConversationModel              # noqa: F401
from app.models.chatbot.message import MessageModel                        # noqa: F401
from app.models.chatbot.chat_retrieval import ChatRetrievalModel           # noqa: F401
