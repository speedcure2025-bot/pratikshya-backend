# Database Schema Compatibility Audit (Read-Only)

This audit inspects the backend's **SQLAlchemy models**, **Alembic migrations**, **Pydantic schemas** and **API/service queries** and produces (a) an expected schema contract, (b) a query-column dependency map, and (c) a local, read-only PostgreSQL verification script.

> **No external database was connected to** and **no migrations, tables, constraints, or data were modified** to create this report.

## Artifacts

| Artifact | Contents |
|----------|----------|
| `schema_audit/expected_schema.json` | Machine-readable expected schema contract |
| `schema_audit/schema_contract.md` | Human-readable per-table contract |
| `schema_audit/query_column_dependencies.json` | Machine-readable query → column map |
| `schema_audit/verify_schema.py` | READ-ONLY local PostgreSQL verifier |
| `schema_audit/generate_expected_schema.py` | Regenerates the contract from models |
| `schema_audit/scan_query_columns.py` | Regenerates the query-column map |

## 1. Expected schema summary

- **Target schema**: `pratikshya`
- **Tables**: 64
- **Columns**: 538
- **Foreign keys**: 31
- **Unique constraints**: 3
- **Indexes**: 137 (by column signature + uniqueness)

Every table, column, type, nullability, PK, FK, unique constraint and index is listed in `schema_contract.md` and in `expected_schema.json`.

**Pydantic schemas** (`app/schemas/**`) were reviewed as API I/O contracts. They describe request/response shapes and are **not** a source of database storage; this audit does not add columns based on schema fields. Where a Pydantic field names a model column, it must already exist in the contract above.

### Tables that are empty model stubs

The following backend models declare **only** the base columns (`id`, `created_at`, `updated_at`). If the real server has additional columns on these tables, `verify_schema.py` will report them as `EXTRA COLUMN` (the existing database is authoritative).

| Tables |
|--------|
| `auth_password_reset`, `auth_verification_token`, `catalog_product_tag` |
| `catalog_tag`, `chatbot_chat_retrieval`, `chatbot_conversation` |
| `chatbot_knowledge_chunk`, `chatbot_knowledge_document`, `chatbot_message` |
| `checkout_checkout`, `checkout_payment`, `checkout_payment_transaction` |
| `inventory_inventory_location`, `inventory_inventory_movement`, `inventory_inventory_stock` |
| `inventory_stock_reservation`, `inventory_stock_transfer`, `inventory_warehouse` |
| `media_marketing_media`, `media_media_asset`, `media_media_review` |
| `media_product_media`, `notification_notification`, `pricing_price_history` |
| `pricing_product_price`, `pricing_tax_rate`, `variants_attribute` |
| `variants_attribute_value`, `variants_product_attribute`, `variants_product_variant` |

### Tables with foreign keys

- `catalog_subcategory`: `category_id` → `catalog_category`
- `commerce_cart`: `coupon_id` → `commerce_coupon`; `customer_id` → `users`
- `commerce_cart_item`: `cart_id` → `commerce_cart`
- `commerce_coupon_redemption`: `coupon_id` → `commerce_coupon`; `customer_id` → `users`
- `commerce_wishlist`: `customer_id` → `users`
- `commerce_wishlist_item`: `wishlist_id` → `commerce_wishlist`
- `customer_address`: `customer_id` → `customer_profiles`
- `customer_preferences`: `customer_id` → `customer_profiles`
- `customer_profiles`: `user_id` → `users`
- `employee_attendance`: `employee_id` → `employee_profiles`
- `employee_performance`: `employee_id` → `employee_profiles`; `reviewer_id` → `users`
- `employee_profiles`: `user_id` → `users`; `department_id` → `employee_department`; `section_id` → `employee_section`
- `employee_section`: `department_id` → `employee_department`
- `employee_target`: `employee_id` → `employee_profiles`
- `oauth_accounts`: `user_id` → `users`
- `orders_order`: `customer_id` → `users`
- `orders_order_item`: `order_id` → `orders_order`
- `orders_order_status_history`: `order_id` → `orders_order`
- `orders_return_item`: `return_order_id` → `orders_return_order`
- `orders_return_order`: `order_id` → `orders_order`
- `payment_sessions`: `order_id` → `orders_order`
- `role_permissions`: `role_id` → `roles`; `permission_id` → `permissions`
- `user_roles`: `role_id` → `roles`; `user_id` → `users`
- `user_sessions`: `user_id` → `users`

### Explicit unique constraints

- `commerce_cart_item`: UNIQUE (`cart_id, product_id, color, size`)
- `commerce_wishlist_item`: UNIQUE (`wishlist_id, product_id`)
- `oauth_accounts`: UNIQUE (`provider, provider_user_id`)

## 2. Migration lineage and schema notes

Alembic migration chain (oldest → newest):

1. `8f0223843258_initial_schema`
2. `597f883749d8_add_customer_address_preferences_columns`
3. `a1b2c3d4e5f6_add_category_subcategory_columns`
4. `c9d1e2f3a4b5_add_collection_columns`
5. `d1e2f3a4b5c6_add_cart_coupon_columns`
6. `e1f2a3b4c5d6_add_orders_columns`
7. `f1a2b3c4d5e6_add_payment_sessions_table`
8. `z1a2b3c4d5e6_add_wishlist_and_activity_columns`
9. `m001_move_tables_to_pratikshya_schema` (moves all app tables into `pratikshya`)
10. `a2b3c4d5e6f7_add_admin_setting_table`

**Important notes**

- The backend `Base` metadata sets `schema='pratikshya'`. If a server has not applied `m001schema`, its tables are still in `public`; `verify_schema.py` reports `MISSING TABLE` in `pratikshya` and notes the table exists in another schema.
- Model-generated index names (`ix_pratikshya_<table>_<col>`) differ from Alembic names (`ix_<table>_<col>`). The verifier therefore matches **indexes by column signature + uniqueness**, not by name.
- `Text()` (SQLAlchemy) is mapped to PG `text`, `String(length=N)` to `varchar(N)`, `JSONB` to `jsonb`, and plain `JSON` to `json`. The verifier compares these details.
- The initial migration created many tables as stubs; several model classes remain stubs with only the base columns (see above).

## 3. Verification semantics (what `verify_schema.py` reports)

| Code | Meaning | Severity |
|------|---------|----------|
| `MISSING TABLE` | Expected backend table not present in the audited schema | error |
| `MISSING COLUMN` | Expected column not present on the table | error |
| `TYPE MISMATCH` | Column kind, length, precision/scale, or timezone differ | error |
| `NULLABILITY MISMATCH` | Expected nullable vs actual nullable differ | error |
| `MISSING PK` | Primary key definition missing or different | error |
| `MISSING FK` | Expected foreign key definition missing | error |
| `MISSING UNIQUE` | Expected unique constraint/index missing | error |
| `MISSING INDEX` | Expected index (columns + uniqueness) missing | error |
| `EXTRA TABLE` | Table exists in schema that backend does not define | info |
| `EXTRA COLUMN` | Column exists on table that backend does not define | info |
| `EXTRA FK` / `EXTRA INDEX` | Extra database constraints clearly absent from backend | info |

> `ERROR` findings make the script exit with code `1`; `INFO` findings do not.

### Run it yourself

```bash
cd backend
# Uses DATABASE_URL from backend/.env (or PG* environment variables)
python schema_audit/verify_schema.py                 # audits the pratikshya schema
python schema_audit/verify_schema.py --schema public # audits the public schema
python schema_audit/verify_schema.py --output report.json
```

The script forces the session to `SET default_transaction_read_only = on`, runs only catalog `SELECT`s, and rolls back. It never prints credentials, tokens or row contents.

## 4. API / service query column dependencies

The static analyzer scanned 286 Python files and found **375 query expressions**. The complete, machine-readable mapping is in `query_column_dependencies.json`; this section summarises it per backend table.

A dependency is a SQLAlchemy `select`/`where`/`filter`/`order_by`/`group_by`/`having`/`join`/`select_from` expression that references a model attribute (i.e. a database column). Raw SQL strings and runtime-built expressions are not parsed.

### `users`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `id` | 19 | analytics.py L196.where analytics_top_customers; auth.py L325.where sign_up_admin |
| `email` | 14 | users.py L67.where list_users; employee_repository.py L46.where email_exists |
| `phone` | 11 | users.py L67.where list_users; employee_repository.py L50.where phone_exists |
| `user_type` | 11 | users.py L69.where list_users; employee_repository.py L28.where get_employee_by_id |
| `full_name` | 5 | users.py L67.where list_users; employee_repository.py L78.where list_employees |
| `created_at` | 3 | users.py L74.order_by list_users; employee_repository.py L97.order_by list_employees |
| `status` | 3 | users.py L71.where list_users; employee_repository.py L74.where list_employees |

### `catalog_product`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `id` | 16 | analytics.py L231.select analytics_inventory_summary; collection_service.py L172.select _rule_product_ids |
| `status` | 14 | analytics.py L75.where analytics_overview; category_service.py L120.where _product_count_for_category |
| `published` | 9 | category_service.py L120.where _product_count_for_category; category_service.py L130.where _product_count_for_subcategory |
| `category` | 6 | category_service.py L120.where _product_count_for_category; category_service.py L130.where _product_count_for_subcategory |
| `sku` | 3 | product_service.py L782.where list_admin_products; product_service.py L1186.where check_availability |
| `slug` | 3 | product_service.py L245.where _get_or_404; product_service.py L1190.where check_availability |
| `assigned_employee_id` | 2 | product_service.py L779.where list_admin_products; product_service.py L1248.where get_metrics |
| `low_stock_threshold` | 2 | analytics.py L67.select analytics_overview; analytics.py L231.select analytics_inventory_summary |
| `stock` | 2 | analytics.py L67.select analytics_overview; analytics.py L231.select analytics_inventory_summary |
| `collection` | 1 | collection_service.py L228.where _label_match_product_ids |
| `collections` | 1 | collection_service.py L228.where _label_match_product_ids |
| `name` | 1 | product_service.py L782.where list_admin_products |
| `review_flags` | 1 | product_service.py L1236.select get_metrics |
| `subcategory` | 1 | category_service.py L130.where _product_count_for_subcategory |

### `orders_order`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `id` | 8 | analytics.py L110.select analytics_sales; analytics.py L137.join analytics_top_products |
| `status` | 8 | analytics.py L54.where analytics_overview; analytics.py L83.where analytics_overview |
| `customer_id` | 7 | analytics.py L173.group_by analytics_top_customers; analytics.py L173.where analytics_top_customers |
| `created_at` | 4 | analytics.py L110.where analytics_sales; analytics.py L110.select analytics_sales |
| `total` | 3 | analytics.py L54.select analytics_overview; analytics.py L110.select analytics_sales |
| `guest_email` | 1 | order_service.py L611.where claim_guest_orders |
| `order_number` | 1 | order_service.py L182.where _load_order_by_number |

### `catalog_collection`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `id` | 4 | collection_service.py L128.where _get_or_404; collection_service.py L140.select _assert_slug_unique |
| `name` | 4 | collection_service.py L267.order_by list_collections; collection_service.py L318.where admin_list_collections |
| `status` | 4 | collection_service.py L264.where list_collections; collection_service.py L314.where admin_list_collections |
| `featured` | 2 | collection_service.py L266.where list_collections; collection_service.py L316.where admin_list_collections |
| `slug` | 2 | collection_service.py L128.where _get_or_404; collection_service.py L140.where _assert_slug_unique |
| `sort_order` | 2 | collection_service.py L267.order_by list_collections; collection_service.py L319.order_by admin_list_collections |

### `user_sessions`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `is_revoked` | 6 | auth_service.py L569.where refresh_access_token; auth_service.py L617.where logout |
| `user_id` | 6 | auth_service.py L569.where refresh_access_token; auth_service.py L617.where logout |
| `expires_at` | 3 | auth_service.py L569.where refresh_access_token; customer_service.py L73.where get_me |

### `employee_profiles`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `user_id` | 6 | users.py L88.where list_users; users.py L121.where get_user |
| `employee_code` | 5 | employee_repository.py L37.where get_employee_by_code; employee_repository.py L55.where employee_code_exists |
| `department_id` | 1 | employee_repository.py L89.where list_employees |
| `designation` | 1 | employee_repository.py L78.where list_employees |

### `roles`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `name` | 9 | roles.py L45.order_by list_roles; users.py L92.select list_users |
| `id` | 3 | roles.py L56.where get_role; users.py L92.join list_users |

### `audit_activity_log`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `action` | 2 | audit.py L61.where list_logs; audit.py L75.where list_logs |
| `created_at` | 2 | admin.py L396.order_by get_activity_log; audit.py L78.order_by list_logs |
| `actor_employee_id` | 1 | audit.py L70.where list_logs |
| `actor_name` | 1 | audit.py L70.where list_logs |
| `summary` | 1 | audit.py L75.where list_logs |
| `target_employee_id` | 1 | audit.py L65.where list_logs |
| `target_order_id` | 1 | audit.py L67.where list_logs |
| `target_product_id` | 1 | audit.py L63.where list_logs |

### `commerce_coupon`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `code` | 4 | coupons.py L182.where validate_offer; coupons.py L247.where admin_create_offer |
| `id` | 4 | coupons.py L288.where admin_update_offer; coupons.py L310.where admin_activate_offer |
| `created_at` | 1 | coupons.py L228.order_by admin_list_offers |
| `is_active` | 1 | coupons.py L152.where list_offers |

### `catalog_category`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `id` | 4 | category_service.py L99.where _get_category_or_404; category_service.py L140.select _assert_slug_unique |
| `slug` | 2 | category_service.py L99.where _get_category_or_404; category_service.py L140.where _assert_slug_unique |
| `featured` | 1 | category_service.py L177.where list_categories |
| `name` | 1 | category_service.py L178.order_by list_categories |
| `sort_order` | 1 | category_service.py L178.order_by list_categories |
| `status` | 1 | category_service.py L175.where list_categories |

### `catalog_subcategory`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `id` | 4 | category_service.py L110.where _get_subcategory_or_404; category_service.py L150.select _assert_sub_slug_unique |
| `category_id` | 2 | category_service.py L150.where _assert_sub_slug_unique; category_service.py L208.where list_subcategories |
| `name` | 1 | category_service.py L211.order_by list_subcategories |
| `slug` | 1 | category_service.py L150.where _assert_sub_slug_unique |
| `sort_order` | 1 | category_service.py L211.order_by list_subcategories |
| `status` | 1 | category_service.py L210.where list_subcategories |

### `orders_order_item`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `product_id` | 2 | analytics.py L137.group_by analytics_top_products; analytics.py L137.select analytics_top_products |
| `product_image` | 2 | analytics.py L137.group_by analytics_top_products; analytics.py L137.select analytics_top_products |
| `product_name` | 2 | analytics.py L137.group_by analytics_top_products; analytics.py L137.select analytics_top_products |
| `line_total` | 1 | analytics.py L137.select analytics_top_products |
| `order_id` | 1 | analytics.py L137.join analytics_top_products |
| `quantity` | 1 | analytics.py L137.select analytics_top_products |

### `permissions`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `code` | 4 | permissions.py L42.order_by list_permissions; permissions.py L58.where get_permission |
| `category` | 1 | permissions.py L42.order_by list_permissions |
| `id` | 1 | roles.py L62.join get_role |

### `commerce_cart_item`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `cart_id` | 3 | cart_service.py L407.where add_item; cart_service.py L446.where update_item |
| `color` | 1 | cart_service.py L407.where add_item |
| `product_id` | 1 | cart_service.py L407.where add_item |
| `size` | 1 | cart_service.py L407.where add_item |

### `user_roles`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `user_id` | 3 | users.py L92.where list_users; users.py L125.where get_user |
| `role_id` | 2 | users.py L92.join list_users; users.py L125.join get_user |

### `admin_setting`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `id` | 4 | admin.py L288.where get_settings_section; admin.py L313.where update_settings_section |

### `employee_attendance`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `employee_id` | 2 | employee_repository.py L151.where list_attendance; employee_repository.py L155.where list_attendance |
| `attendance_date` | 1 | employee_repository.py L155.order_by list_attendance |
| `id` | 1 | employee_repository.py L144.where get_attendance |

### `employee_target`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `employee_id` | 2 | employee_repository.py L177.where list_targets; employee_repository.py L181.where list_targets |
| `id` | 1 | employee_repository.py L170.where get_target |
| `period_start` | 1 | employee_repository.py L181.order_by list_targets |

### `employee_performance`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `employee_id` | 2 | employee_repository.py L203.where list_performance; employee_repository.py L207.where list_performance |
| `id` | 1 | employee_repository.py L196.where get_performance |
| `review_date` | 1 | employee_repository.py L207.order_by list_performance |

### `customer_address`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `customer_id` | 2 | address_service.py L40.where _get_address_owned_by; address_service.py L52.where _demote_existing_default |
| `id` | 1 | address_service.py L40.where _get_address_owned_by |
| `is_default` | 1 | address_service.py L52.where _demote_existing_default |

### `customer_profiles`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `user_id` | 3 | analytics.py L189.where analytics_top_customers; users.py L84.where list_users |

### `employee_department`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `name` | 2 | employee_repository.py L115.where get_department_by_name; employee_repository.py L121.order_by list_departments |
| `id` | 1 | employee_repository.py L109.where get_department |

### `employee_section`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `department_id` | 1 | employee_repository.py L134.where list_sections |
| `id` | 1 | employee_repository.py L127.where get_section |
| `name` | 1 | employee_repository.py L135.order_by list_sections |

### `orders_return_order`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `id` | 2 | order_service.py L902.where _load_return; return_service.py L79.where _load_return |
| `created_at` | 1 | return_service.py L123.order_by list_returns |

### `payment_sessions`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `id` | 1 | payment_service.py L188.where _load_session |
| `idempotency_key` | 1 | payment_service.py L245.where create_session |
| `razorpay_order_id` | 1 | payment_service.py L198.where _load_session_by_razorpay_order |

### `role_permissions`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `permission_id` | 1 | roles.py L62.join get_role |
| `role_id` | 1 | roles.py L62.where get_role |

### `oauth_accounts`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `provider` | 1 | oauth_service.py L207.where _find_or_create_oauth_user |
| `provider_user_id` | 1 | oauth_service.py L207.where _find_or_create_oauth_user |

### `commerce_coupon_redemption`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `coupon_id` | 1 | cart_service.py L539.where apply_coupon |
| `customer_id` | 1 | cart_service.py L539.where apply_coupon |

### `commerce_wishlist_item`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `product_id` | 1 | wishlist_service.py L79.where remove_product |
| `wishlist_id` | 1 | wishlist_service.py L79.where remove_product |

### `commerce_cart`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `customer_id` | 1 | cart_service.py L103.where _get_or_create_cart |

### `commerce_wishlist`

| Column | Query refs | Sample locations |
|--------|------------|------------------|
| `customer_id` | 1 | wishlist_service.py L33.where _get_or_create_wishlist |

### Query references to model attributes that are neither a column nor a relationship

_Every model attribute referenced in the code is either a declared backend column or a declared SQLAlchemy relationship._

## 5. Raw SQL / non-ORM query references (informational)

These are textual references to table names or column literals outside the model attribute path that the AST scanner treats as raw SQL / dynamic fragments. They are reported so they are not forgotten by the ORM-only analyzer.

_No obvious raw SQL statements found._

## 6. Expected file inventory

| File | Purpose |
|------|---------|
| `schema_audit/expected_schema.json` | Expected contract (machine-readable) |
| `schema_audit/schema_contract.md` | Expected contract (human-readable) |
| `schema_audit/query_column_dependencies.json` | Query → column mapping |
| `schema_audit/unmapped_column_refs.json` | Model attrs that are neither column nor relationship |
| `schema_audit/raw_sql_references.json` | Raw-SQL textual references |
| `schema_audit/verify_schema.py` | READ-ONLY PostgreSQL verifier |
| `schema_audit/generate_expected_schema.py` | Contract generator |
| `schema_audit/scan_query_columns.py` | Query-column scanner |
| `schema_audit/render_schema_contract.py` | Contract renderer |
| `schema_audit/render_audit_report.py` | This report generator |
| `schema_audit/README.md` | How to use the audit tooling |
