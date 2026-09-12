import json
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BACKEND_DIR / "docs"
SCHEMA_DOCS_DIR = DOCS_DIR / "schema"

DOCS_DIR.mkdir(exist_ok=True)
SCHEMA_DOCS_DIR.mkdir(exist_ok=True)

with open(BACKEND_DIR / "schema_dump.json", "r", encoding="utf-8") as f:
    schema_dump = json.load(f)

# Module table mappings
MODULE_MAPPINGS = {
    "01_auth_rbac": {
        "title": "Auth & RBAC Module",
        "description": "User authentication, surface management, sessions, social OAuth accounts, roles, permissions, and user-role assignments.",
        "tables": [
            "users",
            "user_sessions",
            "oauth_accounts",
            "roles",
            "permissions",
            "role_permissions",
            "user_roles",
            "auth_password_reset",
            "auth_verification_token",
        ]
    },
    "02_catalog_variants": {
        "title": "Catalog, Variants & Pricing Module",
        "description": "Categories, subcategories, product collections, products, tags, variant attributes, values, pricing, price history, and tax rates.",
        "tables": [
            "catalog_category",
            "catalog_subcategory",
            "catalog_collection",
            "catalog_product",
            "catalog_tag",
            "catalog_product_tag",
            "variants_attribute",
            "variants_attribute_value",
            "variants_product_attribute",
            "variants_product_variant",
            "pricing_product_price",
            "pricing_price_history",
            "pricing_tax_rate",
        ]
    },
    "03_customer_commerce": {
        "title": "Customer & Commerce Module",
        "description": "Customer profiles, addresses, preference tracking, active shopping carts, cart items, customer wishlists, wishlist items, and promotional coupons.",
        "tables": [
            "customer_profiles",
            "customer_address",
            "customer_preferences",
            "commerce_cart",
            "commerce_cart_item",
            "commerce_wishlist",
            "commerce_wishlist_item",
            "commerce_coupon",
            "commerce_coupon_redemption",
        ]
    },
    "04_orders_checkout": {
        "title": "Orders, Returns & Checkout Module",
        "description": "Checkout processing, order placement, line items, status audit trail, return orders, return line items, payment sessions, and payment transaction logs.",
        "tables": [
            "checkout_checkout",
            "orders_order",
            "orders_order_item",
            "orders_order_status_history",
            "orders_return_order",
            "orders_return_item",
            "checkout_payment",
            "checkout_payment_transaction",
            "payment_sessions",
        ]
    },
    "05_inventory_warehouses": {
        "title": "Inventory & Warehouse Module",
        "description": "Warehouse definitions, stock levels across locations/SKUs, inventory movement logs, order/cart stock reservations, and inter-warehouse stock transfers.",
        "tables": [
            "inventory_warehouse",
            "inventory_inventory_location",
            "inventory_inventory_stock",
            "inventory_inventory_movement",
            "inventory_stock_reservation",
            "inventory_stock_transfer",
        ]
    },
    "06_media_marketing": {
        "title": "Media & Marketing Module",
        "description": "Durable media asset storage registry, product gallery/cover media mappings, homepage hero/marketing banners, and customer media reviews.",
        "tables": [
            "media_media_asset",
            "media_product_media",
            "media_marketing_media",
            "media_media_review",
        ]
    },
    "07_employee_support": {
        "title": "Employee & Operations Module",
        "description": "Employee operational profiles, departments, sections, attendance logs, targets, performance reviews, customer support cases, stylist assignments, and custom outfit canvases.",
        "tables": [
            "employee_profiles",
            "employee_department",
            "employee_section",
            "employee_attendance",
            "employee_target",
            "employee_performance",
        ]
    },
    "08_ai_chatbot": {
        "title": "AI Chatbot, System Audit & Configuration Module",
        "description": "AI chatbot conversation threads, message histories, knowledge document vectors, chunk retrievals, admin system settings, and global audit activity logs.",
        "tables": [
            "chatbot_conversation",
            "chatbot_message",
            "chatbot_knowledge_document",
            "chatbot_knowledge_chunk",
            "chatbot_chat_retrieval",
            "admin_setting",
            "audit_activity_log",
            "notification_notification",
            "pratikshya_alembic_version",
        ]
    }
}

def generate_table_markdown(table_name: str, cols: list) -> str:
    md = f"### `pratikshya.{table_name}`\n\n"
    md += "| Column Name | Data Type | Nullable | Default | Constraints / Relationships |\n"
    md += "| :--- | :--- | :--- | :--- | :--- |\n"
    
    for c in cols:
        name = f"**`{c['name']}`**" if c['is_pk'] else f"`{c['name']}`"
        dtype = f"`{c['type']}`"
        nullable = "Yes" if c['nullable'] else "**No**"
        default = f"`{c['default']}`" if c['default'] else "-"
        
        extra = []
        if c['is_pk']:
            extra.append("🔑 `PRIMARY KEY`")
        if c['fk']:
            fk = c['fk']
            extra.append(f"🔗 `FK -> {fk['to_table']}.{fk['to_column']}` (ON DELETE {fk['on_delete']})")
        
        constraints = ", ".join(extra) if extra else "-"
        md += f"| {name} | {dtype} | {nullable} | {default} | {constraints} |\n"
    
    md += "\n"
    return md

# 1. Generate Domain Files
for mod_key, mod_info in MODULE_MAPPINGS.items():
    file_path = SCHEMA_DOCS_DIR / f"{mod_key}.md"
    content = f"# {mod_info['title']}\n\n"
    content += f"{mod_info['description']}\n\n"
    content += f"**Total Tables in Module**: `{len(mod_info['tables'])}`\n\n"
    content += "---\n\n"
    
    for table_name in mod_info['tables']:
        if table_name in schema_dump:
            content += generate_table_markdown(table_name, schema_dump[table_name])
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Generated: {file_path}")

# 2. Generate Master Index DATABASE_SCHEMA.md
master_md = """# Pratikshya Fashon Database Schema Documentation

Welcome to the authoritative database schema documentation for **Pratikshya Fashon Backend**.

All database objects live in the dedicated PostgreSQL schema: **`pratikshya`**.
The database contains **65 tables** categorized into 8 core business modules.

---

## 📐 High-Level Architecture & Domain Modules

| Module # | Domain | Table Count | Description | Documentation Link |
| :--- | :--- | :--- | :--- | :--- |
| **01** | **Auth & RBAC** | 9 | User identity, surface accounts, JWT sessions, OAuth, roles, permissions | [01_auth_rbac.md](file:///d:/Medixo/New_Pratikshya/Backend/pratikshya-backend/docs/schema/01_auth_rbac.md) |
| **02** | **Catalog, Variants & Pricing** | 13 | Categories, collections, products, attributes, variants, pricing & tax | [02_catalog_variants.md](file:///d:/Medixo/New_Pratikshya/Backend/pratikshya-backend/docs/schema/02_catalog_variants.md) |
| **03** | **Customer & Commerce** | 9 | Profiles, addresses, cart items, wishlists, preferences & coupons | [03_customer_commerce.md](file:///d:/Medixo/New_Pratikshya/Backend/pratikshya-backend/docs/schema/03_customer_commerce.md) |
| **04** | **Orders, Returns & Checkout** | 9 | Checkouts, orders, line items, status history, return requests & payments | [04_orders_checkout.md](file:///d:/Medixo/New_Pratikshya/Backend/pratikshya-backend/docs/schema/04_orders_checkout.md) |
| **05** | **Inventory & Warehouses** | 6 | Warehouses, stock levels, stock movements, reservations & transfers | [05_inventory_warehouses.md](file:///d:/Medixo/New_Pratikshya/Backend/pratikshya-backend/docs/schema/05_inventory_warehouses.md) |
| **06** | **Media & Marketing** | 4 | Object storage registry, product media galleries, marketing hero & reviews | [06_media_marketing.md](file:///d:/Medixo/New_Pratikshya/Backend/pratikshya-backend/docs/schema/06_media_marketing.md) |
| **07** | **Employee & Operations** | 6 | Operational staff profiles, departments, attendance, targets & performance | [07_employee_support.md](file:///d:/Medixo/New_Pratikshya/Backend/pratikshya-backend/docs/schema/07_employee_support.md) |
| **08** | **AI Chatbot & System Audit** | 9 | Chatbot threads, knowledge vectors, admin settings, notifications & audit logs | [08_ai_chatbot.md](file:///d:/Medixo/New_Pratikshya/Backend/pratikshya-backend/docs/schema/08_ai_chatbot.md) |

---

## 🔗 Core Entity Relationship Diagram (ERD Overview)

```mermaid
erDiagram
    users ||--o{ user_roles : "has"
    roles ||--o{ user_roles : "assigned to"
    roles ||--o{ role_permissions : "contains"
    permissions ||--o{ role_permissions : "mapped to"
    
    users ||--o| customer_profiles : "has"
    users ||--o| employee_profiles : "has"
    users ||--o{ user_sessions : "maintains"
    
    catalog_category ||--o{ catalog_subcategory : "has"
    catalog_category ||--o{ catalog_product : "contains"
    catalog_subcategory ||--o{ catalog_product : "contains"
    
    catalog_product ||--o{ variants_product_variant : "has variants"
    catalog_product ||--o{ media_product_media : "has media"
    media_media_asset ||--o{ media_product_media : "mapped in"
    
    catalog_product ||--o{ pricing_product_price : "priced by"
    
    users ||--o{ commerce_cart : "owns"
    commerce_cart ||--o{ commerce_cart_item : "contains"
    variants_product_variant ||--o{ commerce_cart_item : "in cart"
    
    users ||--o{ orders_order : "places"
    orders_order ||--o{ orders_order_item : "contains"
    variants_product_variant ||--o{ orders_order_item : "ordered"
    
    inventory_warehouse ||--o{ inventory_inventory_stock : "houses"
    variants_product_variant ||--o{ inventory_inventory_stock : "tracked in"
    inventory_inventory_stock ||--o{ inventory_inventory_movement : "logs"
```

---

## 📋 Comprehensive Alphabetical Table Index

Below is the complete index of all 65 tables in the `pratikshya` PostgreSQL schema:

"""

sorted_tables = sorted(schema_dump.keys())
for table in sorted_tables:
    # Find module
    mod_file = ""
    mod_name = ""
    for k, v in MODULE_MAPPINGS.items():
        if table in v["tables"]:
            mod_file = f"docs/schema/{k}.md"
            mod_name = v["title"]
            break
    
    col_count = len(schema_dump[table])
    master_md += f"- [`pratikshya.{table}`]({mod_file}#pratikshya{table}) — *{col_count} columns* ({mod_name})\n"

with open(DOCS_DIR / "DATABASE_SCHEMA.md", "w", encoding="utf-8") as f:
    f.write(master_md)

print("Generated master: docs/DATABASE_SCHEMA.md")
