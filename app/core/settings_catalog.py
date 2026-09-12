"""
Settings catalogue — ONE source for admin settings sections and defaults.

Extracted verbatim from `app/api/v1/admin.py` during the hardening pass so the
workforce services (attendance punch rules read `attendance` + `holidays`)
resolve against the SAME defaults the settings surface serves — the mirror
`frontend/src/config/attendanceConfig.js` documents these numbers too.
`admin.py` keeps thin aliases (`_merge_defaults`) for its existing callers.
"""

from typing import Any, Dict
import copy

from app.services.media.media_validation import allowed_image_extensions

KNOWN_SECTIONS = {
    "business", "store", "locations", "hours", "attendance", "holidays",
    "tax", "shipping", "payments", "orders", "returns", "inventory",
    "employees", "notifications", "customer", "offers", "media",
}

SETTINGS_DEFAULTS: Dict[str, Any] = {
    "business": {
        "name": "Pratikshya Fashon",
        "email": "",
        "phone": "",
        "gst": "",
        "address": "",
    },
    "store": {
        "currency": "INR",
        "timezone": "Asia/Kolkata",
        "locale": "en-IN",
    },
    "locations": {},
    "hours": {
        "open": "09:00",
        "close": "21:00",
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    },
    "attendance": {
        "startTime": "09:30",
        "endTime": "18:30",
        "lateThresholdMinutes": 10,
        "minimumHalfDayMinutes": 240,
        "fullDayMinutes": 540,
    },
    "holidays": {"list": []},
    "tax": {
        "mode": "INCLUSIVE",
        "defaultRate": 0,
    },
    "shipping": {
        "freeShippingThreshold": 5000,
        "flatShippingFee": 99,
        "expressFee": 199,
        "codFee": 49,
    },
    "payments": {
        "methods": ["upi", "card", "netbanking", "cod"],
        "refundMethod": "Original payment method",
        "refundSla": "5-7 business days",
        "partialRefundEnabled": True,
    },
    "orders": {
        "autoConfirm": True,
        "cancellableStatuses": [
            "PENDING_PAYMENT", "PLACED", "PAYMENT_CONFIRMED",
            "ORDER_CONFIRMED", "CONFIRMED", "PROCESSING", "ALLOCATED", "PICKING",
        ],
    },
    "returns": {
        "returnWindowDays": 7,
        "returnMethods": ["HOME_PICKUP", "STORE_DROP"],
    },
    "inventory": {
        "lowStockThreshold": 5,
        "trackStock": True,
    },
    "employees": {
        "minimumPasswordLength": 8,
        "requireUppercase": True,
        "requireLowercase": True,
        "requireNumber": True,
        "requireSpecialCharacter": False,
        "passwordExpiryDays": 30,
    },
    "notifications": {
        "order": ["IN_APP"],
        "returns": ["IN_APP"],
        "employee": ["IN_APP"],
        "lowStock": ["IN_APP"],
        "offers": ["IN_APP"],
        "marketing": [],
    },
    "customer": {
        "allowGuestOrders": True,
        "autoLoyaltyPoints": True,
    },
    "offers": {
        "defaultDurationDays": 7,
        "maximumCouponDiscount": 10000,
        "defaultCustomerUsageLimit": 1,
        "allowStacking": False,
    },
    "media": {
        "maxImageSizeMb": 10,
        "maxVideoSizeMb": 100,
        # DERIVED from the single house image policy the upload validator and
        # the migration tool enforce (settings.ALLOWED_IMAGE_TYPES mapped
        # through app.storage.signatures) — never a second hand-maintained
        # list. This keeps the settings desk from advertising a format the
        # backend would reject (or omitting one it accepts): .avif and .webp
        # are part of the policy because the real product asset library is
        # AVIF-first (228 of the 238 shipped assets carry a .avif name).
        "allowedImageTypes": [ext.lstrip(".") for ext in allowed_image_extensions()],
        "allowedVideoTypes": ["mp4", "webm"],
    },
}


def merge_defaults(section: str, stored: dict) -> dict:
    """Deep-merge stored values on top of defaults."""
    defaults = copy.deepcopy(SETTINGS_DEFAULTS.get(section, {}))
    if stored:
        def _deep_merge(base: dict, override: dict) -> dict:
            result = dict(base)
            for k, v in override.items():
                if isinstance(v, dict) and isinstance(result.get(k), dict):
                    result[k] = _deep_merge(result[k], v)
                else:
                    result[k] = v
            return result
        return _deep_merge(defaults, stored)
    return defaults
