-- ============================================================================
-- Idempotent migration script for Pratikshya Fashon
-- Applies all remaining schema changes that Alembic couldn't apply due to
-- missing stub tables on this RDS database.
--
-- Covers migrations:
--   b6b5dcfb675b  (media_asset + product_media tables)
--   r1a2b3c4d5e6  (account_level columns on users)
--   s2a3b4c5d6e7  (employee_leave + attendance guard)
--   t3c4d5e6f7a8  (ensure account_level — no-op if r1 worked)
-- ============================================================================

SET search_path TO pratikshya, public;

-- ── b6b5dcfb675b: media_media_asset ──────────────────────────────────────────

-- Drop FK from marketing_media if it exists (so we can safely recreate)
ALTER TABLE IF EXISTS pratikshya.media_marketing_media
    DROP CONSTRAINT IF EXISTS media_marketing_media_media_asset_id_fkey;

-- Drop stubs if they exist
DROP TABLE IF EXISTS pratikshya.media_product_media CASCADE;
DROP TABLE IF EXISTS pratikshya.media_media_asset CASCADE;

-- Create the real media_media_asset table
CREATE TABLE IF NOT EXISTS pratikshya.media_media_asset (
    id              VARCHAR(36) NOT NULL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    object_key      VARCHAR(512) NOT NULL,
    storage_provider VARCHAR(20) NOT NULL DEFAULT 'local',
    media_type      VARCHAR(30) NOT NULL DEFAULT 'image',
    mime_type       VARCHAR(100) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    file_size       INTEGER NOT NULL,
    checksum_sha256 VARCHAR(64) NOT NULL,
    width           INTEGER,
    height          INTEGER,
    title           VARCHAR(255),
    alt_text        TEXT,
    caption         TEXT,
    status          VARCHAR(30) NOT NULL DEFAULT 'uploaded',
    scope           VARCHAR(30) NOT NULL DEFAULT 'product',
    uploaded_by     VARCHAR(36),
    CONSTRAINT fk_media_asset_uploaded_by
        FOREIGN KEY (uploaded_by) REFERENCES pratikshya.users(id) ON DELETE SET NULL,
    CONSTRAINT uq_media_asset_object_key UNIQUE (object_key)
);

CREATE INDEX IF NOT EXISTS ix_media_media_asset_id
    ON pratikshya.media_media_asset (id);
CREATE INDEX IF NOT EXISTS ix_media_media_asset_checksum_sha256
    ON pratikshya.media_media_asset (checksum_sha256);

-- Re-add the FK from marketing_media -> media_media_asset
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'pratikshya' AND table_name = 'media_marketing_media'
    ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'media_marketing_media_media_asset_id_fkey'
              AND table_schema = 'pratikshya'
        ) THEN
            ALTER TABLE pratikshya.media_marketing_media
                ADD CONSTRAINT media_marketing_media_media_asset_id_fkey
                FOREIGN KEY (media_asset_id) REFERENCES pratikshya.media_media_asset(id)
                ON DELETE SET NULL;
        END IF;
    END IF;
END $$;

-- Create the real media_product_media table
CREATE TABLE IF NOT EXISTS pratikshya.media_product_media (
    id              VARCHAR(36) NOT NULL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    product_id      VARCHAR(36) NOT NULL,
    media_id        VARCHAR(36) NOT NULL,
    role            VARCHAR(30) NOT NULL DEFAULT 'gallery',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    is_primary      BOOLEAN NOT NULL DEFAULT FALSE,
    assigned_by     VARCHAR(36),
    assignment_note VARCHAR(500),
    CONSTRAINT fk_product_media_product_id
        FOREIGN KEY (product_id) REFERENCES pratikshya.catalog_product(id) ON DELETE CASCADE,
    CONSTRAINT fk_product_media_media_id
        FOREIGN KEY (media_id) REFERENCES pratikshya.media_media_asset(id) ON DELETE CASCADE,
    CONSTRAINT uq_product_media_asset UNIQUE (product_id, media_id)
);

CREATE INDEX IF NOT EXISTS ix_media_product_media_id
    ON pratikshya.media_product_media (id);
CREATE INDEX IF NOT EXISTS ix_media_product_media_media_id
    ON pratikshya.media_product_media (media_id);


-- ── r1a2b3c4d5e6: account_level columns on users ────────────────────────────

ALTER TABLE pratikshya.users
    ADD COLUMN IF NOT EXISTS account_level VARCHAR(20),
    ADD COLUMN IF NOT EXISTS permission_mode VARCHAR(10) DEFAULT 'role',
    ADD COLUMN IF NOT EXISTS custom_permissions JSONB;

COMMENT ON COLUMN pratikshya.users.account_level IS
    'Staff hierarchy level: SUPER_ADMIN | ADMIN | SUPER_EMPLOYEE | EMPLOYEE; NULL for customers';
COMMENT ON COLUMN pratikshya.users.permission_mode IS
    'role = capability set comes from assigned roles; custom = custom_permissions override';
COMMENT ON COLUMN pratikshya.users.custom_permissions IS
    'Explicit grant list (canonical capability or legacy granular codes) when permission_mode=custom';

-- Backfill: admins with SUPER_ADMIN role
UPDATE pratikshya.users
   SET account_level = 'SUPER_ADMIN'
 WHERE user_type = 'admin'
   AND account_level IS NULL
   AND id IN (
       SELECT ur.user_id
         FROM pratikshya.user_roles ur
         JOIN pratikshya.roles r ON r.id = ur.role_id
        WHERE r.name = 'SUPER_ADMIN'
   );

-- Backfill: remaining admins
UPDATE pratikshya.users
   SET account_level = 'ADMIN'
 WHERE user_type = 'admin'
   AND account_level IS NULL;

-- Backfill: employees with SUPER_EMPLOYEE role
UPDATE pratikshya.users
   SET account_level = 'SUPER_EMPLOYEE'
 WHERE user_type = 'employee'
   AND account_level IS NULL
   AND id IN (
       SELECT ur.user_id
         FROM pratikshya.user_roles ur
         JOIN pratikshya.roles r ON r.id = ur.role_id
        WHERE r.name = 'SUPER_EMPLOYEE'
   );

-- Backfill: remaining employees
UPDATE pratikshya.users
   SET account_level = 'EMPLOYEE'
 WHERE user_type = 'employee'
   AND account_level IS NULL;

-- Backfill: permission_mode for staff
UPDATE pratikshya.users
   SET permission_mode = 'role'
 WHERE permission_mode IS NULL
   AND user_type IN ('admin', 'employee');

-- Add force_password_change if missing (used by User ORM)
ALTER TABLE pratikshya.users
    ADD COLUMN IF NOT EXISTS force_password_change BOOLEAN DEFAULT FALSE;


-- ── s2a3b4c5d6e7: employee_leave + attendance guard ─────────────────────────

CREATE TABLE IF NOT EXISTS pratikshya.employee_leave (
    id              VARCHAR(36) NOT NULL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    employee_id     VARCHAR(36) NOT NULL,
    leave_type      VARCHAR(20) NOT NULL DEFAULT 'OTHER',
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    days            INTEGER NOT NULL DEFAULT 1,
    reason          TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at     TIMESTAMPTZ,
    reviewed_by     VARCHAR(36),
    review_note     TEXT,
    CONSTRAINT fk_leave_employee_id
        FOREIGN KEY (employee_id) REFERENCES pratikshya.employee_profiles(id) ON DELETE CASCADE,
    CONSTRAINT fk_leave_reviewed_by
        FOREIGN KEY (reviewed_by) REFERENCES pratikshya.users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_employee_leave_id
    ON pratikshya.employee_leave (id);
CREATE INDEX IF NOT EXISTS ix_employee_leave_employee_id
    ON pratikshya.employee_leave (employee_id);
CREATE INDEX IF NOT EXISTS ix_employee_leave_start_date
    ON pratikshya.employee_leave (start_date);
CREATE INDEX IF NOT EXISTS ix_employee_leave_status
    ON pratikshya.employee_leave (status);

-- Attendance dedupe: keep the most recently updated row per (employee, date)
DELETE FROM pratikshya.employee_attendance a
USING pratikshya.employee_attendance b
WHERE a.employee_id = b.employee_id
  AND a.attendance_date = b.attendance_date
  AND (a.updated_at < b.updated_at
       OR (a.updated_at = b.updated_at AND a.id < b.id));

-- Unique guard for attendance
CREATE UNIQUE INDEX IF NOT EXISTS uq_employee_attendance_employee_date
    ON pratikshya.employee_attendance (employee_id, attendance_date);


-- ── Done ─────────────────────────────────────────────────────────────────────
-- After running this script, stamp alembic:
--   python -m alembic stamp head
