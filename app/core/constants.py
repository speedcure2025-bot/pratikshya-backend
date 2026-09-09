from enum import Enum


# ── Revenue accounting ────────────────────────────────────────────────────────
# Order statuses whose `total` counts towards revenue: everything except a
# wholesale cancellation/refund. Single source of truth shared by the admin
# analytics overview and the admin customer list/detail aggregates.
REVENUE_ORDER_STATUSES: tuple = (
    "PENDING_PAYMENT", "PAYMENT_CONFIRMED", "ORDER_CONFIRMED", "PROCESSING",
    "ALLOCATED", "PICKING", "PACKED", "READY_TO_DISPATCH", "SHIPPED",
    "OUT_FOR_DELIVERY", "DELIVERED", "COMPLETED", "PARTIALLY_RETURNED",
    "RETURN_REQUESTED", "RETURNED",
)



class UserType(str, Enum):
    CUSTOMER = "customer"
    EMPLOYEE = "employee"
    ADMIN = "admin"


class PredefinedRole(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    STORE_MANAGER = "STORE_MANAGER"
    SALES_EXECUTIVE = "SALES_EXECUTIVE"
    INVENTORY_MANAGER = "INVENTORY_MANAGER"
    INVENTORY_STAFF = "INVENTORY_STAFF"
    WAREHOUSE_STAFF = "WAREHOUSE_STAFF"
    CUSTOMER_SUPPORT = "CUSTOMER_SUPPORT"
    FASHION_STYLIST = "FASHION_STYLIST"


class ProductStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


class MediaStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    PROCESSING = "PROCESSING"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    RETURN_REQUESTED = "RETURN_REQUESTED"
    RETURNED = "RETURNED"


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class ReturnStatus(str, Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PICKED_UP = "PICKED_UP"
    RECEIVED = "RECEIVED"
    INSPECTED = "INSPECTED"
    REFUNDED = "REFUNDED"


class AttendanceStatus(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    HALF_DAY = "HALF_DAY"
    LEAVE = "LEAVE"
