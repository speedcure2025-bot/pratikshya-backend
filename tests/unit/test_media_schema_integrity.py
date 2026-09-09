"""
Media schema relational integrity — REAL PostgreSQL tests.

These tests prove that the constraints created by the Alembic revision
``b6b5dcfb675b_add_media_asset_and_product_media_tables`` are enforced by
PostgreSQL itself, not by the ORM and not by a mock:

    valid product + valid media asset            -> mapping row is accepted
    unknown product_id + valid media_id          -> FK violation  (SQLSTATE 23503)
    valid product_id + unknown media_id          -> FK violation  (SQLSTATE 23503)
    same product_id + same media_id twice        -> unique violation (23505) on
                                                    uq_product_media_asset
    same object_key registered twice             -> unique violation (23505) on
                                                    uq_media_asset_object_key
    DELETE a product                             -> its mappings cascade away
    DELETE a media asset                         -> its mappings cascade away
    DELETE the uploading user                    -> media_media_asset.uploaded_by
                                                    is set to NULL, row survives

It also reflects the migrated tables and asserts the SQLAlchemy models describe
exactly what PostgreSQL actually has, so the model layer cannot silently drift
from the schema.

HOW THE DATABASE IS PROVIDED
----------------------------
The suite never writes to the developer's working database. The shared helper
``app.testing.local_postgres`` reads ``DATABASE_URL``, refuses to continue
unless that URL points at a LOOPBACK host AND at ``pratikshya_local`` (the
disposable development database), and then creates its own throwaway database
``pf_media_it_<random>`` on that same local server. The real Alembic chain is
applied to it with ``alembic upgrade head`` — so every run also re-proves that
a completely fresh PostgreSQL reaches the full schema with no manual SQL. The
throwaway database is dropped when the module finishes.

If no local PostgreSQL is reachable, or ``DATABASE_URL`` points anywhere other
than the local development database, the whole module SKIPS with the reason
printed. A shared or company server is never contacted.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

from app.testing.local_postgres import (
    ThrowawayDatabase,
    alembic_current,
    alembic_heads,
    throwaway_database,
    unavailable_reason,
)

SCHEMA = "pratikshya"

_REASON = unavailable_reason()
if _REASON is not None:
    pytest.skip(
        f"real PostgreSQL media-integrity tests skipped: {_REASON}",
        allow_module_level=True,
    )

import psycopg2  # noqa: E402
import psycopg2.errors  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures — one throwaway database per module, built by the real Alembic chain
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def throwaway() -> Iterator[ThrowawayDatabase]:
    """A private database on the local server, migrated, then dropped."""
    with throwaway_database("media_it") as database:
        yield database


@pytest.fixture()
def connection(throwaway: ThrowawayDatabase) -> Iterator[Any]:
    """A connection that is rolled back and closed after every test."""
    conn = psycopg2.connect(throwaway.dsn)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


# --------------------------------------------------------------------------- #
# Row builders — plain SQL, so the tests exercise PostgreSQL, not the ORM
# --------------------------------------------------------------------------- #
def add_product(conn, product_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.catalog_product (
                id, created_at, updated_at, product_id, name, slug, sku, brand,
                product_type, category, gender, is_featured, is_bestseller, is_new,
                is_limited_edition, is_trending, price, currency, stock,
                availability, inventory_tracked, low_stock_threshold, review_count,
                status, published
            ) VALUES (
                %s, now(), now(), %s, %s, %s, %s, 'Pratikshya Fashon', 'fashion',
                'sarees', 'Women', false, false, false, false, false, 1000, 'INR',
                0, 'in-stock', false, 5, 0, 'DRAFT', false
            )
            """,
            (product_id, product_id, f"Test {product_id}", product_id.lower(), product_id),
        )
    return product_id


def add_user(conn, user_id: str, email: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.users (
                id, created_at, updated_at, email, full_name, user_type, status,
                is_verified, force_password_change
            ) VALUES (%s, now(), now(), %s, 'Media Integrity Tester', 'admin',
                      'ACTIVE', true, false)
            """,
            (user_id, email),
        )
    return user_id


def add_asset(conn, asset_id: str, object_key: str, uploaded_by: Optional[str] = None) -> str:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.media_media_asset (
                id, created_at, updated_at, object_key, mime_type,
                original_filename, file_size, checksum_sha256, uploaded_by
            ) VALUES (
                %s, now(), now(), %s, 'image/avif', %s, 2048,
                encode(sha256(%s::bytea), 'hex'), %s
            )
            """,
            (asset_id, object_key, object_key.rsplit("/", 1)[-1], object_key.encode(), uploaded_by),
        )
    return asset_id


def add_mapping(
    conn,
    mapping_id: str,
    product_id: str,
    media_id: str,
    *,
    role: str = "gallery",
    is_primary: bool = False,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.media_product_media (
                id, created_at, updated_at, product_id, media_id, role,
                sort_order, is_primary
            ) VALUES (%s, now(), now(), %s, %s, %s, 0, %s)
            """,
            (mapping_id, product_id, media_id, role, is_primary),
        )


def _sqlstate(exc: Exception) -> str:
    return getattr(exc, "pgcode", "") or getattr(getattr(exc, "orig", None), "pgcode", "")


def _constraint(exc: Exception) -> str:
    return getattr(exc, "diag", None).constraint_name if getattr(exc, "diag", None) else ""


# --------------------------------------------------------------------------- #
# 1. The migrated schema itself
# --------------------------------------------------------------------------- #
def test_migration_reached_head(throwaway: ThrowawayDatabase) -> None:
    """`alembic upgrade head` on a fresh database lands on the media revision."""
    current = alembic_current(throwaway)
    assert "b6b5dcfb675b" in current, current
    assert "(head)" in current, current


def test_heads_is_exactly_one() -> None:
    """The revision graph has a single head (no branching)."""
    heads = [line for line in alembic_heads().splitlines() if "(head)" in line]
    assert len(heads) == 1, f"expected exactly one head, got {heads}"
    assert "b6b5dcfb675b" in heads[0], heads


def test_tables_primary_keys_and_constraints(connection) -> None:
    """PKs, FKs (+ delete rule), unique constraints and indexes are all real."""
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT t.relname, c.conname, c.contype, c.confdeltype
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = %s
              AND t.relname IN ('media_media_asset', 'media_product_media')
              AND c.contype IN ('p', 'f', 'u')
            ORDER BY t.relname, c.contype, c.conname
            """,
            (SCHEMA,),
        )
        rows = cur.fetchall()

    by_table: Dict[str, List[Tuple[str, str, str]]] = {}
    for table, name, contype, deltype in rows:
        by_table.setdefault(table, []).append((name, contype, deltype))

    # confdeltype is a single space when the constraint is not a foreign key.
    # Primary keys
    assert ("media_media_asset_pkey", "p", " ") in by_table["media_media_asset"]
    assert ("media_product_media_pkey", "p", " ") in by_table["media_product_media"]

    # Unique constraints
    assert ("uq_media_asset_object_key", "u", " ") in by_table["media_media_asset"]
    assert ("uq_product_media_asset", "u", " ") in by_table["media_product_media"]

    # Foreign keys with their ON DELETE rule
    assert ("media_media_asset_uploaded_by_fkey", "f", "n") in by_table["media_media_asset"]
    assert ("media_product_media_media_id_fkey", "f", "c") in by_table["media_product_media"]
    assert ("media_product_media_product_id_fkey", "f", "c") in by_table["media_product_media"]

    # Indexes
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT t.relname, i.relname, ix.indisunique
            FROM pg_index ix
            JOIN pg_class t ON t.oid = ix.indrelid
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = %s
              AND t.relname IN ('media_media_asset', 'media_product_media')
            ORDER BY i.relname
            """,
            (SCHEMA,),
        )
        index_names = {row[1] for row in cur.fetchall()}

    assert {
        "ix_media_media_asset_id",
        "ix_media_media_asset_checksum_sha256",
        "ix_media_product_media_id",
        "ix_media_product_media_media_id",
    } <= index_names, index_names


# --------------------------------------------------------------------------- #
# 2. A valid mapping is accepted
# --------------------------------------------------------------------------- #
def test_valid_product_and_media_mapping_is_accepted(connection) -> None:
    add_product(connection, "PF-INT-0001")
    add_asset(connection, str(uuid.uuid4()), "products/PF-INT-0001/cover.avif")
    mapping_id = str(uuid.uuid4())
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {SCHEMA}.media_media_asset WHERE object_key = %s",
            ("products/PF-INT-0001/cover.avif",),
        )
        media_id = cur.fetchone()[0]

    add_mapping(connection, mapping_id, "PF-INT-0001", media_id, role="COVER", is_primary=True)
    connection.commit()

    with connection.cursor() as cur:
        cur.execute(
            f"""
            SELECT pm.id, pm.product_id, pm.media_id, pm.role, pm.is_primary,
                   pm.sort_order, p.name, ma.object_key
            FROM {SCHEMA}.media_product_media pm
            JOIN {SCHEMA}.catalog_product p   ON p.id  = pm.product_id
            JOIN {SCHEMA}.media_media_asset ma ON ma.id = pm.media_id
            WHERE pm.id = %s
            """,
            (mapping_id,),
        )
        row = cur.fetchone()

    assert row is not None, "the mapping row was not persisted"
    assert row[1] == "PF-INT-0001"
    assert row[2] == media_id
    assert row[3] == "COVER"
    assert row[4] is True
    assert row[7] == "products/PF-INT-0001/cover.avif"


# --------------------------------------------------------------------------- #
# 3. A nonexistent product_id is rejected by PostgreSQL
# --------------------------------------------------------------------------- #
def test_unknown_product_is_rejected_by_foreign_key(connection) -> None:
    add_asset(connection, str(uuid.uuid4()), "products/PF-GHOST/orphan.avif")
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {SCHEMA}.media_media_asset WHERE object_key = %s",
            ("products/PF-GHOST/orphan.avif",),
        )
        media_id = cur.fetchone()[0]

    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as excinfo:
        add_mapping(connection, str(uuid.uuid4()), "PF-DOES-NOT-EXIST", media_id)

    assert _sqlstate(excinfo.value) == "23503"
    assert _constraint(excinfo.value) == "media_product_media_product_id_fkey"
    connection.rollback()

    with connection.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {SCHEMA}.media_product_media "
            "WHERE media_id = %s AND product_id = %s",
            (media_id, "PF-DOES-NOT-EXIST"),
        )
        assert cur.fetchone()[0] == 0, "the rejected mapping must not have been stored"


# --------------------------------------------------------------------------- #
# 4. A nonexistent media_id is rejected by PostgreSQL
# --------------------------------------------------------------------------- #
def test_unknown_media_is_rejected_by_foreign_key(connection) -> None:
    add_product(connection, "PF-INT-0002")

    with pytest.raises(psycopg2.errors.ForeignKeyViolation) as excinfo:
        add_mapping(connection, str(uuid.uuid4()), "PF-INT-0002", str(uuid.uuid4()))

    assert _sqlstate(excinfo.value) == "23503"
    assert _constraint(excinfo.value) == "media_product_media_media_id_fkey"
    connection.rollback()

    with connection.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {SCHEMA}.media_product_media WHERE product_id = %s",
            ("PF-INT-0002",),
        )
        assert cur.fetchone()[0] == 0, "the rejected mapping must not have been stored"


# --------------------------------------------------------------------------- #
# 5. The same product+media pair cannot be mapped twice
# --------------------------------------------------------------------------- #
def test_duplicate_mapping_is_rejected_by_unique_constraint(connection) -> None:
    add_product(connection, "PF-INT-0003")
    add_asset(connection, str(uuid.uuid4()), "products/PF-INT-0003/one.avif")
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {SCHEMA}.media_media_asset WHERE object_key = %s",
            ("products/PF-INT-0003/one.avif",),
        )
        media_id = cur.fetchone()[0]

    add_mapping(connection, str(uuid.uuid4()), "PF-INT-0003", media_id, role="gallery")
    connection.commit()

    with pytest.raises(psycopg2.errors.UniqueViolation) as excinfo:
        add_mapping(connection, str(uuid.uuid4()), "PF-INT-0003", media_id, role="COVER")

    assert _sqlstate(excinfo.value) == "23505"
    assert _constraint(excinfo.value) == "uq_product_media_asset"
    connection.rollback()

    with connection.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {SCHEMA}.media_product_media WHERE product_id = %s",
            ("PF-INT-0003",),
        )
        assert cur.fetchone()[0] == 1, "the duplicate mapping must not have been stored"


def test_same_object_key_cannot_be_registered_twice(connection) -> None:
    """`uq_media_asset_object_key` keeps one row per stored object."""
    add_asset(connection, str(uuid.uuid4()), "products/PF-INT-0004/dup.avif")
    connection.commit()

    with pytest.raises(psycopg2.errors.UniqueViolation) as excinfo:
        add_asset(connection, str(uuid.uuid4()), "products/PF-INT-0004/dup.avif")

    assert _sqlstate(excinfo.value) == "23505"
    assert _constraint(excinfo.value) == "uq_media_asset_object_key"


# --------------------------------------------------------------------------- #
# 6. ON DELETE behaviour matches the documented convention
# --------------------------------------------------------------------------- #
def test_deleting_a_product_cascades_to_its_mappings(connection) -> None:
    add_product(connection, "PF-INT-0005")
    asset_id = add_asset(connection, str(uuid.uuid4()), "products/PF-INT-0005/a.avif")
    add_mapping(connection, str(uuid.uuid4()), "PF-INT-0005", asset_id)
    connection.commit()

    with connection.cursor() as cur:
        cur.execute(
            f"DELETE FROM {SCHEMA}.catalog_product WHERE id = %s", ("PF-INT-0005",)
        )
        cur.execute(
            f"SELECT count(*) FROM {SCHEMA}.media_product_media WHERE product_id = %s",
            ("PF-INT-0005",),
        )
        assert cur.fetchone()[0] == 0, "product delete must cascade its mappings"
        # The asset itself survives: it is not owned by the product.
        cur.execute(
            f"SELECT count(*) FROM {SCHEMA}.media_media_asset WHERE id = %s", (asset_id,)
        )
        assert cur.fetchone()[0] == 1


def test_deleting_a_media_asset_cascades_to_its_mappings(connection) -> None:
    add_product(connection, "PF-INT-0006")
    asset_id = add_asset(connection, str(uuid.uuid4()), "products/PF-INT-0006/a.avif")
    add_mapping(connection, str(uuid.uuid4()), "PF-INT-0006", asset_id)
    connection.commit()

    with connection.cursor() as cur:
        cur.execute(
            f"DELETE FROM {SCHEMA}.media_media_asset WHERE id = %s", (asset_id,)
        )
        cur.execute(
            f"SELECT count(*) FROM {SCHEMA}.media_product_media WHERE media_id = %s",
            (asset_id,),
        )
        assert cur.fetchone()[0] == 0, "asset delete must cascade its mappings"
        cur.execute(
            f"SELECT count(*) FROM {SCHEMA}.catalog_product WHERE id = %s",
            ("PF-INT-0006",),
        )
        assert cur.fetchone()[0] == 1, "the product must survive"


def test_deleting_the_uploader_nulls_uploaded_by_and_keeps_the_asset(connection) -> None:
    user_id = add_user(connection, str(uuid.uuid4()), "integrity-uploader@example.test")
    asset_id = add_asset(
        connection, str(uuid.uuid4()), "products/PF-INT-0007/a.avif", uploaded_by=user_id
    )
    connection.commit()

    with connection.cursor() as cur:
        cur.execute(f"DELETE FROM {SCHEMA}.users WHERE id = %s", (user_id,))
        cur.execute(
            f"SELECT uploaded_by FROM {SCHEMA}.media_media_asset WHERE id = %s",
            (asset_id,),
        )
        row = cur.fetchone()
        assert row is not None, "the asset row must survive the user delete"
        assert row[0] is None, "uploaded_by must be SET NULL, not cascade or block"


# --------------------------------------------------------------------------- #
# 7. The SQLAlchemy models describe exactly what PostgreSQL has
# --------------------------------------------------------------------------- #
def test_models_match_the_migrated_schema(throwaway: ThrowawayDatabase) -> None:
    from sqlalchemy import create_engine, inspect

    from app.models.base import Base
    import app.models  # noqa: F401  registers every mapped class

    sync_url = throwaway.url
    if "+asyncpg" in sync_url:
        sync_url = sync_url.replace("+asyncpg", "+psycopg2", 1)
    engine = create_engine(sync_url)
    try:
        inspector = inspect(engine)
        for table_name in ("media_media_asset", "media_product_media"):
            actual_columns = {
                col["name"]: col for col in inspector.get_columns(table_name, schema=SCHEMA)
            }
            model_table = Base.metadata.tables[f"{SCHEMA}.{table_name}"]

            # columns + nullability
            assert set(actual_columns) == {c.name for c in model_table.columns}, table_name
            for column in model_table.columns:
                assert bool(actual_columns[column.name]["nullable"]) == column.nullable, (
                    f"{table_name}.{column.name} nullability"
                )

            # primary key
            pk = inspector.get_pk_constraint(table_name, schema=SCHEMA)
            assert pk["constrained_columns"] == [c.name for c in model_table.primary_key]

            # unique constraints (compared as column sets)
            actual_unique = {
                tuple(sorted(u["column_names"]))
                for u in inspector.get_unique_constraints(table_name, schema=SCHEMA)
            }
            model_unique = {
                tuple(sorted(c.name for c in constraint.columns))
                for constraint in model_table.constraints
                if constraint.__class__.__name__ == "UniqueConstraint"
            }
            assert model_unique <= actual_unique, (table_name, model_unique, actual_unique)

            # foreign keys, including the ON DELETE rule
            actual_fks = {
                (tuple(fk["constrained_columns"]), fk["referred_table"], fk["options"].get("ondelete"))
                for fk in inspector.get_foreign_keys(table_name, schema=SCHEMA)
            }
            model_fks = {
                (
                    tuple(sorted(column.name for column in constraint.columns)),
                    constraint.referred_table.name,
                    constraint.ondelete,
                )
                for constraint in model_table.constraints
                if constraint.__class__.__name__ == "ForeignKeyConstraint"
            }
            normalised_actual = {
                (tuple(sorted(cols)), table, rule) for cols, table, rule in actual_fks
            }
            assert model_fks == normalised_actual, (table_name, model_fks, normalised_actual)
    finally:
        engine.dispose()
