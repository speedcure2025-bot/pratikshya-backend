# Expected Backend Schema Contract (human-readable)

Generated from the backend SQLAlchemy models. No external database was accessed.

- **Schema**: `pratikshya`
- **Tables**: 64
- **Columns**: 560
- **Foreign keys**: 34
- **Unique constraints**: 5
- **Indexes**: 139

> Index matching in the verification script is by column signature + 
> uniqueness, not by index name, because Alembic/model naming conventions differ.


## `admin_setting`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(64)` | false | yes | `` |
| `value` | `jsonb` | false |  | `<builtin:dict>` |
| `updated_by` | `varchar(36)` | true |  | `` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_admin_setting_id` | (id) | false |


## `audit_activity_log`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `actor_employee_id` | `varchar(36)` | true |  | `` |
| `actor_name` | `varchar(255)` | true |  | `` |
| `target_employee_id` | `varchar(36)` | true |  | `` |
| `target_product_id` | `varchar(100)` | true |  | `` |
| `target_offer_id` | `varchar(36)` | true |  | `` |
| `target_category_id` | `varchar(36)` | true |  | `` |
| `target_collection_id` | `varchar(36)` | true |  | `` |
| `target_order_id` | `varchar(36)` | true |  | `` |
| `target_return_id` | `varchar(36)` | true |  | `` |
| `target_media_id` | `varchar(36)` | true |  | `` |
| `action` | `varchar(100)` | true |  | `` |
| `summary` | `text` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_audit_activity_log_action` | (action) | false |
| `ix_pratikshya_audit_activity_log_actor_employee_id` | (actor_employee_id) | false |
| `ix_pratikshya_audit_activity_log_id` | (id) | false |
| `ix_pratikshya_audit_activity_log_target_employee_id` | (target_employee_id) | false |
| `ix_pratikshya_audit_activity_log_target_product_id` | (target_product_id) | false |


## `auth_password_reset`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_auth_password_reset_id` | (id) | false |


## `auth_verification_token`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_auth_verification_token_id` | (id) | false |


## `catalog_category`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `name` | `varchar(100)` | false |  | `` |
| `slug` | `varchar(120)` | false |  | `` |
| `eyebrow` | `varchar(100)` | true |  | `''` |
| `description` | `text` | true |  | `''` |
| `image` | `text` | true |  | `''` |
| `banner_media_id` | `varchar(64)` | true |  | `` |
| `sort_order` | `integer` | false |  | `0` |
| `featured` | `boolean` | false |  | `False` |
| `seo_title` | `varchar(255)` | true |  | `''` |
| `seo_description` | `text` | true |  | `''` |
| `status` | `varchar(30)` | false |  | `'DRAFT'` |
| `created_by` | `varchar(64)` | true |  | `` |
| `updated_by` | `varchar(64)` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_catalog_category_sort_order` | (sort_order) | false |
| `ix_catalog_category_status` | (status) | false |
| `ix_pratikshya_catalog_category_id` | (id) | false |
| `ix_pratikshya_catalog_category_slug` | (slug) | true |


## `catalog_collection`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `name` | `varchar(200)` | false |  | `` |
| `slug` | `varchar(220)` | false |  | `` |
| `eyebrow` | `varchar(120)` | true |  | `''` |
| `description` | `text` | true |  | `''` |
| `image` | `text` | true |  | `''` |
| `hero_media_id` | `varchar(64)` | true |  | `` |
| `thumbnail_media_id` | `varchar(64)` | true |  | `` |
| `type` | `varchar(30)` | false |  | `'MANUAL'` |
| `start_date` | `timestamp with time zone` | true |  | `` |
| `end_date` | `timestamp with time zone` | true |  | `` |
| `status` | `varchar(30)` | false |  | `'DRAFT'` |
| `featured` | `boolean` | false |  | `False` |
| `sort_order` | `integer` | false |  | `0` |
| `explicit_product_ids` | `jsonb` | true |  | `<builtin:list>` |
| `rule` | `jsonb` | true |  | `<builtin:dict>` |
| `created_by` | `varchar(64)` | true |  | `` |
| `updated_by` | `varchar(64)` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_catalog_collection_sort_order` | (sort_order) | false |
| `ix_catalog_collection_status` | (status) | false |
| `ix_catalog_collection_type` | (type) | false |
| `ix_pratikshya_catalog_collection_id` | (id) | false |
| `ix_pratikshya_catalog_collection_slug` | (slug) | true |


## `catalog_product`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `product_id` | `varchar(64)` | false |  | `` |
| `name` | `varchar(255)` | false |  | `''` |
| `slug` | `varchar(255)` | false |  | `''` |
| `sku` | `varchar(100)` | false |  | `''` |
| `brand` | `varchar(100)` | false |  | `'Pratikshya Fashon'` |
| `product_type` | `varchar(50)` | false |  | `'fashion'` |
| `product_code` | `varchar(100)` | true |  | `''` |
| `barcode` | `varchar(100)` | true |  | `''` |
| `internal_reference` | `varchar(100)` | true |  | `''` |
| `category` | `varchar(100)` | false |  | `''` |
| `subcategory` | `varchar(100)` | true |  | `''` |
| `gender` | `varchar(20)` | false |  | `'Women'` |
| `short_description` | `text` | true |  | `''` |
| `description` | `text` | true |  | `''` |
| `highlights` | `jsonb` | true |  | `<builtin:list>` |
| `specifications` | `jsonb` | true |  | `<builtin:dict>` |
| `care_instructions` | `jsonb` | true |  | `<builtin:list>` |
| `delivery_info` | `text` | true |  | `''` |
| `return_info` | `text` | true |  | `''` |
| `return_policy` | `jsonb` | true |  | `<builtin:dict>` |
| `fabric` | `varchar(100)` | true |  | `''` |
| `material` | `varchar(100)` | true |  | `''` |
| `primary_color` | `varchar(100)` | true |  | `''` |
| `secondary_color` | `varchar(100)` | true |  | `''` |
| `colors` | `jsonb` | true |  | `<builtin:list>` |
| `patterns` | `jsonb` | true |  | `<builtin:list>` |
| `work` | `jsonb` | true |  | `<builtin:list>` |
| `occasion` | `jsonb` | true |  | `<builtin:list>` |
| `sizes` | `jsonb` | true |  | `<builtin:list>` |
| `unavailable_colors` | `jsonb` | true |  | `<builtin:list>` |
| `unavailable_sizes` | `jsonb` | true |  | `<builtin:list>` |
| `season` | `varchar(50)` | true |  | `''` |
| `fit` | `varchar(50)` | true |  | `''` |
| `length` | `varchar(50)` | true |  | `''` |
| `collection` | `varchar(200)` | true |  | `''` |
| `collections` | `jsonb` | true |  | `<builtin:list>` |
| `tags` | `jsonb` | true |  | `<builtin:list>` |
| `badges` | `jsonb` | true |  | `<builtin:list>` |
| `is_featured` | `boolean` | false |  | `False` |
| `is_bestseller` | `boolean` | false |  | `False` |
| `is_new` | `boolean` | false |  | `False` |
| `is_limited_edition` | `boolean` | false |  | `False` |
| `is_trending` | `boolean` | false |  | `False` |
| `flags` | `jsonb` | true |  | `<builtin:dict>` |
| `price` | `integer` | false |  | `0` |
| `original_price` | `integer` | true |  | `` |
| `compare_at_price` | `integer` | true |  | `` |
| `currency` | `varchar(3)` | false |  | `'INR'` |
| `pricing` | `jsonb` | true |  | `<builtin:dict>` |
| `stock` | `integer` | false |  | `0` |
| `availability` | `varchar(30)` | false |  | `'in-stock'` |
| `inventory_tracked` | `boolean` | false |  | `False` |
| `low_stock_threshold` | `integer` | false |  | `5` |
| `rating` | `numeric(3,2)` | true |  | `` |
| `review_count` | `integer` | false |  | `0` |
| `seo` | `jsonb` | true |  | `<builtin:dict>` |
| `status` | `varchar(30)` | false |  | `'DRAFT'` |
| `published` | `boolean` | false |  | `False` |
| `review` | `jsonb` | true |  | `<builtin:dict>` |
| `assigned_employee_id` | `varchar(64)` | true |  | `` |
| `review_flags` | `jsonb` | true |  | `<builtin:list>` |
| `media_ids` | `jsonb` | true |  | `<builtin:list>` |
| `primary_media_id` | `varchar(64)` | true |  | `` |
| `gallery_media_ids` | `jsonb` | true |  | `<builtin:list>` |
| `image` | `text` | true |  | `''` |
| `hover_image` | `text` | true |  | `''` |
| `additional_images` | `jsonb` | true |  | `<builtin:list>` |
| `created_by` | `varchar(64)` | true |  | `` |
| `updated_by` | `varchar(64)` | true |  | `` |
| `published_by` | `varchar(64)` | true |  | `` |
| `published_at` | `timestamp with time zone` | true |  | `` |
| `history` | `jsonb` | true |  | `<builtin:list>` |
| `price_history` | `jsonb` | true |  | `<builtin:list>` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_catalog_product_assigned_employee` | (assigned_employee_id) | false |
| `ix_catalog_product_category_status` | (category, status) | false |
| `ix_catalog_product_sku` | (sku) | false |
| `ix_catalog_product_slug` | (slug) | false |
| `ix_catalog_product_status` | (status) | false |
| `ix_pratikshya_catalog_product_category` | (category) | false |
| `ix_pratikshya_catalog_product_id` | (id) | false |


## `catalog_product_tag`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_catalog_product_tag_id` | (id) | false |


## `catalog_subcategory`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `category_id` | `varchar(36)` | false |  | `` |
| `name` | `varchar(100)` | false |  | `` |
| `slug` | `varchar(120)` | false |  | `` |
| `description` | `text` | true |  | `''` |
| `image` | `text` | true |  | `''` |
| `sort_order` | `integer` | false |  | `0` |
| `status` | `varchar(30)` | false |  | `'DRAFT'` |
| `created_by` | `varchar(64)` | true |  | `` |
| `updated_by` | `varchar(64)` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `category_id` -> `catalog_category` (`catalog_category.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_catalog_subcategory_status` | (status) | false |
| `ix_pratikshya_catalog_subcategory_category_id` | (category_id) | false |
| `ix_pratikshya_catalog_subcategory_id` | (id) | false |
| `ix_pratikshya_catalog_subcategory_slug` | (slug) | false |
| `uq_subcategory_category_slug` | (category_id, slug) | true |


## `catalog_tag`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_catalog_tag_id` | (id) | false |


## `chatbot_chat_retrieval`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_chatbot_chat_retrieval_id` | (id) | false |


## `chatbot_conversation`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_chatbot_conversation_id` | (id) | false |


## `chatbot_knowledge_chunk`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_chatbot_knowledge_chunk_id` | (id) | false |


## `chatbot_knowledge_document`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_chatbot_knowledge_document_id` | (id) | false |


## `chatbot_message`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_chatbot_message_id` | (id) | false |


## `checkout_checkout`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_checkout_checkout_id` | (id) | false |


## `checkout_payment`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_checkout_payment_id` | (id) | false |


## `checkout_payment_transaction`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_checkout_payment_transaction_id` | (id) | false |


## `commerce_cart`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `customer_id` | `varchar(36)` | false |  | `` |
| `coupon_code` | `varchar(100)` | true |  | `` |
| `coupon_id` | `varchar(36)` | true |  | `` |
| `coupon_lapsed` | `boolean` | false |  | `False` |
| `customer_note` | `text` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `customer_id` -> `users` (`users.id`) ondelete=CASCADE
- `coupon_id` -> `commerce_coupon` (`commerce_coupon.id`) ondelete=SET NULL

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_commerce_cart_customer_id` | (customer_id) | true |
| `ix_pratikshya_commerce_cart_id` | (id) | false |


## `commerce_cart_item`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `cart_id` | `varchar(36)` | false |  | `` |
| `product_id` | `varchar(36)` | false |  | `` |
| `color` | `varchar(100)` | true |  | `` |
| `size` | `varchar(50)` | true |  | `` |
| `quantity` | `integer` | false |  | `1` |
| `added_at` | `timestamp with time zone` | false |  | `<callable>` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `cart_id` -> `commerce_cart` (`commerce_cart.id`) ondelete=CASCADE

### Unique constraints

- `uq_cart_item_line`: (cart_id, product_id, color, size)

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_commerce_cart_item_cart_id` | (cart_id) | false |
| `ix_pratikshya_commerce_cart_item_id` | (id) | false |
| `ix_pratikshya_commerce_cart_item_product_id` | (product_id) | false |


## `commerce_coupon`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `code` | `varchar(100)` | false |  | `` |
| `name` | `varchar(255)` | true |  | `` |
| `description` | `text` | true |  | `` |
| `discount_type` | `varchar(20)` | false |  | `'percentage'` |
| `discount_value` | `double precision` | false |  | `0.0` |
| `minimum_order_value` | `integer` | false |  | `0` |
| `starts_at` | `timestamp with time zone` | true |  | `` |
| `expires_at` | `timestamp with time zone` | true |  | `` |
| `usage_limit` | `integer` | true |  | `` |
| `usage_count` | `integer` | false |  | `0` |
| `per_customer_limit` | `integer` | true |  | `` |
| `eligible_customer_ids` | `jsonb` | true |  | `` |
| `eligible_product_ids` | `jsonb` | true |  | `` |
| `eligible_category_ids` | `jsonb` | true |  | `` |
| `eligible_collection_ids` | `jsonb` | true |  | `` |
| `excluded_product_ids` | `jsonb` | true |  | `` |
| `excluded_category_ids` | `jsonb` | true |  | `` |
| `is_stackable` | `boolean` | false |  | `False` |
| `is_active` | `boolean` | false |  | `True` |
| `created_by` | `varchar(36)` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_commerce_coupon_code` | (code) | true |
| `ix_pratikshya_commerce_coupon_id` | (id) | false |


## `commerce_coupon_redemption`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `coupon_id` | `varchar(36)` | false |  | `` |
| `customer_id` | `varchar(36)` | false |  | `` |
| `order_id` | `varchar(36)` | true |  | `` |
| `coupon_code` | `varchar(100)` | false |  | `` |
| `discount_amount` | `integer` | false |  | `0` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `customer_id` -> `users` (`users.id`) ondelete=CASCADE
- `coupon_id` -> `commerce_coupon` (`commerce_coupon.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_commerce_coupon_redemption_coupon_id` | (coupon_id) | false |
| `ix_pratikshya_commerce_coupon_redemption_customer_id` | (customer_id) | false |
| `ix_pratikshya_commerce_coupon_redemption_id` | (id) | false |
| `ix_pratikshya_commerce_coupon_redemption_order_id` | (order_id) | false |


## `commerce_wishlist`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `customer_id` | `varchar(36)` | false |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `customer_id` -> `users` (`users.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_commerce_wishlist_customer_id` | (customer_id) | true |
| `ix_pratikshya_commerce_wishlist_id` | (id) | false |


## `commerce_wishlist_item`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `wishlist_id` | `varchar(36)` | false |  | `` |
| `product_id` | `varchar(100)` | false |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `wishlist_id` -> `commerce_wishlist` (`commerce_wishlist.id`) ondelete=CASCADE

### Unique constraints

- `uq_wishlist_product`: (wishlist_id, product_id)

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_commerce_wishlist_item_id` | (id) | false |
| `ix_pratikshya_commerce_wishlist_item_product_id` | (product_id) | false |
| `ix_pratikshya_commerce_wishlist_item_wishlist_id` | (wishlist_id) | false |


## `customer_address`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `customer_id` | `varchar(36)` | false |  | `` |
| `full_name` | `varchar(255)` | false |  | `` |
| `phone` | `varchar(20)` | false |  | `` |
| `address_line` | `varchar(500)` | false |  | `` |
| `landmark` | `varchar(255)` | true |  | `` |
| `city` | `varchar(100)` | false |  | `` |
| `state` | `varchar(100)` | false |  | `` |
| `pincode` | `varchar(10)` | false |  | `` |
| `address_type` | `varchar(50)` | false |  | `'Home'` |
| `is_default` | `boolean` | false |  | `False` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `customer_id` -> `customer_profiles` (`customer_profiles.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_customer_address_customer_id` | (customer_id) | false |
| `ix_pratikshya_customer_address_id` | (id) | false |


## `customer_preferences`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `customer_id` | `varchar(36)` | false |  | `` |
| `email_notifications` | `boolean` | false |  | `True` |
| `sms_notifications` | `boolean` | false |  | `True` |
| `promotional_updates` | `boolean` | false |  | `True` |
| `order_updates` | `boolean` | false |  | `True` |
| `styling_invitations` | `boolean` | false |  | `True` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `customer_id` -> `customer_profiles` (`customer_profiles.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_customer_preferences_customer_id` | (customer_id) | true |
| `ix_pratikshya_customer_preferences_id` | (id) | false |


## `customer_profiles`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `user_id` | `varchar(36)` | false |  | `` |
| `first_name` | `varchar(100)` | true |  | `` |
| `last_name` | `varchar(100)` | true |  | `` |
| `avatar` | `varchar(1000)` | true |  | `` |
| `date_of_birth` | `date` | true |  | `` |
| `loyalty_tier` | `varchar(50)` | false |  | `'BRONZE'` |
| `loyalty_points` | `integer` | false |  | `0` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `user_id` -> `users` (`users.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_customer_profiles_id` | (id) | false |
| `ix_pratikshya_customer_profiles_user_id` | (user_id) | true |


## `employee_attendance`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `employee_id` | `varchar(36)` | false |  | `` |
| `attendance_date` | `date` | false |  | `` |
| `check_in` | `time without time zone` | true |  | `` |
| `check_out` | `time without time zone` | true |  | `` |
| `status` | `varchar(20)` | false |  | `'PRESENT'` |
| `notes` | `text` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `employee_id` -> `employee_profiles` (`employee_profiles.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_employee_attendance_attendance_date` | (attendance_date) | false |
| `ix_pratikshya_employee_attendance_employee_id` | (employee_id) | false |
| `ix_pratikshya_employee_attendance_id` | (id) | false |


## `employee_department`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `name` | `varchar(100)` | false |  | `` |
| `description` | `text` | true |  | `` |
| `is_active` | `boolean` | false |  | `True` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_employee_department_id` | (id) | false |
| `ix_pratikshya_employee_department_name` | (name) | true |


## `employee_performance`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `employee_id` | `varchar(36)` | false |  | `` |
| `review_date` | `date` | false |  | `` |
| `rating` | `integer` | false |  | `` |
| `review_period` | `varchar(20)` | false |  | `'MONTHLY'` |
| `reviewer_id` | `varchar(36)` | true |  | `` |
| `comments` | `text` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `reviewer_id` -> `users` (`users.id`) ondelete=SET NULL
- `employee_id` -> `employee_profiles` (`employee_profiles.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_employee_performance_employee_id` | (employee_id) | false |
| `ix_pratikshya_employee_performance_id` | (id) | false |
| `ix_pratikshya_employee_performance_review_date` | (review_date) | false |


## `employee_profiles`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `user_id` | `varchar(36)` | false |  | `` |
| `employee_code` | `varchar(50)` | false |  | `` |
| `designation` | `varchar(100)` | false |  | `` |
| `department` | `varchar(100)` | true |  | `` |
| `department_id` | `varchar(36)` | true |  | `` |
| `section_id` | `varchar(36)` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `user_id` -> `users` (`users.id`) ondelete=CASCADE
- `department_id` -> `employee_department` (`employee_department.id`) ondelete=SET NULL
- `section_id` -> `employee_section` (`employee_section.id`) ondelete=SET NULL

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_employee_profiles_department_id` | (department_id) | false |
| `ix_pratikshya_employee_profiles_employee_code` | (employee_code) | true |
| `ix_pratikshya_employee_profiles_id` | (id) | false |
| `ix_pratikshya_employee_profiles_section_id` | (section_id) | false |
| `ix_pratikshya_employee_profiles_user_id` | (user_id) | true |


## `employee_section`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `department_id` | `varchar(36)` | false |  | `` |
| `name` | `varchar(100)` | false |  | `` |
| `description` | `text` | true |  | `` |
| `is_active` | `boolean` | false |  | `True` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `department_id` -> `employee_department` (`employee_department.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_employee_section_department_id` | (department_id) | false |
| `ix_pratikshya_employee_section_id` | (id) | false |


## `employee_target`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `employee_id` | `varchar(36)` | false |  | `` |
| `period_start` | `date` | false |  | `` |
| `period_end` | `date` | false |  | `` |
| `target_amount` | `numeric(12,2)` | false |  | `` |
| `achieved_amount` | `numeric(12,2)` | true |  | `0` |
| `target_type` | `varchar(50)` | false |  | `'SALES'` |
| `notes` | `text` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `employee_id` -> `employee_profiles` (`employee_profiles.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_employee_target_employee_id` | (employee_id) | false |
| `ix_pratikshya_employee_target_id` | (id) | false |


## `inventory_inventory_location`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_inventory_inventory_location_id` | (id) | false |


## `inventory_inventory_movement`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_inventory_inventory_movement_id` | (id) | false |


## `inventory_inventory_stock`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_inventory_inventory_stock_id` | (id) | false |


## `inventory_stock_reservation`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_inventory_stock_reservation_id` | (id) | false |


## `inventory_stock_transfer`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_inventory_stock_transfer_id` | (id) | false |


## `inventory_warehouse`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_inventory_warehouse_id` | (id) | false |


## `media_marketing_media`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_media_marketing_media_id` | (id) | false |


## `media_media_asset`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `object_key` | `varchar(512)` | false |  | `` |
| `storage_provider` | `varchar(20)` | false |  | `'local'` |
| `media_type` | `varchar(30)` | false |  | `'image'` |
| `mime_type` | `varchar(100)` | false |  | `` |
| `original_filename` | `varchar(255)` | false |  | `` |
| `file_size` | `integer` | false |  | `` |
| `checksum_sha256` | `varchar(64)` | false |  | `` |
| `width` | `integer` | true |  | `` |
| `height` | `integer` | true |  | `` |
| `title` | `varchar(255)` | true |  | `` |
| `alt_text` | `text` | true |  | `` |
| `caption` | `text` | true |  | `` |
| `status` | `varchar(30)` | false |  | `'uploaded'` |
| `scope` | `varchar(30)` | false |  | `'product'` |
| `uploaded_by` | `varchar(36)` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `uploaded_by` -> `users` (`users.id`) ondelete=SET NULL

### Unique constraints

- `uq_media_asset_object_key`: (object_key)

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_media_media_asset_checksum_sha256` | (checksum_sha256) | false |
| `ix_pratikshya_media_media_asset_id` | (id) | false |


## `media_media_review`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_media_media_review_id` | (id) | false |


## `media_product_media`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `product_id` | `varchar(36)` | false |  | `` |
| `media_id` | `varchar(36)` | false |  | `` |
| `role` | `varchar(30)` | false |  | `'gallery'` |
| `sort_order` | `integer` | false |  | `0` |
| `is_primary` | `boolean` | false |  | `False` |
| `assigned_by` | `varchar(36)` | true |  | `` |
| `assignment_note` | `varchar(500)` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `product_id` -> `catalog_product` (`catalog_product.id`) ondelete=CASCADE
- `media_id` -> `media_media_asset` (`media_media_asset.id`) ondelete=CASCADE

### Unique constraints

- `uq_product_media_asset`: (product_id, media_id)

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_media_product_media_id` | (id) | false |
| `ix_pratikshya_media_product_media_media_id` | (media_id) | false |


## `notification_notification`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_notification_notification_id` | (id) | false |


## `oauth_accounts`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `user_id` | `varchar(36)` | false |  | `` |
| `provider` | `varchar(50)` | false |  | `` |
| `provider_user_id` | `varchar(255)` | false |  | `` |
| `email` | `varchar(255)` | true |  | `` |
| `access_token` | `varchar(2048)` | true |  | `` |
| `expires_at` | `timestamp with time zone` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `user_id` -> `users` (`users.id`) ondelete=CASCADE

### Unique constraints

- `uq_oauth_provider_user`: (provider, provider_user_id)

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_oauth_accounts_id` | (id) | false |
| `ix_pratikshya_oauth_accounts_provider` | (provider) | false |
| `ix_pratikshya_oauth_accounts_user_id` | (user_id) | false |


## `orders_order`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `order_number` | `varchar(50)` | false |  | `` |
| `customer_id` | `varchar(36)` | true |  | `` |
| `guest_email` | `varchar(255)` | true |  | `` |
| `guest_phone` | `varchar(20)` | true |  | `` |
| `shipping_address` | `json` | true |  | `` |
| `delivery_method` | `varchar(20)` | false |  | `'standard'` |
| `payment_method` | `varchar(30)` | false |  | `` |
| `status` | `varchar(50)` | false |  | `'ORDER_CONFIRMED'` |
| `payment_status` | `varchar(30)` | false |  | `'PENDING'` |
| `subtotal` | `integer` | false |  | `0` |
| `product_discount` | `integer` | false |  | `0` |
| `coupon_discount` | `integer` | false |  | `0` |
| `shipping_fee` | `integer` | false |  | `0` |
| `cod_fee` | `integer` | false |  | `0` |
| `total` | `integer` | false |  | `0` |
| `coupon_code` | `varchar(50)` | true |  | `` |
| `coupon_id` | `varchar(36)` | true |  | `` |
| `customer_note` | `text` | true |  | `` |
| `inventory_reservation_id` | `varchar(36)` | true |  | `` |
| `fulfillment_location_id` | `varchar(36)` | true |  | `` |
| `fulfillment_handler_id` | `varchar(36)` | true |  | `` |
| `tracking_number` | `varchar(100)` | true |  | `` |
| `carrier` | `varchar(100)` | true |  | `` |
| `estimated_delivery` | `timestamp with time zone` | true |  | `` |
| `delivered_at` | `timestamp with time zone` | true |  | `` |
| `dispatched_at` | `timestamp with time zone` | true |  | `` |
| `internal_notes` | `json` | true |  | `<builtin:list>` |
| `timeline` | `json` | true |  | `<builtin:list>` |
| `invoice_number` | `varchar(100)` | true |  | `` |
| `invoice_issued_at` | `timestamp with time zone` | true |  | `` |
| `cancelled_at` | `timestamp with time zone` | true |  | `` |
| `cancellation_reason` | `text` | true |  | `` |
| `cancelled_by` | `varchar(36)` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `customer_id` -> `users` (`users.id`) ondelete=SET NULL

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_orders_order_customer_id` | (customer_id) | false |
| `ix_pratikshya_orders_order_id` | (id) | false |
| `ix_pratikshya_orders_order_order_number` | (order_number) | true |
| `ix_pratikshya_orders_order_status` | (status) | false |


## `orders_order_item`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `order_id` | `varchar(36)` | false |  | `` |
| `product_id` | `varchar(36)` | false |  | `` |
| `product_name` | `varchar(255)` | false |  | `` |
| `product_image` | `varchar(500)` | true |  | `` |
| `sku` | `varchar(100)` | true |  | `` |
| `color` | `varchar(100)` | true |  | `` |
| `size` | `varchar(50)` | true |  | `` |
| `unit_price` | `integer` | false |  | `` |
| `original_price` | `integer` | false |  | `0` |
| `quantity` | `integer` | false |  | `1` |
| `line_total` | `integer` | false |  | `` |
| `returned_quantity` | `integer` | false |  | `0` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `order_id` -> `orders_order` (`orders_order.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_orders_order_item_id` | (id) | false |
| `ix_pratikshya_orders_order_item_order_id` | (order_id) | false |
| `ix_pratikshya_orders_order_item_product_id` | (product_id) | false |


## `orders_order_status_history`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `order_id` | `varchar(36)` | false |  | `` |
| `from_status` | `varchar(50)` | true |  | `` |
| `to_status` | `varchar(50)` | false |  | `` |
| `actor_id` | `varchar(36)` | true |  | `` |
| `actor_name` | `varchar(255)` | true |  | `` |
| `note` | `text` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `order_id` -> `orders_order` (`orders_order.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_orders_order_status_history_id` | (id) | false |
| `ix_pratikshya_orders_order_status_history_order_id` | (order_id) | false |


## `orders_return_item`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `return_order_id` | `varchar(36)` | false |  | `` |
| `order_item_id` | `varchar(36)` | false |  | `` |
| `product_id` | `varchar(36)` | false |  | `` |
| `product_name` | `varchar(255)` | false |  | `` |
| `quantity` | `integer` | false |  | `1` |
| `reason` | `text` | true |  | `` |
| `refund_amount` | `integer` | false |  | `0` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `return_order_id` -> `orders_return_order` (`orders_return_order.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_orders_return_item_id` | (id) | false |
| `ix_pratikshya_orders_return_item_return_order_id` | (return_order_id) | false |


## `orders_return_order`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `order_id` | `varchar(36)` | false |  | `` |
| `return_number` | `varchar(50)` | false |  | `` |
| `customer_id` | `varchar(36)` | true |  | `` |
| `pickup_method` | `varchar(30)` | false |  | `'SCHEDULED_PICKUP'` |
| `status` | `varchar(50)` | false |  | `'RETURN_REQUESTED'` |
| `rejection_reason` | `text` | true |  | `` |
| `rejection_reason_customer` | `text` | true |  | `` |
| `package_condition` | `varchar(50)` | true |  | `` |
| `inspection_condition` | `varchar(50)` | true |  | `` |
| `inspection_notes` | `text` | true |  | `` |
| `refund_amount` | `integer` | false |  | `0` |
| `refund_status` | `varchar(30)` | false |  | `'NOT_REQUESTED'` |
| `refund_method` | `varchar(50)` | true |  | `` |
| `refund_initiated_at` | `timestamp with time zone` | true |  | `` |
| `refund_completed_at` | `timestamp with time zone` | true |  | `` |
| `pickup_scheduled_at` | `timestamp with time zone` | true |  | `` |
| `pickup_address` | `json` | true |  | `` |
| `timeline` | `json` | true |  | `<builtin:list>` |
| `reviewed_by` | `varchar(36)` | true |  | `` |
| `reviewed_at` | `timestamp with time zone` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `order_id` -> `orders_order` (`orders_order.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_orders_return_order_customer_id` | (customer_id) | false |
| `ix_pratikshya_orders_return_order_id` | (id) | false |
| `ix_pratikshya_orders_return_order_order_id` | (order_id) | false |
| `ix_pratikshya_orders_return_order_return_number` | (return_number) | true |
| `ix_pratikshya_orders_return_order_status` | (status) | false |


## `payment_sessions`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `order_id` | `varchar(36)` | false |  | `` |
| `razorpay_order_id` | `varchar(100)` | true |  | `` |
| `razorpay_payment_id` | `varchar(100)` | true |  | `` |
| `razorpay_signature` | `varchar(255)` | true |  | `` |
| `amount_paise` | `integer` | false |  | `` |
| `currency` | `varchar(3)` | false |  | `'INR'` |
| `payment_method` | `varchar(30)` | false |  | `` |
| `status` | `varchar(30)` | false |  | `'CREATED'` |
| `paid_at` | `timestamp with time zone` | true |  | `` |
| `cancelled_at` | `timestamp with time zone` | true |  | `` |
| `expires_at` | `timestamp with time zone` | true |  | `` |
| `failure_reason` | `text` | true |  | `` |
| `failure_code` | `varchar(100)` | true |  | `` |
| `last_webhook_event` | `varchar(100)` | true |  | `` |
| `idempotency_key` | `varchar(100)` | true |  | `` |
| `razorpay_receipt` | `varchar(100)` | true |  | `` |
| `razorpay_notes` | `text` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `order_id` -> `orders_order` (`orders_order.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_payment_sessions_id` | (id) | false |
| `ix_pratikshya_payment_sessions_idempotency_key` | (idempotency_key) | true |
| `ix_pratikshya_payment_sessions_order_id` | (order_id) | false |
| `ix_pratikshya_payment_sessions_razorpay_order_id` | (razorpay_order_id) | true |
| `ix_pratikshya_payment_sessions_razorpay_payment_id` | (razorpay_payment_id) | false |
| `ix_pratikshya_payment_sessions_status` | (status) | false |


## `permissions`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `code` | `varchar(100)` | false |  | `` |
| `name` | `varchar(100)` | false |  | `` |
| `category` | `varchar(50)` | false |  | `` |
| `description` | `varchar(255)` | true |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_permissions_code` | (code) | true |
| `ix_pratikshya_permissions_id` | (id) | false |


## `pricing_price_history`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_pricing_price_history_id` | (id) | false |


## `pricing_product_price`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_pricing_product_price_id` | (id) | false |


## `pricing_tax_rate`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_pricing_tax_rate_id` | (id) | false |


## `role_permissions`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `role_id` | `varchar(36)` | false |  | `` |
| `permission_id` | `varchar(36)` | false |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `permission_id` -> `permissions` (`permissions.id`) ondelete=CASCADE
- `role_id` -> `roles` (`roles.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_role_permissions_id` | (id) | false |
| `ix_pratikshya_role_permissions_permission_id` | (permission_id) | false |
| `ix_pratikshya_role_permissions_role_id` | (role_id) | false |


## `roles`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `name` | `varchar(50)` | false |  | `` |
| `description` | `varchar(255)` | true |  | `` |
| `is_system` | `boolean` | false |  | `False` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_roles_id` | (id) | false |
| `ix_pratikshya_roles_name` | (name) | true |


## `user_roles`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `user_id` | `varchar(36)` | false |  | `` |
| `role_id` | `varchar(36)` | false |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `user_id` -> `users` (`users.id`) ondelete=CASCADE
- `role_id` -> `roles` (`roles.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_user_roles_id` | (id) | false |
| `ix_pratikshya_user_roles_role_id` | (role_id) | false |
| `ix_pratikshya_user_roles_user_id` | (user_id) | false |


## `user_sessions`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `user_id` | `varchar(36)` | false |  | `` |
| `refresh_token_hash` | `varchar(255)` | false |  | `` |
| `user_agent` | `varchar(500)` | true |  | `` |
| `ip_address` | `varchar(45)` | true |  | `` |
| `is_revoked` | `boolean` | false |  | `False` |
| `expires_at` | `timestamp with time zone` | false |  | `` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

- `user_id` -> `users` (`users.id`) ondelete=CASCADE

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_user_sessions_id` | (id) | false |
| `ix_pratikshya_user_sessions_refresh_token_hash` | (refresh_token_hash) | false |
| `ix_pratikshya_user_sessions_user_id` | (user_id) | false |


## `users`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `email` | `varchar(255)` | true |  | `` |
| `phone` | `varchar(20)` | true |  | `` |
| `full_name` | `varchar(255)` | false |  | `` |
| `hashed_password` | `varchar(255)` | true |  | `` |
| `user_type` | `varchar(50)` | false |  | `'customer'` |
| `status` | `varchar(50)` | false |  | `'ACTIVE'` |
| `is_verified` | `boolean` | false |  | `False` |
| `force_password_change` | `boolean` | false |  | `False` |
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_users_email` | (email) | true |
| `ix_pratikshya_users_id` | (id) | false |
| `ix_pratikshya_users_phone` | (phone) | true |


## `variants_attribute`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_variants_attribute_id` | (id) | false |


## `variants_attribute_value`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_variants_attribute_value_id` | (id) | false |


## `variants_product_attribute`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_variants_product_attribute_id` | (id) | false |


## `variants_product_variant`

- **Schema**: `pratikshya`
- **Primary key**: `id`

### Columns

| Column | Type | Nullable | PK | Default (app-side) |
|--------|------|----------|----|--------------------|
| `id` | `varchar(36)` | false | yes | `<callable>` |
| `created_at` | `timestamp with time zone` | false |  | `<callable>` |
| `updated_at` | `timestamp with time zone` | false |  | `<callable>` |

### Foreign keys

_None._

### Unique constraints

_None (unique columns are represented by unique indexes below)._

### Indexes

| Index (name candidates) | Columns | Unique |
|------------------------|---------|--------|
| `ix_pratikshya_variants_product_variant_id` | (id) | false |
