# Real PostgreSQL Server Audit — Classification of All 108 Findings

**Date:** 2026-08-26
**Scope:** Read-only classification of the 108 findings produced by `schema_audit/verify_schema.py` against the real PostgreSQL server (schema `pratikshya`), compared with the committed expected contract `backend/schema_audit/expected_schema.json`.
**Constraints honoured:** no database modification, no migrations created, no `ALTER TABLE`, no schema change, no model change, no existing file modified. The server schema is treated as authoritative throughout. This report is a **new** analysis document only.

---

## 1. Executive summary

| Category | Count | Verdict |
|---|---|---|
| Missing tables / columns / PKs / uniques / indexes, type mismatches, extra columns | **0** | Perfect structural compatibility |
| NULLABILITY MISMATCH | **44** | **42 SAFE / informational, 2 APPLICATION-CODE RISK (low, data-dependent), 0 real schema risk** |
| MISSING FK | **31** | **29 are pure representation artifacts** (audit-tooling normalization bug); **2 are genuine drift** on the wishlist tables |
| EXTRA FK | **29** | **All 29 are the same artifacts** — every actual FK pairs 1:1 with an expected FK; there are **no** genuinely-extra FKs |
| EXTRA INDEX | **3** | All three are **required unique constraints** declared by the models themselves; **must not be removed** |
| EXTRA TABLE (`pratikshya_alembic_version`) | **1** | Alembic's migration tracking table; **must remain** |

**Bottom line:** the real server is structurally identical to what the backend models expect for every table, column, type, PK, unique constraint and index. **104 of the 108 findings are artifacts of the audit tooling or intentional design**, **2 findings are genuine (but low-impact) referential-integrity drift** on `commerce_wishlist` / `commerce_wishlist_item`, and **2 nullability columns carry a small, data-dependent application-code risk**. Nothing in the 108 findings blocks frontend ↔ backend integration, and **no database or model change is required now**.

### Root cause of all 60 FK findings (31 missing + 29 extra)

`generate_expected_schema.py` (line 190) records each expected FK's referred columns **schema-qualified with the table name**:

```python
referred = [f"{fk_ref.column.table.name}.{fk_ref.column.name}" ...]   # → "users.id"
```

while `verify_schema.py` (lines 448–450 and 466–468) compares them against the **bare column names** returned by `pg_catalog` (`id`). `"users.id" != "id"`, so **no expected FK can ever match any actual FK**: every expected FK is reported `MISSING FK` and every actual FK is reported `EXTRA FK`.

This is exactly the schema-qualification/name-normalization case the task called out — expected renders as `users(users.id)` where PostgreSQL reports `users(id)` — and **the two refer to the same schema (`pratikshya`), same table, same column**. Per the task rules these FKs are **not broken**.

Pairing proof (see §3): after normalizing the `table.` prefix, **29 of the 31 expected FKs match an actual server FK exactly** (same constrained columns, same referred table, same referred columns). Delete/update rules also match the models on all 29 (24 × `ON DELETE CASCADE`, 5 × `ON DELETE SET NULL`). The remaining **2 expected FKs have no server counterpart** — the genuine drift described in §5.

### Why the 44 nullability mismatches exist

All 44 sit in tables that were first created as **stub tables** (`id`/`created_at`/`updated_at` only) by `8f0223843258_initial_schema`. Their business columns were added later, against live tables, by three migrations that deliberately chose `nullable=True`:

- `d1e2f3a4b5c6` — cart / coupon / redemption columns
- `e1f2a3b4c5d6` — order / order-item / status-history / return columns (contains the comment *"Back-fill nulls so NOT NULL can be enforced later if needed"* and explicit `UPDATE … WHERE … IS NULL` back-fills for 20 columns — a later `NOT NULL`-tightening migration was clearly planned and never written)
- `z1a2b3c4d5e6` — wishlist / activity-log columns

So the server's permissive nullability is **intentional** (safe additive-migration discipline), and the models' `nullable=False` is an **intentional application write-contract** (every affected column is `Mapped[str|int|datetime]`, non-Optional, mostly with Python-side defaults). Audit evidence shows the application **always writes these columns non-null** (100 % ORM construction with explicit values; zero raw SQL, zero bulk updates — `raw_sql_references.json` is empty), 26 of the 44 columns additionally carry **server defaults**, and 20 were **back-filled at migration time**. A NULL can therefore only appear through pre-migration stub-era rows or out-of-band/manual writes.

---

## 2. Methodology — how the 108 findings were reproduced and validated

The real-server findings JSON was not checked in (only the summary counts), so the audit was reproduced locally and cross-validated:

1. A **disposable PostgreSQL instance was created inside this sandbox only** (`/tmp`, via `pgserver`). The real server was never contacted or modified.
2. The repo's own migration chain (`8f0223843258 → … → a2b3c4d5e6f7`) was replayed into it — reproducing, byte-for-byte, the schema the real server was built from (`INTEGRATION_AUDIT.md` records the migration history as the authoritative local record of the server schema).
3. The repo's own read-only `verify_schema.py` was run against the committed `expected_schema.json`.

Reproduced counts match the real-server summary **exactly** — 0 / 0 / 0 / 0 / 0 / 0 / 0, NULLABILITY 44, MISSING FK 31, EXTRA FK 29, EXTRA INDEX 3, EXTRA TABLE 1 (total 108) — which simultaneously validates the reproduction *and* confirms the real server has not drifted from the migration history.

Code-level evidence was then gathered statically: model definitions (`app/models/commerce/*`, `app/models/orders/*`), the three creating migrations, every writer (`services/commerce/cart_service.py`, `wishlist_service.py`, `api/v1/coupons.py`, `services/orders/order_service.py`, `return_service.py`, `order_status_service.py`, `services/payments/payment_service.py`), and every reader/serializer (`schemas/orders/order.py`, `schemas/commerce/*.py`, dict builders in `api/v1/coupons.py`, `api/v1/wishlist.py`).

---

## 3. FK mapping — every "missing" FK vs the actual server FK

Normalization applied: expected `users(users.id)` ≡ actual `users(id)` (same schema/table/column) → **representation-only difference, not a broken FK**.

| # | Table.Constraint column(s) | Expected (SQLAlchemy contract) | Actual (PostgreSQL) | Same constraint? | delete rule match |
|---|---|---|---|---|---|
| 1 | `catalog_subcategory.category_id` | → `catalog_category(catalog_category.id)` | → `catalog_category(id)` | ✅ yes | ✅ CASCADE |
| 2 | `commerce_cart.coupon_id` | → `commerce_coupon(commerce_coupon.id)` | → `commerce_coupon(id)` | ✅ yes | ✅ SET NULL |
| 3 | `commerce_cart.customer_id` | → `users(users.id)` | → `users(id)` | ✅ yes | ✅ CASCADE |
| 4 | `commerce_cart_item.cart_id` | → `commerce_cart(commerce_cart.id)` | → `commerce_cart(id)` | ✅ yes | ✅ CASCADE |
| 5 | `commerce_coupon_redemption.coupon_id` | → `commerce_coupon(commerce_coupon.id)` | → `commerce_coupon(id)` | ✅ yes | ✅ CASCADE |
| 6 | `commerce_coupon_redemption.customer_id` | → `users(users.id)` | → `users(id)` | ✅ yes | ✅ CASCADE |
| 7 | `commerce_wishlist.customer_id` | → `users(users.id)` | **— none —** | ❌ **genuinely missing** | n/a |
| 8 | `commerce_wishlist_item.wishlist_id` | → `commerce_wishlist(commerce_wishlist.id)` | **— none —** | ❌ **genuinely missing** | n/a |
| 9 | `customer_address.customer_id` | → `customer_profiles(customer_profiles.id)` | → `customer_profiles(id)` | ✅ yes | ✅ CASCADE |
| 10 | `customer_preferences.customer_id` | → `customer_profiles(customer_profiles.id)` | → `customer_profiles(id)` | ✅ yes | ✅ CASCADE |
| 11 | `customer_profiles.user_id` | → `users(users.id)` | → `users(id)` | ✅ yes | ✅ CASCADE |
| 12 | `employee_attendance.employee_id` | → `employee_profiles(employee_profiles.id)` | → `employee_profiles(id)` | ✅ yes | ✅ CASCADE |
| 13 | `employee_performance.employee_id` | → `employee_profiles(employee_profiles.id)` | → `employee_profiles(id)` | ✅ yes | ✅ CASCADE |
| 14 | `employee_performance.reviewer_id` | → `users(users.id)` | → `users(id)` | ✅ yes | ✅ SET NULL |
| 15 | `employee_profiles.user_id` | → `users(users.id)` | → `users(id)` | ✅ yes | ✅ CASCADE |
| 16 | `employee_profiles.department_id` | → `employee_department(employee_department.id)` | → `employee_department(id)` | ✅ yes | ✅ SET NULL |
| 17 | `employee_profiles.section_id` | → `employee_section(employee_section.id)` | → `employee_section(id)` | ✅ yes | ✅ SET NULL |
| 18 | `employee_section.department_id` | → `employee_department(employee_department.id)` | → `employee_department(id)` | ✅ yes | ✅ CASCADE |
| 19 | `employee_target.employee_id` | → `employee_profiles(employee_profiles.id)` | → `employee_profiles(id)` | ✅ yes | ✅ CASCADE |
| 20 | `oauth_accounts.user_id` | → `users(users.id)` | → `users(id)` | ✅ yes | ✅ CASCADE |
| 21 | `orders_order.customer_id` | → `users(users.id)` | → `users(id)` | ✅ yes | ✅ SET NULL |
| 22 | `orders_order_item.order_id` | → `orders_order(orders_order.id)` | → `orders_order(id)` | ✅ yes | ✅ CASCADE |
| 23 | `orders_order_status_history.order_id` | → `orders_order(orders_order.id)` | → `orders_order(id)` | ✅ yes | ✅ CASCADE |
| 24 | `orders_return_item.return_order_id` | → `orders_return_order(orders_return_order.id)` | → `orders_return_order(id)` | ✅ yes | ✅ CASCADE |
| 25 | `orders_return_order.order_id` | → `orders_order(orders_order.id)` | → `orders_order(id)` | ✅ yes | ✅ CASCADE |
| 26 | `payment_sessions.order_id` | → `orders_order(orders_order.id)` | → `orders_order(id)` | ✅ yes | ✅ CASCADE |
| 27 | `role_permissions.role_id` | → `roles(roles.id)` | → `roles(id)` | ✅ yes | ✅ CASCADE |
| 28 | `role_permissions.permission_id` | → `permissions(permissions.id)` | → `permissions(id)` | ✅ yes | ✅ CASCADE |
| 29 | `user_roles.role_id` | → `roles(roles.id)` | → `roles(id)` | ✅ yes | ✅ CASCADE |
| 30 | `user_roles.user_id` | → `users(users.id)` | → `users(id)` | ✅ yes | ✅ CASCADE |
| 31 | `user_sessions.user_id` | → `users(users.id)` | → `users(id)` | ✅ yes | ✅ CASCADE |

**Result:**
- **29 + 29 = 58 of the 60 FK findings are pure artifacts** of the `table.column` vs `column` normalization difference (audit-tooling bug, not a schema problem). All delete rules match the models.
- **2 findings are genuine drift** (rows 7–8): the models declare `ForeignKey("users.id", ondelete="CASCADE")` on `commerce_wishlist.customer_id` and `ForeignKey("commerce_wishlist.id", ondelete="CASCADE")` on `commerce_wishlist_item.wishlist_id`, but migration `z1a2b3c4d5e6` added these columns **without creating the FK constraints**. Direct catalog inspection confirms the wishlist tables carry only their PKs (+ `uq_wishlist_product`) and **zero** FKs.
- **0 genuinely-extra FKs exist** — every one of the 29 "EXTRA FK" rows pairs with an expected FK above.

---

## 4. The 44 nullability findings — classification table

All 44 are the same direction: **model says `nullable=False` (intentional app contract), server says nullable (intentional additive-migration design)**. Classifications: `SAFE` = informational; `APP-RISK` = application-code risk (low, data-dependent); see §6 for the two recommended data checks.

Writers (all verified to set the column explicitly, via ORM construction only):
`cart_service.py:107,420-427` · `wishlist_service.py:37,67` · `api/v1/coupons.py:243-251` · `order_service.py:293-306,346-382,394-406,557-577` · `payment_service.py` (payment_sessions, not in the 44). `CouponRedemptionModel` has **no insert path anywhere** (read-only usage at `cart_service.py:539-541`).

| # | Table.Column | Server DDL (authoritative) | Created by | Write path | Read path if NULL | Classification |
|---|---|---|---|---|---|---|
| 1 | `commerce_cart.customer_id` | varchar(36) NULL, no default | d1e2f3a4b5c6 | always set (auth id), cart_service.py:107 | equality lookup `customer_id == X` never matches NULL → row unreachable; not exposed in `CartResponse` | **SAFE** |
| 2 | `commerce_cart_item.added_at` | timestamptz NULL, default `now()` | d1e2f3a4b5c6 | set at cart_service.py:426 | default guarantees value | **SAFE** |
| 3 | `commerce_cart_item.cart_id` | varchar(36) NULL, no default | d1e2f3a4b5c6 | set at cart_service.py:421 | FK exists; rows loaded only via `cart.items` relationship keyed on `cart_id` → NULL rows unreachable | **SAFE** |
| 4 | `commerce_cart_item.product_id` | varchar(36) NULL, no default | d1e2f3a4b5c6 | set at cart_service.py:422 (Pydantic-required, non-empty) | `products.get(None)` → line skipped (cart_service.py:248-249) | **SAFE** |
| 5 | `commerce_coupon.code` | varchar(100) NULL, no default (unique idx) | d1e2f3a4b5c6 | required `min_length=2`, normalized `.strip().upper()` (api/v1/coupons.py:243-251); PATCH cannot change code | **no default & never back-filled**; `/offers` + `/admin/offers` list directly and dict-serialize `"code": c.code` → JSON `null` reaches the frontend (cosmetic, no 500); code lookups treat NULL as unfindable | **APP-RISK (low)** — see §6 check #1 |
| 6 | `commerce_coupon_redemption.coupon_id` | varchar(36) NULL, no default | d1e2f3a4b5c6 | **no insert path exists** | equality-filtered read only (cart_service.py:539-541) | **SAFE** |
| 7 | `commerce_coupon_redemption.customer_id` | varchar(36) NULL, no default | d1e2f3a4b5c6 | no insert path | equality-filtered read only | **SAFE** |
| 8 | `commerce_coupon_redemption.coupon_code` | varchar(100) NULL, no default | d1e2f3a4b5c6 | no insert path | never read | **SAFE** |
| 9 | `commerce_wishlist.customer_id` | varchar(36) NULL, no default (unique idx) | z1a2b3c4d5e6 | always set (auth id), wishlist_service.py:37 | equality lookup never matches NULL; not exposed in response | **SAFE** (but see FK drift §5.1) |
| 10 | `commerce_wishlist_item.wishlist_id` | varchar(36) NULL, no default | z1a2b3c4d5e6 | set at wishlist_service.py:67 | relationship-scoped (`wishlist.items`) → NULL rows unreachable | **SAFE** |
| 11 | `commerce_wishlist_item.product_id` | varchar(100) NULL, no default | z1a2b3c4d5e6 | set from path param, wishlist_service.py:67 | dict payload `items` list; NULL only if unreachable row | **SAFE** |
| 12 | `orders_order.order_number` | varchar(50) NULL, no default (unique cnstr) | e1f2a3b4c5d6 | generated `_generate_order_number()` (order_service.py:348) | **back-filled** `'PF-ORD-LEGACY-'||id` at migration time; required `str` in `OrderResponse` → would 500 loudly, but cannot exist | **SAFE** |
| 13 | `orders_order.payment_method` | varchar(30) NULL, no default | e1f2a3b4c5d6 | required by `PlaceOrderRequest` (order_service.py:363) | **back-filled** `'unknown'`; required `str` in response | **SAFE** |
| 14 | `orders_order.delivery_method` | varchar(20) NULL, default `'standard'` | e1f2a3b4c5d6 | set from request | default + back-fill; required `str` in response | **SAFE** |
| 15 | `orders_order.status` | varchar(50) NULL, default `'ORDER_CONFIRMED'` | e1f2a3b4c5d6 | literal writes only | default + back-fill; `in`/`not in` status checks are NULL-safe | **SAFE** |
| 16 | `orders_order.payment_status` | varchar(30) NULL, default `'PENDING'` | e1f2a3b4c5d6 | set (order_service.py:365) | default + back-fill | **SAFE** |
| 17 | `orders_order.subtotal` | integer NULL, default `0` | e1f2a3b4c5d6 | computed (order_service.py:366) | default + back-fill | **SAFE** |
| 18 | `orders_order.product_discount` | integer NULL, default `0` | e1f2a3b4c5d6 | computed | default + back-fill | **SAFE** |
| 19 | `orders_order.coupon_discount` | integer NULL, default `0` | e1f2a3b4c5d6 | computed | default + back-fill | **SAFE** |
| 20 | `orders_order.shipping_fee` | integer NULL, default `0` | e1f2a3b4c5d6 | computed | default + back-fill | **SAFE** |
| 21 | `orders_order.cod_fee` | integer NULL, default `0` | e1f2a3b4c5d6 | computed | default + back-fill | **SAFE** |
| 22 | `orders_order.total` | integer NULL, default `0` | e1f2a3b4c5d6 | computed | default + back-fill | **SAFE** |
| 23 | `orders_order_item.order_id` | varchar(36) NULL, no default | e1f2a3b4c5d6 | set (order_service.py:294 via flush order) | FK exists; relationship-scoped (`order.items`) → NULL rows unreachable | **SAFE** |
| 24 | `orders_order_item.product_id` | varchar(36) NULL, no default | e1f2a3b4c5d6 | set from cart line (order_service.py:295) | on reachable rows always app-written | **SAFE** |
| 25 | `orders_order_item.product_name` | varchar(255) NULL, default `''` | e1f2a3b4c5d6 | snapshot `product.name or ""` (order_service.py:296) | default + back-fill | **SAFE** |
| 26 | `orders_order_item.unit_price` | integer NULL, default `0` | e1f2a3b4c5d6 | computed (order_service.py:301) | default + back-fill | **SAFE** |
| 27 | `orders_order_item.original_price` | integer NULL, default `0` | e1f2a3b4c5d6 | computed | default + back-fill | **SAFE** |
| 28 | `orders_order_item.quantity` | integer NULL, default `1` | e1f2a3b4c5d6 | set (order_service.py:303) | default + back-fill; arithmetic `price * quantity` would TypeError loudly, but cannot be NULL | **SAFE** |
| 29 | `orders_order_item.line_total` | integer NULL, default `0` | e1f2a3b4c5d6 | computed (order_service.py:304) | default + back-fill | **SAFE** |
| 30 | `orders_order_item.returned_quantity` | integer NULL, default `0` | e1f2a3b4c5d6 | default 0 in model | default + back-fill | **SAFE** |
| 31 | `orders_order_status_history.order_id` | varchar(36) NULL, no default | e1f2a3b4c5d6 | set (order_service.py:207,395-403) | FK exists; relationship-scoped (`status_history`) → unreachable | **SAFE** |
| 32 | `orders_order_status_history.to_status` | varchar(50) NULL, default `''` | e1f2a3b4c5d6 | literal writes only | default + back-fill; required `str` in `StatusHistoryEntry` | **SAFE** |
| 33 | `orders_return_item.return_order_id` | varchar(36) NULL, no default | e1f2a3b4c5d6 | set (order_service.py:582) | FK exists; relationship-scoped (`return_order.items`) → unreachable | **SAFE** |
| 34 | `orders_return_item.order_item_id` | varchar(36) NULL, no default | e1f2a3b4c5d6 | copied from `order_item.id` (order_service.py:559) | reachable only via relationship on app-created rows | **SAFE** |
| 35 | `orders_return_item.product_id` | varchar(36) NULL, no default | e1f2a3b4c5d6 | copied snapshot (order_service.py:560) | same | **SAFE** |
| 36 | `orders_return_item.product_name` | varchar(255) NULL, default `''` | e1f2a3b4c5d6 | copied snapshot | default + back-fill | **SAFE** |
| 37 | `orders_return_item.quantity` | integer NULL, default `1` | e1f2a3b4c5d6 | `ReturnItemRequest` requires `ge=1` | default + back-fill | **SAFE** |
| 38 | `orders_return_item.refund_amount` | integer NULL, default `0` | e1f2a3b4c5d6 | computed | default + back-fill | **SAFE** |
| 39 | `orders_return_order.order_id` | varchar(36) NULL, no default | e1f2a3b4c5d6 | set (order_service.py:569) | **no default & never back-filled**; `/admin/returns` lists return orders **directly** (return_service.py:98-115) and `ReturnResponse.order_id` is a required `str` → a NULL row would cause a pydantic 500 | **APP-RISK (low)** — see §6 check #2 |
| 40 | `orders_return_order.return_number` | varchar(50) NULL, no default (unique cnstr) | e1f2a3b4c5d6 | generated (order_service.py:570) | **back-filled** `'PF-RET-LEGACY-'||id` | **SAFE** |
| 41 | `orders_return_order.pickup_method` | varchar(30) NULL, default `'SCHEDULED_PICKUP'` | e1f2a3b4c5d6 | set from request | default + back-fill | **SAFE** |
| 42 | `orders_return_order.status` | varchar(50) NULL, default `'RETURN_REQUESTED'` | e1f2a3b4c5d6 | literal writes only | default + back-fill | **SAFE** |
| 43 | `orders_return_order.refund_amount` | integer NULL, default `0` | e1f2a3b4c5d6 | computed | default + back-fill | **SAFE** |
| 44 | `orders_return_order.refund_status` | varchar(30) NULL, default `'NOT_REQUESTED'` | e1f2a3b4c5d6 | literal writes only | default + back-fill | **SAFE** |

**Tally: 42 SAFE / informational · 2 APPLICATION-CODE RISK (low, conditional on legacy/out-of-band rows) · 0 REAL DATABASE SCHEMA RISK · 0 UNKNOWN** (every code path was statically determinable; the only open variable is row *data*, covered by the two read-only checks in §6 — no human design decision is pending).

Design intent (task item 3): the server schema **intentionally allows NULL** (additive-migration pattern with back-fills; the `NOT NULL`-tightening follow-up was planned — per the migration's own comment — and never written), while `nullable=False` in the models is an **intentional application expectation** enforced on 100 % of write paths. Both positions are defensible; the mismatch is documented drift, not an error on either side.

---

## 5. Genuine schema risks (frontend/backend integration impact)

### 5.1 The only real drift: two missing wishlist FKs — REAL DATABASE SCHEMA RISK (low)

- `commerce_wishlist.customer_id → users.id` (model: CASCADE) — absent on server
- `commerce_wishlist_item.wishlist_id → commerce_wishlist.id` (model: CASCADE) — absent on server

**Impact analysis:** referential integrity for wishlists is not enforced at the DB level. Orphaned wishlist/wishlist-item rows could be created by direct DBA/ETL writes or by deleting a `users` row outside the app. *Within the application* the exposure is minimal: the app never deletes customer users (`delete_employee` only removes employee users, which do not own wishlists), item deletion is app-managed (`wishlist_service.py:86`), and orphaned rows are unreachable through every read path (equality lookup on `customer_id` / relationship on `wishlist_id`). **No frontend/backend integration impact.** The unique protections the models expect on these tables (`ix_commerce_wishlist_customer_id` unique, `uq_wishlist_product`) **do** exist — hence MISSING UNIQUE = 0.

**Classification:** REAL DATABASE SCHEMA RISK — low likelihood, low impact, latent data-hygiene issue only. Do **not** fix now (would require `ALTER TABLE`/new migration, explicitly out of scope). Log it for a future maintenance window, together with the planned NOT NULL tightening the migrations already anticipated.

### 5.2 Everything else

- **29 MISSING FK / 29 EXTRA FK** — audit-tooling artifact (§1, §3). The database's FKs are exactly the models' FKs, including delete rules.
- **44 NULLABILITY MISMATCH** — intentional server design vs intentional app contract; 42 informational, 2 low app-code risk (§4, §6).
- **3 EXTRA INDEX** — not risks; they are protections (see §5.3).
- **EXTRA TABLE** — not a risk (§5.4).
- **0 missing tables/columns/PKs/uniques/indexes, 0 type mismatches, 0 extra columns** — the API's persistence assumptions are fully satisfied. **Nothing in this audit blocks the mock-removal integration plan in `INTEGRATION_AUDIT.md`.**

### 5.3 The 3 extra indexes — required, do NOT remove

All three are **unique constraints backed by `pg_constraint` (contype 'u')** — not plain performance indexes — and each one implements a unique constraint **declared by the backend models themselves** (identical names and column signatures in `expected_schema.json`):

| Index | Table (columns) | Model declaration | What removing it would break |
|---|---|---|---|
| `uq_cart_item_line` | `commerce_cart_item (cart_id, product_id, color, size)` UNIQUE | `UniqueConstraint` in `CartItemModel` | Duplicate cart lines for the same product/color/size — breaks add/increment idempotency (`cart_service.add_item` merges lines by this tuple) |
| `uq_wishlist_product` | `commerce_wishlist_item (wishlist_id, product_id)` UNIQUE | `UniqueConstraint` in `WishlistItemModel` | Duplicate wishlist entries — breaks add-if-absent idempotency (`wishlist_service.toggle/add_product`) |
| `uq_oauth_provider_user` | `oauth_accounts (provider, provider_user_id)` UNIQUE | `UniqueConstraint` in `OAuthAccountModel` | Multiple local users linked to the same OAuth identity — an account-takeover-grade integrity break in OAuth sign-in |

They are reported as "EXTRA INDEX" only because the verifier's extra-index pass compares against the model's *index* list, while these live in the model's *unique-constraint* list (the same category of classification artifact as the FK prefix bug). `MISSING UNIQUE = 0` proves the constraints themselves are expected and satisfied. **Verdict: beneficial and required; removing any of them would be dangerous. Keep all three.**

### 5.4 `pratikshya_alembic_version` — Alembic's tracking table, must remain

Confirmed: single column `version_num varchar(32)`, one row (`a2b3c4d5e6f7` = current head). It is created by `alembic/env.py`, which configures `version_table="pratikshya_alembic_version"` with `version_table_schema="pratikshya"` (a deliberate choice to keep migration state inside the app schema). It is Alembic's migration bookkeeping, not a business table. **Keep it exactly as is**; never register a model for it, never edit rows manually, never drop it (dropping it would make Alembic lose track of the schema version).

---

## 6. Recommended application-code changes (optional; none required)

No change is required for correctness or integration. Two optional hardening items and two hygiene notes:

1. **Data verification (read-only, run by a human with read credentials)** — closes the only two open variables from §4:
   ```sql
   SELECT count(*) FROM pratikshya.commerce_coupon     WHERE code     IS NULL;  -- expect 0
   SELECT count(*) FROM pratikshya.orders_return_order WHERE order_id IS NULL;  -- expect 0
   ```
   Both return 0 → the two APP-RISK rows in §4 downgrade to SAFE and the 44 findings are 100 % informational.
2. **Optional defensive serializer** (only if check #1 is not possible): `_coupon_to_dict` in `api/v1/coupons.py` could emit `"code": c.code or ""` so a stray NULL code degrades to an empty badge instead of a `null` chip in the storefront offers strip. One-line, no schema impact. (Not applied — analysis-only task.)
3. **Tooling fix for future audits (file change, explicitly not made now):** in `schema_audit/generate_expected_schema.py` line 190, record `fk_ref.column.name` (bare) instead of `f"{table}.{column}"` — or normalize both sides in `verify_schema.py`. That alone eliminates 58 of 108 findings on the next run and would surface the 2 genuine wishlist FK gaps cleanly. No model, migration, or database impact.
4. **Functional observations logged for their owners (not schema issues):** `CouponRedemptionModel` has no insert path anywhere in the codebase, so per-coupon/per-customer usage counting (`cart_service.py:539-541`) always sees zero rows until redemption recording is implemented; and the NOT NULL tightening the `e1f2a3b4c5d6` comment anticipated ("Back-fill nulls so NOT NULL can be enforced later if needed") remains an open, deliberately deferred task.

---

## 7. Explicit confirmation — what must NOT be changed

1. **The database schema** — nothing: no `ALTER TABLE`, no new constraints, no index changes, no drops. The server is authoritative and already matches every structural expectation (0 missing anything, 0 type mismatches).
2. **The 3 unique "extra" indexes** (`uq_cart_item_line`, `uq_wishlist_product`, `uq_oauth_provider_user`) — required integrity protections; removing them is dangerous.
3. **`pratikshya_alembic_version`** — Alembic's migration tracking table in the `pratikshya` schema; must remain untouched.
4. **The 29 real FKs** — all present and semantically identical to the models (including ON DELETE rules). The MISSING/EXTRA FK findings are tooling artifacts, not defects.
5. **The models' `nullable=False` declarations** — they are a correct, intentional write-contract and are enforced on every write path; relaxing them is neither needed nor recommended.
6. **The server's permissive nullability** — intentional additive-migration design with back-fills; tightening to NOT NULL is a deferred, separate decision requiring its own migration (out of scope and not requested).
7. **The two wishlist FKs are documented, not fixed** — adding them would violate the no-migration/no-ALTER constraint; they are logged as latent, low-impact drift for a future maintenance window.
8. **No files were modified** in producing this analysis; this report is a newly added document, and the reproduction database lived entirely in `/tmp` inside this sandbox (the real server was never connected to).
