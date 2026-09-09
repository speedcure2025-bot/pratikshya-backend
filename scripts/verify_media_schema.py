#!/usr/bin/env python3
"""READ-ONLY verification of the media schema in a LOCAL PostgreSQL database.

This is the metadata counterpart to the Alembic revision
``b6b5dcfb675b_add_media_asset_and_product_media_tables``. It answers, from
``pg_catalog`` / ``information_schema`` only:

  * which database is actually connected (it must be the disposable local one),
  * the media tables and their columns with nullability and defaults,
  * the real PostgreSQL PRIMARY KEYs,
  * the real PostgreSQL FOREIGN KEYs *and their ON DELETE rule*,
  * the UNIQUE constraints,
  * the indexes.

Nothing here writes. The session is forced into
``SET default_transaction_read_only = on`` and the transaction is rolled back,
so the script cannot alter the database even if a query were mistyped.

SAFETY — the company database is never a valid target
-----------------------------------------------------
The script refuses to run (exit 2) unless the resolved connection is BOTH on a
loopback host AND pointed at ``pratikshya_local``. Any other host or database
name — including the company server — is rejected before a connection is
opened, and no ``DATABASE_URL`` password is ever printed.

USAGE (from ``backend/``)
-------------------------
    python scripts/verify_media_schema.py
    python scripts/verify_media_schema.py --json /tmp/media_schema.json

Exit codes: 0 = every check passed, 1 = a check failed, 2 = refused to run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "pratikshya"
REQUIRED_DATABASE = "pratikshya_local"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

#: confdeltype codes from pg_constraint.
DELETE_RULE = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}

MEDIA_TABLES = (
    "media_media_asset",
    "media_product_media",
    "media_marketing_media",
    "media_media_review",
)

# ── Expected shape -----------------------------------------------------------
# (column, pg type, nullable) — the exact contract of the Alembic revision.
EXPECTED_ASSET_COLUMNS: List[Tuple[str, str, bool]] = [
    ("id", "character varying", False),
    ("created_at", "timestamp with time zone", False),
    ("updated_at", "timestamp with time zone", False),
    ("object_key", "character varying", False),
    ("storage_provider", "character varying", False),
    ("media_type", "character varying", False),
    ("mime_type", "character varying", False),
    ("original_filename", "character varying", False),
    ("file_size", "integer", False),
    ("checksum_sha256", "character varying", False),
    ("width", "integer", True),
    ("height", "integer", True),
    ("title", "character varying", True),
    ("alt_text", "text", True),
    ("caption", "text", True),
    ("status", "character varying", False),
    ("scope", "character varying", False),
    ("uploaded_by", "character varying", True),
]

EXPECTED_MAPPING_COLUMNS: List[Tuple[str, str, bool]] = [
    ("id", "character varying", False),
    ("created_at", "timestamp with time zone", False),
    ("updated_at", "timestamp with time zone", False),
    ("product_id", "character varying", False),
    ("media_id", "character varying", False),
    ("role", "character varying", False),
    ("sort_order", "integer", False),
    ("is_primary", "boolean", False),
    ("assigned_by", "character varying", True),
    ("assignment_note", "character varying", True),
]

EXPECTED_FKS = [
    # (table, column, referenced table, referenced column, ON DELETE)
    ("media_media_asset", "uploaded_by", "users", "id", "SET NULL"),
    ("media_product_media", "product_id", "catalog_product", "id", "CASCADE"),
    ("media_product_media", "media_id", "media_media_asset", "id", "CASCADE"),
]

EXPECTED_UNIQUE = [
    ("media_media_asset", "uq_media_asset_object_key", ["object_key"]),
    ("media_product_media", "uq_product_media_asset", ["media_id", "product_id"]),
]

EXPECTED_INDEXES = [
    ("media_media_asset", ["checksum_sha256"], False),
    ("media_media_asset", ["id"], False),
    ("media_media_asset", ["object_key"], True),
    ("media_product_media", ["id"], False),
    ("media_product_media", ["media_id"], False),
    ("media_product_media", ["media_id", "product_id"], True),
]


# --------------------------------------------------------------------------- #
# Connection target — resolved and safety-checked BEFORE connecting
# --------------------------------------------------------------------------- #
def _refuse(reason: str) -> "SystemExit":
    """Refuse to run. Exit code 2 == 'this target is not the local dev database'."""
    print(f"REFUSED: {reason}", file=sys.stderr)
    return SystemExit(2)


def _parse_env_file(path: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not os.path.exists(path):
        return result
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().split(" #", 1)[0].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            result[key.strip()] = value
    return result


def _redact(value: str) -> str:
    return re.sub(r"(://[^:/@]+):([^@/]+)@", r"\1:***@", value)


def resolve_target(backend_root: str) -> Tuple[str, str, Dict[str, Optional[str]]]:
    """Return (psycopg2 dsn, safe label, parsed parts), or raise SystemExit(2)."""
    env = dict(os.environ)
    env_file = _parse_env_file(os.path.join(backend_root, ".env"))
    # The process environment wins, exactly like alembic/env.py.
    merged = {**env_file, **{k: v for k, v in env.items() if v is not None}}

    url = merged.get("DATABASE_URL")
    host = port = database = None
    if url:
        m = re.match(
            r"^(?:postgresql|postgres)(?:\+[a-z0-9]+)?://"
            r"(?:(?P<user>[^:/@]+)(?::[^@/]*)?@)?"
            r"(?P<host>[^/:?]+)?(?::(?P<port>\d+))?(?:/(?P<db>[^?]*))?",
            url.strip(),
            re.IGNORECASE,
        )
        if not m:
            raise _refuse(f"could not parse DATABASE_URL {_redact(url)!r}")
        host = m.group("host") or "localhost"
        port = m.group("port") or "5432"
        database = m.group("db") or None
        dsn = re.sub(r"^([a-z]+)\+([a-z]+)://", r"\1://", url.strip(), flags=re.IGNORECASE)
        label = _redact(dsn)
    else:
        host = merged.get("PGHOST", "localhost")
        port = merged.get("PGPORT", "5432")
        database = merged.get("PGDATABASE", merged.get("POSTGRES_DB"))
        user = merged.get("PGUSER", merged.get("POSTGRES_USER", "postgres"))
        password = merged.get("PGPASSWORD", merged.get("POSTGRES_PASSWORD", ""))
        dsn = f"host={host} port={port} dbname={database} user={user}"
        if password:
            dsn += f" password={password}"
        label = f"host={host} port={port} dbname={database} user={user}"

    if host not in LOOPBACK_HOSTS:
        raise _refuse(
            f"DATABASE_URL host is {host!r}, not a loopback address. This script "
            "only ever inspects the disposable local database — it will not "
            "connect to a shared or company server."
        )
    if database != REQUIRED_DATABASE:
        raise _refuse(
            f"database is {database!r}, expected {REQUIRED_DATABASE!r}. Point "
            "DATABASE_URL at the disposable local database first."
        )
    return dsn, label, {"host": host, "port": port, "database": database}


# --------------------------------------------------------------------------- #
# Metadata queries (all read-only)
# --------------------------------------------------------------------------- #
COLUMNS_SQL = """
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema = %s AND table_name = %s
    ORDER BY ordinal_position
"""

TABLES_SQL = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = %s AND table_name LIKE %s
    ORDER BY table_name
"""

PK_SQL = """
    SELECT a.attname
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
    WHERE c.contype = 'p' AND n.nspname = %s AND t.relname = %s
    ORDER BY k.ord
"""

FK_SQL = """
    SELECT
        t.relname                                   AS table_name,
        a.attname                                   AS column_name,
        ft.relname                                  AS referenced_table,
        fa.attname                                  AS referenced_column,
        c.confdeltype                               AS delete_rule_code,
        c.conname                                   AS constraint_name
    FROM pg_constraint c
    JOIN pg_class t  ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN pg_class ft ON ft.oid = c.confrelid
    JOIN pg_attribute a  ON a.attrelid = c.conrelid  AND a.attnum = c.conkey[1]
    JOIN pg_attribute fa ON fa.attrelid = c.confrelid AND fa.attnum = c.confkey[1]
    WHERE c.contype = 'f' AND n.nspname = %s AND t.relname = %s
    ORDER BY c.conname
"""

UNIQUE_SQL = """
    SELECT c.conname, array_agg(a.attname ORDER BY k.ord)
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
    WHERE c.contype = 'u' AND n.nspname = %s AND t.relname = %s
    GROUP BY c.conname
    ORDER BY c.conname
"""

INDEX_SQL = """
    SELECT i.relname AS index_name,
           array_agg(a.attname ORDER BY k.ord) AS columns,
           ix.indisunique AS is_unique
    FROM pg_index ix
    JOIN pg_class t  ON t.oid = ix.indrelid
    JOIN pg_class i  ON i.oid = ix.indexrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord)
    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
    WHERE n.nspname = %s AND t.relname = %s
    GROUP BY i.relname, ix.indisunique
    ORDER BY i.relname
"""


class Reporter:
    def __init__(self) -> None:
        self.failures: List[str] = []
        self.report: Dict[str, Any] = {}

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            self.failures.append(f"{label}: {detail}")
        return ok

    @staticmethod
    def section(title: str) -> None:
        print(f"\n── {title} " + "─" * max(0, 66 - len(title)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        default=None,
        help="Also write the collected metadata to this JSON file.",
    )
    args = parser.parse_args()

    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dsn, label, parts = resolve_target(backend_root)

    try:
        import psycopg2
    except ImportError:  # pragma: no cover - dependency guard
        print("ERROR: psycopg2 is required (pip install psycopg2-binary)", file=sys.stderr)
        return 2

    print("=" * 78)
    print("MEDIA SCHEMA VERIFICATION (read-only)")
    print("=" * 78)
    print(f"target: {label}")

    connection = psycopg2.connect(dsn)
    try:
        connection.autocommit = False
        with connection.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")

            cur.execute("SELECT current_database(), inet_server_port(), version()")
            db_name, server_port, version = cur.fetchone()
            reporter = Reporter()
            reporter.section("connection")
            print(f"  current_database() = {db_name}")
            print(f"  server port        = {server_port}")
            print(f"  server             = {version.split(',')[0]}")

            reporter.report["connection"] = {
                "current_database": db_name,
                "host": parts["host"],
                "port": str(server_port),
                "requested_database": parts["database"],
            }
            reporter.check(
                db_name == REQUIRED_DATABASE,
                f"current_database() == {REQUIRED_DATABASE!r}",
                f"actual {db_name!r}",
            )

            # ── tables ────────────────────────────────────────────────────────
            cur.execute(TABLES_SQL, (SCHEMA, "media_%"))
            present = [row[0] for row in cur.fetchall()]
            reporter.section("tables in schema 'pratikshya'")
            for name in present:
                print(f"  · {SCHEMA}.{name}")
            reporter.report["tables"] = present
            for name in MEDIA_TABLES:
                reporter.check(name in present, f"table {SCHEMA}.{name} exists")

            # ── columns ───────────────────────────────────────────────────────
            collected: Dict[str, Any] = {}
            for table, expected in (
                ("media_media_asset", EXPECTED_ASSET_COLUMNS),
                ("media_product_media", EXPECTED_MAPPING_COLUMNS),
            ):
                cur.execute(COLUMNS_SQL, (SCHEMA, table))
                rows = cur.fetchall()
                actual = {r[0]: (r[1], r[2].strip().upper() != "NO", r[3]) for r in rows}
                collected[table] = [
                    {
                        "column": r[0],
                        "type": r[1],
                        "nullable": r[2].strip().upper() != "NO",
                        "default": r[3],
                    }
                    for r in rows
                ]
                reporter.section(f"{table} columns ({len(rows)})")
                for name, datatype, nullable, default in rows:
                    flag = "NULL" if nullable.strip().upper() == "YES" else "NOT NULL"
                    suffix = f" DEFAULT {default}" if default else ""
                    print(f"  {name:<20} {datatype:<28} {flag:<9}{suffix}")

                reporter.check(
                    [r[0] for r in rows] == [c[0] for c in expected],
                    f"{table}: column set matches the migration",
                    f"actual {[r[0] for r in rows]}",
                )
                for name, datatype, nullable in expected:
                    if name not in actual:
                        reporter.check(False, f"{table}.{name} present")
                        continue
                    got_type, got_nullable, _ = actual[name]
                    reporter.check(
                        got_type == datatype,
                        f"{table}.{name} type == {datatype}",
                        f"actual {got_type}",
                    )
                    reporter.check(
                        got_nullable == nullable,
                        f"{table}.{name} nullable == {nullable}",
                        f"actual {got_nullable}",
                    )

            reporter.report["columns"] = collected

            # ── primary keys ──────────────────────────────────────────────────
            reporter.section("primary keys")
            pks: Dict[str, List[str]] = {}
            for table in MEDIA_TABLES:
                cur.execute(PK_SQL, (SCHEMA, table))
                pks[table] = [row[0] for row in cur.fetchall()]
                print(f"  {table:<24} PK {pks[table] or '(none)'}")
            reporter.report["primary_keys"] = pks
            for table in MEDIA_TABLES:
                reporter.check(
                    pks[table] == ["id"], f"{table} has a real PRIMARY KEY on (id)"
                )

            # ── foreign keys ──────────────────────────────────────────────────
            reporter.section("foreign keys (PostgreSQL-enforced)")
            fks: List[Dict[str, str]] = []
            for table in ("media_media_asset", "media_product_media"):
                cur.execute(FK_SQL, (SCHEMA, table))
                for tname, col, rtable, rcol, code, cname in cur.fetchall():
                    rule = DELETE_RULE.get(code, code)
                    fks.append(
                        {
                            "table": tname,
                            "column": col,
                            "references": f"{rtable}.{rcol}",
                            "on_delete": rule,
                            "constraint": cname,
                        }
                    )
                    print(
                        f"  {tname}.{col} -> {SCHEMA}.{rtable}.{rcol} "
                        f"ON DELETE {rule}  ({cname})"
                    )
            reporter.report["foreign_keys"] = fks
            for table, col, rtable, rcol, rule in EXPECTED_FKS:
                match = next(
                    (
                        f
                        for f in fks
                        if f["table"] == table
                        and f["column"] == col
                        and f["references"] == f"{rtable}.{rcol}"
                    ),
                    None,
                )
                reporter.check(
                    match is not None,
                    f"FK {table}.{col} -> {rtable}.{rcol} exists",
                )
                if match:
                    reporter.check(
                        match["on_delete"] == rule,
                        f"FK {table}.{col} ON DELETE {rule}",
                        f"actual {match['on_delete']}",
                    )
            reporter.check(
                len(fks) == len(EXPECTED_FKS),
                "no unexpected extra foreign keys on the media tables",
                f"actual {len(fks)}",
            )

            # ── unique constraints ────────────────────────────────────────────
            reporter.section("unique constraints")
            uniques: Dict[str, Dict[str, List[str]]] = {}
            for table in ("media_media_asset", "media_product_media"):
                cur.execute(UNIQUE_SQL, (SCHEMA, table))
                uniques[table] = {name: cols for name, cols in cur.fetchall()}
                for name, cols in uniques[table].items():
                    print(f"  {table}: {name} UNIQUE ({', '.join(cols)})")
            reporter.report["unique_constraints"] = uniques
            for table, name, cols in EXPECTED_UNIQUE:
                got = uniques.get(table, {}).get(name)
                reporter.check(
                    got is not None and sorted(got) == sorted(cols),
                    f"{table}.{name} UNIQUE ({', '.join(sorted(cols))})",
                    f"actual {got}",
                )

            # ── indexes ───────────────────────────────────────────────────────
            reporter.section("indexes")
            indexes: Dict[str, List[Dict[str, Any]]] = {}
            for table in ("media_media_asset", "media_product_media"):
                cur.execute(INDEX_SQL, (SCHEMA, table))
                indexes[table] = [
                    {"name": n, "columns": c, "unique": bool(u)} for n, c, u in cur.fetchall()
                ]
                for entry in indexes[table]:
                    kind = "UNIQUE INDEX" if entry["unique"] else "INDEX"
                    print(
                        f"  {table}: {entry['name']:<40} {kind} "
                        f"({', '.join(entry['columns'])})"
                    )
            reporter.report["indexes"] = indexes
            for table, cols, unique in EXPECTED_INDEXES:
                found = any(
                    sorted(i["columns"]) == sorted(cols) and i["unique"] == unique
                    for i in indexes.get(table, [])
                )
                reporter.check(
                    found,
                    f"{table}: {'unique ' if unique else ''}index on ({', '.join(cols)})",
                )
            reporter.check(
                all(
                    sorted(i["columns"]) != ["product_id"]
                    for i in indexes.get("media_product_media", [])
                ),
                "media_product_media carries no redundant single-column product_id index "
                "(covered by uq_product_media_asset)",
            )

        connection.rollback()
    finally:
        connection.close()

    print("\n" + "=" * 78)
    if reporter.failures:
        print(f"MEDIA SCHEMA VERIFICATION: FAILED ({len(reporter.failures)} check(s))")
        for failure in reporter.failures:
            print(f"  - {failure}")
        print("=" * 78)
        return 1
    print("MEDIA SCHEMA VERIFICATION: ALL CHECKS PASSED")
    print(f"database inspected (read-only): {db_name} on {parts['host']}")
    print("=" * 78)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(reporter.report, handle, indent=2, default=str)
        print(f"metadata written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
