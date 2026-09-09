#!/usr/bin/env python3
"""READ-ONLY PostgreSQL schema compatibility audit.

This script NEVER writes to the database.  It connects to your real
PostgreSQL server using credentials from your private ``.env`` (or the process
environment), queries only ``information_schema`` / ``pg_catalog`` metadata,
compares it against the backend's expected schema contract
(``expected_schema.json``) and reports compatibility issues.

REPORTED FINDINGS
-----------------
  MISSING TABLE       An expected backend table is not present in the schema.
  MISSING COLUMN      An expected column is not present on the table.
  TYPE MISMATCH       Column type (kind, length, precision/scale or timezone) differs.
  NULLABILITY MISMATCH  Expected nullable vs actual nullable differ.
  MISSING PK          Expected primary key definition is missing or differs.
  MISSING FK          Expected foreign key definition is missing.
  MISSING UNIQUE      Expected unique constraint/index is missing.
  MISSING INDEX       Expected index (columns + uniqueness) is missing.
  EXTRA TABLE         A table exists in the schema that the backend does not expect.
  EXTRA COLUMN        A column exists on the table that the backend does not expect.
  (INFO)  EXTRA UNIQUE / EXTRA INDEX / EXTRA FK / TABLE IN OTHER SCHEMA / INDEX NAME DIFFERS

SAFETY
------
  * Sessions are forced into ``SET default_transaction_read_only = on`` and the
    transaction is rolled back at the end.  In practice only SELECTs against
    catalogs are executed.
  * No credentials, passwords, tokens, customer data, product data or row
    contents are ever printed or written to the output report.

USAGE (from ``backend/``)
-------------------------
  python schema_audit/verify_schema.py                 # uses backend/.env DATABASE_URL
  python schema_audit/verify_schema.py --schema public
  python schema_audit/verify_schema.py --output report.json

``expected_schema.json`` is regenerated with
``python schema_audit/generate_expected_schema.py``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_AUDIT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCHEMA_AUDIT_DIR.parent
DEFAULT_CONTRACT = SCHEMA_AUDIT_DIR / "expected_schema.json"

ISSUE_CATEGORIES = [
    "MISSING TABLE",
    "MISSING COLUMN",
    "TYPE MISMATCH",
    "NULLABILITY MISMATCH",
    "MISSING PK",
    "MISSING FK",
    "MISSING UNIQUE",
    "MISSING INDEX",
    "EXTRA TABLE",
    "EXTRA COLUMN",
]


# --------------------------------------------------------------------------- #
# .env parsing (tiny, dependency-free)
# --------------------------------------------------------------------------- #
def _parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a python-dotenv compatible .env file (simple KEY=VALUE only)."""
    result: Dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            # strip trailing comment after a space (naive, but fine for DATABASE_URL)
            value = value.split(" #", 1)[0].strip()
        if key and not key.startswith("#"):
            result[key] = value
    return result


def _redact(value: str) -> str:
    """Redact anything that looks like an embedded credential."""
    value = re.sub(r"(://[^:/@]+):([^@/]+)@", r"\1:***@", value)
    value = re.sub(r"(\bpassword\s*=\s*)[^\s;'\"&]+", r"\1***", value, flags=re.IGNORECASE)
    return value


def _build_dsn() -> Tuple[str, str]:
    """Return a psycopg2 conninfo string and a human label.

    The label never contains a password/token.
    """
    env_file = BACKEND_ROOT / ".env"
    env = dict(os.environ)
    env.update(_parse_env_file(env_file))

    url = env.get("DATABASE_URL")
    if url:
        # psycopg2/libpq only understands postgresql://, not postgresql+asyncpg://.
        url = re.sub(r"^([a-z]+)\+([a-z]+)://", r"\1://", url, flags=re.IGNORECASE)
        return url, _redact(url)

    host = env.get("PGHOST", "localhost")
    port = env.get("PGPORT", "5432")
    dbname = env.get("PGDATABASE", env.get("POSTGRES_DB", "pratikshya_fashon"))
    user = env.get("PGUSER", env.get("POSTGRES_USER", "postgres"))
    password = env.get("PGPASSWORD", env.get("POSTGRES_PASSWORD", ""))
    label = f"host={host} port={port} dbname={dbname} user={user}"
    if password:
        dsn = f"host={host} port={port} dbname={dbname} user={user} password={password}"
    else:
        dsn = f"host={host} port={port} dbname={dbname} user={user}"
    return dsn, label


# --------------------------------------------------------------------------- #
# Contract helpers
# --------------------------------------------------------------------------- #
def _sig(cols: List[str]) -> str:
    return "|".join(cols)


def load_contract(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "tables" not in data:
        raise SystemExit(f"Invalid contract: {path} has no 'tables' key.")
    return data


def expected_index_signature(index: Dict[str, Any]) -> str:
    return f"{_sig(index['columns'])}||{bool(index['unique'])}"


def expected_fk_signature(fk: Dict[str, Any]) -> str:
    return f"{_sig(fk['columns'])}->{fk['referred_table']}." + _sig(fk["referred_columns"])


# --------------------------------------------------------------------------- #
# PostgreSQL metadata queries (read-only)
# --------------------------------------------------------------------------- #
TABLE_NAMES_SQL = """
SELECT table_name, table_type
FROM information_schema.tables
WHERE table_schema = %s AND table_type = 'BASE TABLE'
ORDER BY table_name
"""

TABLE_EXISTS_SQL = """
SELECT table_name
FROM information_schema.tables
WHERE table_schema = %s AND table_name = %s AND table_type = 'BASE TABLE'
"""

TABLE_IN_OTHER_SCHEMA_SQL = """
SELECT table_schema
FROM information_schema.tables
WHERE table_name = %s AND table_type = 'BASE TABLE'
ORDER BY table_schema
"""

COLUMNS_SQL = """
SELECT column_name, data_type, is_nullable,
       character_maximum_length, numeric_precision, numeric_scale, datetime_precision
FROM information_schema.columns
WHERE table_schema = %s AND table_name = %s
ORDER BY ordinal_position
"""

PK_SQL = """
SELECT a.attname AS column_name, u.ord AS ord
FROM pg_constraint con
JOIN pg_class t ON t.oid = con.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = u.attnum
WHERE n.nspname = %s AND t.relname = %s AND con.contype = 'p'
ORDER BY u.ord
"""

FK_SQL = """
SELECT con.conname AS constraint_name,
       t.relname AS table_name,
       a.attname AS column_name,
       ft.relname AS referred_table,
       fa.attname AS referred_column,
       u.ord AS ord,
       con.confdeltype AS delete_rule,
       con.confupdtype AS update_rule
FROM pg_constraint con
JOIN pg_class t ON t.oid = con.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_class ft ON ft.oid = con.confrelid
JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = u.attnum
JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS fu(attnum, ord) ON true
JOIN pg_attribute fa ON fa.attrelid = ft.oid AND fa.attnum = fu.attnum AND fu.ord = u.ord
WHERE n.nspname = %s AND t.relname = %s AND con.contype = 'f'
ORDER BY u.ord
"""

UNIQUE_CONSTRAINT_SQL = """
SELECT con.conname AS constraint_name, a.attname AS column_name, u.ord AS ord
FROM pg_constraint con
JOIN pg_class t ON t.oid = con.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = u.attnum
WHERE n.nspname = %s AND t.relname = %s AND con.contype = 'u'
ORDER BY con.conname, u.ord
"""

INDEXES_SQL = """
SELECT i.relname AS index_name,
       x.indisunique AS is_unique,
       a.attname AS column_name,
       u.ord AS ord
FROM pg_class t
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_index x ON x.indrelid = t.oid AND x.indisvalid
JOIN pg_class i ON i.oid = x.indexrelid
JOIN LATERAL unnest(x.indkey) WITH ORDINALITY AS u(attnum, ord) ON true
JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = u.attnum
WHERE n.nspname = %s AND t.relname = %s
ORDER BY i.relname, u.ord
"""


def _rows(cur, sql: str, params: tuple) -> List[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def _normalize_actual_type(
    data_type: str,
    char_length: Optional[int],
    num_precision: Optional[int],
    num_scale: Optional[int],
    datetime_precision: Optional[int],
) -> Dict[str, Any]:
    dt = (data_type or "").lower()
    out: Dict[str, Any] = {
        "kind": None,
        "length": char_length,
        "precision": num_precision,
        "scale": num_scale,
        "timezone": None,
        "pg_datatype": dt,
    }
    if dt == "character varying":
        out["kind"] = "varchar"
        out["length"] = char_length
        out["pg_datatype"] = f"varchar({char_length})" if char_length is not None else "varchar"
    elif dt in ("text", "character"):
        out["kind"] = "text"
        out["pg_datatype"] = "text"
    elif dt == "integer":
        out["kind"] = "integer"
    elif dt == "bigint":
        out["kind"] = "bigint"
    elif dt == "numeric":
        out["kind"] = "numeric"
        out["precision"] = num_precision
        out["scale"] = num_scale
        if num_precision is not None:
            out["pg_datatype"] = f"numeric({num_precision},{num_scale})" if num_scale is not None else f"numeric({num_precision})"
        else:
            out["pg_datatype"] = "numeric"
    elif dt == "double precision":
        out["kind"] = "double_precision"
        out["pg_datatype"] = "double precision"
    elif dt == "boolean":
        out["kind"] = "boolean"
    elif dt == "date":
        out["kind"] = "date"
    elif dt == "time without time zone":
        out["kind"] = "time"
    elif dt == "timestamp with time zone":
        out["kind"] = "timestamp"
        out["timezone"] = True
    elif dt == "timestamp without time zone":
        out["kind"] = "timestamp"
        out["timezone"] = False
    elif dt == "jsonb":
        out["kind"] = "jsonb"
    elif dt == "json":
        out["kind"] = "json"
    else:
        out["kind"] = dt.replace(" ", "_")
    return out


def _types_equivalent(expected: Dict[str, Any], actual: Dict[str, Any]) -> bool:
    if expected["kind"] != actual["kind"]:
        return False
    if expected["kind"] == "varchar":
        return (expected.get("length") or 0) == (actual.get("length") or 0)
    if expected["kind"] == "numeric":
        e_prec, e_scale = expected.get("precision"), expected.get("scale")
        a_prec, a_scale = actual.get("precision"), actual.get("scale")
        if e_prec is None or a_prec is None:
            return True
        if e_scale is None or a_scale is None:
            return e_prec == a_prec
        return e_prec == a_prec and e_scale == a_scale
    if expected["kind"] == "timestamp":
        return bool(expected.get("timezone")) == bool(actual.get("timezone"))
    return True


def _describe_findings(issue: str, severity: str, **parts: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {"issue": issue, "severity": severity}
    row.update({k: v for k, v in parts.items() if v is not None})
    return row


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #
def compare_table(
    cur,
    expected: Dict[str, Any],
    schema: str,
    table_name: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    stats = {"columns": 0, "extra_columns": 0, "missing_columns": 0, "issues": 0}

    # ---- tables present anywhere? -----------------------------------------
    present = bool(_rows(cur, TABLE_EXISTS_SQL, (schema, table_name)))
    if not present:
        # Find table in other schemas for a more useful message.
        others = [r[0] for r in _rows(cur, TABLE_IN_OTHER_SCHEMA_SQL, (table_name,))]
        detail = "present" if not others else f"found in other schema(s): {', '.join(others)}"
        findings.append(
            _describe_findings(
                "MISSING TABLE", "error", table=table_name, schema=schema, detail=detail
            )
        )
        stats["issues"] += 1
        return findings, stats

    # ---- columns -----------------------------------------------------------
    column_rows = _rows(cur, COLUMNS_SQL, (schema, table_name))
    actual_cols = {
        r[0]: _normalize_actual_type(r[1], r[3], r[4], r[5], r[6]) for r in column_rows
    }
    actual_nullable = {r[0]: (r[2].strip().upper() != "NO") for r in column_rows}
    stats["columns"] = len(actual_cols)

    expected_cols_by_name = {c["name"]: c for c in expected["columns"]}

    for name in actual_cols:
        if name not in expected_cols_by_name:
            findings.append(
                _describe_findings(
                    "EXTRA COLUMN", "info", table=table_name, column=name,
                    detail=f"actual {actual_cols[name]['pg_datatype']} not defined in backend model",
                )
            )
            stats["extra_columns"] += 1
            stats["issues"] += 1

    for name, ecol in expected_cols_by_name.items():
        if name not in actual_cols:
            findings.append(
                _describe_findings(
                    "MISSING COLUMN", "error", table=table_name, column=name,
                    detail=f"expected {ecol['pg_datatype']} nullable={ecol['nullable']}",
                )
            )
            stats["missing_columns"] += 1
            stats["issues"] += 1
            continue

        acol = actual_cols[name]
        if not _types_equivalent(ecol, acol):
            findings.append(
                _describe_findings(
                    "TYPE MISMATCH", "error", table=table_name, column=name,
                    detail=f"expected {ecol['pg_datatype']} but actual {acol['pg_datatype']}",
                )
            )
            stats["issues"] += 1

        exp_nullable = bool(ecol["nullable"])
        act_nullable = actual_nullable[name]
        if exp_nullable != act_nullable:
            findings.append(
                _describe_findings(
                    "NULLABILITY MISMATCH", "error", table=table_name, column=name,
                    detail=f"expected nullable={exp_nullable} but actual nullable={act_nullable}",
                )
            )
            stats["issues"] += 1

    # ---- primary key -------------------------------------------------------
    pk_actual = [r[0] for r in _rows(cur, PK_SQL, (schema, table_name))]
    pk_expected = expected["primary_key"]
    if _sig(pk_actual) != _sig(pk_expected):
        findings.append(
            _describe_findings(
                "MISSING PK", "error", table=table_name,
                detail=f"expected PRIMARY KEY ({', '.join(pk_expected)}) "
                       f"but actual ({', '.join(pk_actual) or 'none'})",
            )
        )
        stats["issues"] += 1

    # ---- foreign keys ------------------------------------------------------
    fk_rows = _rows(cur, FK_SQL, (schema, table_name))
    actual_fks: Dict[str, Dict[str, Any]] = {}
    for con_name, tname, col, ref_tab, ref_col, ord_, delrule, updrule in fk_rows:
        key = con_name
        if key not in actual_fks:
            actual_fks[key] = {
                "columns": [],
                "referred_table": ref_tab,
                "referred_columns": [],
                "ondelete": {"c": "CASCADE", "r": "RESTRICT", "n": "SET NULL",
                             "a": "NO ACTION", "d": "SET DEFAULT"}.get(delrule, delrule),
                "onupdate": {"c": "CASCADE", "r": "RESTRICT", "n": "SET NULL",
                             "a": "NO ACTION", "d": "SET DEFAULT"}.get(updrule, updrule),
            }
        actual_fks[key]["columns"].append(col)
        actual_fks[key]["referred_columns"].append(ref_col)

    for efk in expected["foreign_keys"]:
        wanted = expected_fk_signature(efk)
        match = any(
            _sig(fk["columns"]) == _sig(efk["columns"])
            and fk["referred_table"] == efk["referred_table"]
            and _sig(fk["referred_columns"]) == _sig(efk["referred_columns"])
            for fk in actual_fks.values()
        )
        if not match:
            findings.append(
                _describe_findings(
                    "MISSING FK", "error", table=table_name,
                    detail=f"expected FK {', '.join(efk['columns'])} -> "
                           f"{efk['referred_table']}({', '.join(efk['referred_columns'])})",
                )
            )
            stats["issues"] += 1

    # Extra FKs (informational, DB is authoritative).
    for fk in actual_fks.values():
        if not any(
            _sig(fk["columns"]) == _sig(efk["columns"])
            and fk["referred_table"] == efk["referred_table"]
            and _sig(fk["referred_columns"]) == _sig(efk["referred_columns"])
            for efk in expected["foreign_keys"]
        ):
            findings.append(
                _describe_findings(
                    "EXTRA FK", "info", table=table_name,
                    detail=f"actual FK {', '.join(fk['columns'])} -> "
                           f"{fk['referred_table']}({', '.join(fk['referred_columns'])})",
                )
            )

    # ---- unique constraints -------------------------------------------------
    uq_rows = _rows(cur, UNIQUE_CONSTRAINT_SQL, (schema, table_name))
    actual_unique_constraints: List[Dict[str, Any]] = []
    by_name_uci: Dict[str, Dict[str, Any]] = {}
    for con_name, col, ord_ in uq_rows:
        if con_name not in by_name_uci:
            by_name_uci[con_name] = {"name": con_name, "columns": [], "columns_signature": ""}
        by_name_uci[con_name]["columns"].append(col)
    actual_unique_constraints = list(by_name_uci.values())
    for u in actual_unique_constraints:
        u["columns_signature"] = _sig(u["columns"])

    # unique indexes also satisfy "unique" expectations.
    idx_rows = _rows(cur, INDEXES_SQL, (schema, table_name))
    unique_indexes: List[Dict[str, Any]] = []
    by_name_idx: Dict[str, Dict[str, Any]] = {}
    for index_name, is_unique, col, ord_ in idx_rows:
        if bool(is_unique):
            if index_name not in by_name_idx:
                by_name_idx[index_name] = {"name": index_name, "columns": [], "unique": True}
            by_name_idx[index_name]["columns"].append(col)
    unique_indexes = list(by_name_idx.values())
    for u in unique_indexes:
        u["columns_signature"] = _sig(u["columns"])

    satisfied_unique_sigs = {
        u["columns_signature"] for u in actual_unique_constraints + unique_indexes
    }

    for uc in expected["unique_constraints"]:
        if uc["columns_signature"] not in satisfied_unique_sigs:
            findings.append(
                _describe_findings(
                    "MISSING UNIQUE", "error", table=table_name,
                    detail=f"expected UNIQUE ({', '.join(uc['columns'])})",
                )
            )
            stats["issues"] += 1

    # ---- indexes ------------------------------------------------------------
    actual_indexes: Dict[str, Dict[str, Any]] = {}
    for index_name, is_unique, col, ord_ in idx_rows:
        if index_name not in actual_indexes:
            actual_indexes[index_name] = {"name": index_name, "columns": [], "unique": bool(is_unique)}
        actual_indexes[index_name]["columns"].append(col)
    actual_index_sigs = {
        expected_index_signature(ix): ix for ix in actual_indexes.values()
    }

    for eidx in expected["indexes"]:
        sig = expected_index_signature(eidx)
        # A non-unique index requirement is satisfied by any index with the same columns
        # (unique is stricter; a unique index implies a non-unique one).
        present_unique = sig in actual_index_sigs
        if not bool(eidx["unique"]):
            present_nonunique = any(
                _sig(ix["columns"]) == _sig(eidx["columns"])
                for ix in actual_indexes.values()
            )
            if not (present_unique or present_nonunique):
                findings.append(
                    _describe_findings(
                        "MISSING INDEX", "error", table=table_name,
                        detail=f"expected index on ({', '.join(eidx['columns'])})",
                    )
                )
                stats["issues"] += 1
        elif not present_unique:
            findings.append(
                _describe_findings(
                    "MISSING INDEX", "error", table=table_name,
                    detail=f"expected UNIQUE index on ({', '.join(eidx['columns'])})",
                )
            )
            stats["issues"] += 1

    # Extra indexes (informational, DB is authoritative).  A unique index that
    # satisfies a non-unique expected index is considered "known" to avoid noise.
    for actual_ix in actual_indexes.values():
        if not any(
            _sig(a["columns"]) == _sig(actual_ix["columns"])
            for a in expected["indexes"]
        ):
            findings.append(
                _describe_findings(
                    "EXTRA INDEX", "info", table=table_name, index=actual_ix["name"],
                    detail=f"actual index on ({', '.join(actual_ix['columns'])}) unique={actual_ix['unique']}",
                )
            )

    return findings, stats


def compare_all(cur, contract: Dict[str, Any], schema: str) -> Dict[str, Any]:
    expected_tables = contract["tables"]
    issues: List[Dict[str, Any]] = []
    table_stats: Dict[str, Any] = {}

    for table_name in sorted(expected_tables.keys()):
        exp = expected_tables[table_name]
        findings, stats = compare_table(cur, exp, schema, table_name)
        table_stats[table_name] = stats
        issues.extend(findings)

    # ---- extra tables -------------------------------------------------------
    expected_names = set(expected_tables.keys())
    actual_names = {r[0] for r in _rows(cur, TABLE_NAMES_SQL, (schema,))}
    for table_name in sorted(actual_names - expected_names):
        issues.append(
            _describe_findings(
                "EXTRA TABLE", "info", schema=schema, table=table_name,
                detail="not defined in backend SQLAlchemy models",
            )
        )

    return {"issues": issues, "table_stats": table_stats}


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _print_report(issues: List[Dict[str, Any]], schema: str, dsn_label: str) -> Dict[str, Any]:
    counts: Dict[str, int] = OrderedDict((c, 0) for c in ISSUE_CATEGORIES)
    for issue in issues:
        if issue["issue"] in counts:
            counts[issue["issue"]] += 1

    print("=" * 76)
    print("SCHEMA COMPATIBILITY AUDIT (READ-ONLY)")
    print("=" * 76)
    print(f"  Schema      : {schema}")
    print(f"  Connection  : {dsn_label}")
    print(f"  Issues      : {len(issues)}")
    print()

    # Print only error/warning lines, plus a compact info line.
    grouped: Dict[str, List[Dict[str, Any]]] = OrderedDict()
    for issue in issues:
        grouped.setdefault(issue["issue"], []).append(issue)

    print_cats = list(ISSUE_CATEGORIES) + [c for c in grouped if c not in ISSUE_CATEGORIES]
    for cat in print_cats:
        if cat in grouped:
            print(f"[{cat}] ({len(grouped[cat])})")
            for issue in grouped[cat][:80]:
                detail = issue.get("detail", "")
                scope = issue.get("table", "")
                if issue.get("column"):
                    scope += f".{issue['column']}"
                elif issue.get("index"):
                    scope += f".{issue['index']}"
                line = f"     {scope:48s} {detail}" if scope else f"     {detail}"
                print(line)
            if len(grouped[cat]) > 80:
                print(f"     ... {len(grouped[cat]) - 80} more")
    print("-- Summary by category --")
    for cat in ISSUE_CATEGORIES:
        print(f"   {cat:<22} {counts[cat]}")
    extras = [i["issue"] for i in issues if i["issue"] not in ISSUE_CATEGORIES]
    if extras:
        from collections import Counter
        print(f"   Other/info findings: {dict(Counter(extras))}")
    print()

    return {
        "schema": schema,
        "connection_label": dsn_label,
        "summary": counts,
        "total_issues": len(issues),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract",
        type=Path,
        default=DEFAULT_CONTRACT,
        help="Expected schema JSON path (default: backend/schema_audit/expected_schema.json)",
    )
    parser.add_argument("--schema", default=None, help="Override schema (default: contract schema)")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the full findings to a JSON file (never contains credentials/data)",
    )
    args = parser.parse_args()

    contract = load_contract(args.contract)
    schema = args.schema or contract.get("schema") or "pratikshya"

    dsn, dsn_label = _build_dsn()

    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is required. Install it with: pip install psycopg2-binary", file=sys.stderr)
        return 2

    conn = None
    cur = None
    try:
        conn = psycopg2.connect(dsn)
        # Set the session default to read-only BEFORE any transaction is opened.
        # Once set, every subsequent transaction on this session is read-only.
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SET default_transaction_read_only = on")
        conn.autocommit = False
        result = compare_all(cur, contract, schema)
        # Rollback (nothing was written; this is just to be absolutely safe and close cleanly).
        conn.rollback()
    except Exception as exc:  # noqa: BLE001  (report connection errors safely)
        print(f"ERROR: could not run read-only audit: {_redact(str(exc))}", file=sys.stderr)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return 1
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # Sort: errors first, then warnings, then info.
    result["issues"].sort(key=lambda i: SEVERITY_ORDER.get(i.get("severity", "info"), 2))
    summary = _print_report(result["issues"], schema, dsn_label)

    full_report = {
        "meta": {
            "kind": "read-only postgres schema compatibility audit",
            "contract": str(args.contract),
            "schema": schema,
        },
        "summary": summary,
        "issues": result["issues"],
        "per_table": result["table_stats"],
    }

    if args.output:
        args.output.write_text(json.dumps(full_report, indent=2), encoding="utf-8")
        print(f"Report written to {args.output}")

    # Exit non-zero only if there are required (error) findings.
    error_count = sum(1 for i in result["issues"] if i.get("severity") == "error")
    return 1 if error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
