#!/usr/bin/env python3
"""Render the read-only schema compatibility audit report.

Read-only by design; it reads the generated artifacts (expected_schema.json,
query_column_dependencies.json) and writes SCHEMA_AUDIT_REPORT.md.

Usage:
    python schema_audit/render_audit_report.py
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import List

THIS_DIR = Path(__file__).resolve().parent


def _e(n):  # empty
    return ""


def _coalesce(value, default="—"):
    return value if value not in (None, "", [], {}) else default


def render(contract: dict, deps: dict) -> str:
    L: List[str] = []
    meta = contract["meta"]
    tables = contract["tables"]
    queries = deps.get("queries", [])

    # ---- header -----------------------------------------------------------
    L.append("# Database Schema Compatibility Audit (Read-Only)")
    L.append("")
    L.append("This audit inspects the backend's **SQLAlchemy models**, **Alembic migrations**, "
             "**Pydantic schemas** and **API/service queries** and produces (a) an expected "
             "schema contract, (b) a query-column dependency map, and (c) a local, "
             "read-only PostgreSQL verification script.")
    L.append("")
    L.append("> **No external database was connected to** and **no migrations, tables, "
             "constraints, or data were modified** to create this report.")
    L.append("")
    L.append("## Artifacts")
    L.append("")
    L.append("| Artifact | Contents |")
    L.append("|----------|----------|")
    L.append("| `schema_audit/expected_schema.json` | Machine-readable expected schema contract |")
    L.append("| `schema_audit/schema_contract.md` | Human-readable per-table contract |")
    L.append("| `schema_audit/query_column_dependencies.json` | Machine-readable query → column map |")
    L.append("| `schema_audit/verify_schema.py` | READ-ONLY local PostgreSQL verifier |")
    L.append("| `schema_audit/generate_expected_schema.py` | Regenerates the contract from models |")
    L.append("| `schema_audit/scan_query_columns.py` | Regenerates the query-column map |")
    L.append("")

    # ---- expected schema summary -------------------------------------------
    L.append("## 1. Expected schema summary")
    L.append("")
    L.append(f"- **Target schema**: `{contract['schema']}`")
    L.append(f"- **Tables**: {meta['tables_count']}")
    L.append(f"- **Columns**: {meta['columns_total']}")
    L.append(f"- **Foreign keys**: {meta['foreign_keys_total']}")
    L.append(f"- **Unique constraints**: {meta['unique_constraints_total']}")
    L.append(f"- **Indexes**: {meta['indexes_total']} (by column signature + uniqueness)")
    L.append("")
    L.append("Every table, column, type, nullability, PK, FK, unique constraint and index is "
             "listed in `schema_contract.md` and in `expected_schema.json`.")
    L.append("")
    L.append("**Pydantic schemas** (`app/schemas/**`) were reviewed as API I/O contracts. They "
             "describe request/response shapes and are **not** a source of database storage; this "
             "audit does not add columns based on schema fields. Where a Pydantic field names a "
             "model column, it must already exist in the contract above.")
    L.append("")

    # ---- stub tables --------------------------------------------------------
    base = {"id", "created_at", "updated_at"}
    stubs = [
        name
        for name, t in sorted(tables.items())
        if {c["name"] for c in t["columns"]} == base
    ]
    L.append("### Tables that are empty model stubs")
    L.append("")
    L.append("The following backend models declare **only** the base columns "
             "(`id`, `created_at`, `updated_at`). If the real server has additional columns on "
             "these tables, `verify_schema.py` will report them as `EXTRA COLUMN` (the existing "
             "database is authoritative).")
    L.append("")
    L.append("| Tables |")
    L.append("|--------|")
    for i in range(0, len(stubs), 3):
        L.append("| " + ", ".join(f"`{t}`" for t in stubs[i:i + 3]) + " |")
    L.append("")

    # ---- FK / unique tables --------------------------------------------------
    L.append("### Tables with foreign keys")
    L.append("")
    fk_tables = [
        (name, [(fk["columns_signature"], fk["referred_table"]) for fk in t["foreign_keys"]])
        for name, t in sorted(tables.items())
        if t["foreign_keys"]
    ]
    for name, fks in fk_tables:
        rendered = "; ".join(f"`{cols}` → `{ref}`" for cols, ref in fks)
        L.append(f"- `{name}`: {rendered}")
    L.append("")

    L.append("### Explicit unique constraints")
    L.append("")
    uq_tables = [
        (name, [u["columns"] for u in t["unique_constraints"]])
        for name, t in sorted(tables.items())
        if t["unique_constraints"]
    ]
    for name, uqs in uq_tables:
        for uq in uqs:
            L.append(f"- `{name}`: UNIQUE (`{', '.join(uq)}`)")
    L.append("")

    # ---- migration lineage & known notes ------------------------------------
    L.append("## 2. Migration lineage and schema notes")
    L.append("")
    L.append("Alembic migration chain (oldest → newest):")
    L.append("")
    L.append("1. `8f0223843258_initial_schema`")
    L.append("2. `597f883749d8_add_customer_address_preferences_columns`")
    L.append("3. `a1b2c3d4e5f6_add_category_subcategory_columns`")
    L.append("4. `c9d1e2f3a4b5_add_collection_columns`")
    L.append("5. `d1e2f3a4b5c6_add_cart_coupon_columns`")
    L.append("6. `e1f2a3b4c5d6_add_orders_columns`")
    L.append("7. `f1a2b3c4d5e6_add_payment_sessions_table`")
    L.append("8. `z1a2b3c4d5e6_add_wishlist_and_activity_columns`")
    L.append("9. `m001_move_tables_to_pratikshya_schema` (moves all app tables into `pratikshya`)")
    L.append("10. `a2b3c4d5e6f7_add_admin_setting_table`")
    L.append("")
    L.append("**Important notes**")
    L.append("")
    L.append("- The backend `Base` metadata sets `schema='pratikshya'`. If a server has not applied "
             "`m001schema`, its tables are still in `public`; `verify_schema.py` reports `MISSING TABLE` "
             "in `pratikshya` and notes the table exists in another schema.")
    L.append("- Model-generated index names (`ix_pratikshya_<table>_<col>`) differ from Alembic names "
             "(`ix_<table>_<col>`). The verifier therefore matches **indexes by column signature + "
             "uniqueness**, not by name.")
    L.append("- `Text()` (SQLAlchemy) is mapped to PG `text`, `String(length=N)` to `varchar(N)`, "
             "`JSONB` to `jsonb`, and plain `JSON` to `json`. The verifier compares these details.")
    L.append("- The initial migration created many tables as stubs; several model classes remain stubs "
             "with only the base columns (see above).")
    L.append("")

    # ---- verification semantics ---------------------------------------------
    L.append("## 3. Verification semantics (what `verify_schema.py` reports)")
    L.append("")
    L.append("| Code | Meaning | Severity |")
    L.append("|------|---------|----------|")
    L.append("| `MISSING TABLE` | Expected backend table not present in the audited schema | error |")
    L.append("| `MISSING COLUMN` | Expected column not present on the table | error |")
    L.append("| `TYPE MISMATCH` | Column kind, length, precision/scale, or timezone differ | error |")
    L.append("| `NULLABILITY MISMATCH` | Expected nullable vs actual nullable differ | error |")
    L.append("| `MISSING PK` | Primary key definition missing or different | error |")
    L.append("| `MISSING FK` | Expected foreign key definition missing | error |")
    L.append("| `MISSING UNIQUE` | Expected unique constraint/index missing | error |")
    L.append("| `MISSING INDEX` | Expected index (columns + uniqueness) missing | error |")
    L.append("| `EXTRA TABLE` | Table exists in schema that backend does not define | info |")
    L.append("| `EXTRA COLUMN` | Column exists on table that backend does not define | info |")
    L.append("| `EXTRA FK` / `EXTRA INDEX` | Extra database constraints clearly absent from backend | info |")
    L.append("")
    L.append("> `ERROR` findings make the script exit with code `1`; `INFO` findings do not.")
    L.append("")
    L.append("### Run it yourself")
    L.append("")
    L.append("```bash")
    L.append("cd backend")
    L.append("# Uses DATABASE_URL from backend/.env (or PG* environment variables)")
    L.append("python schema_audit/verify_schema.py                 # audits the pratikshya schema")
    L.append("python schema_audit/verify_schema.py --schema public # audits the public schema")
    L.append("python schema_audit/verify_schema.py --output report.json")
    L.append("```")
    L.append("")
    L.append("The script forces the session to `SET default_transaction_read_only = on`, runs only "
             "catalog `SELECT`s, and rolls back. It never prints credentials, tokens or row contents.")
    L.append("")

    # ---- query dependencies -------------------------------------------------
    L.append("## 4. API / service query column dependencies")
    L.append("")
    L.append("The static analyzer scanned 286 Python files and found **"
             f"{len(queries)} query expressions**. The complete, machine-readable mapping is in "
             "`query_column_dependencies.json`; this section summarises it per backend table.")
    L.append("")
    L.append("A dependency is a SQLAlchemy `select`/`where`/`filter`/`order_by`/`group_by`/"
             "`having`/`join`/`select_from` expression that references a model attribute "
             "(i.e. a database column). Raw SQL strings and runtime-built expressions are not parsed.")
    L.append("")

    # per table -> column -> (records, sample locations).
    # 'columns_by_table' keeps the column-table attribution correct even when a
    # query joins several models; fall back to the legacy flat list only if absent.
    col_deps: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for rec in queries:
        by_table = rec.get("columns_by_table")
        if by_table:
            for ent, cols in by_table.items():
                for col in cols:
                    col_deps[ent][col].append(rec)
        else:
            for ent in rec.get("entities", []):
                for col in rec.get("columns", []):
                    col_deps[ent][col].append(rec)

    top_deps = sorted(col_deps.items(), key=lambda kv: sum(len(v) for v in kv[1].values()), reverse=True)
    info_only = {
        "note": "Columns are those the backend queries reference. "
                "The existing server schema is authoritative; missing entries here do not mean "
                "the column is unused by raw SQL.",
    }
    for table, cols in top_deps:
        L.append(f"### `{table}`")
        L.append("")
        ordered = sorted(cols.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        L.append("| Column | Query refs | Sample locations |")
        L.append("|--------|------------|------------------|")
        for col, recs in ordered:
            sample = "; ".join(
                " ".join([str(r["file"]).split("/")[-1], f"L{r['line']}.{r['call']}", r["function"] or ""])
                for r in recs[:2]
            )
            L.append(f"| `{col}` | {len(recs)} | {sample} |")
        L.append("")

    # Unmapped model attribute references (informational but important)
    unmapped = _find_unmapped_column_refs()
    L.append("### Query references to model attributes that are neither a column nor a relationship")
    L.append("")
    if unmapped:
        L.append("The following class-attribute references are **not** a column on the model's table "
                 "and **not** a declared SQLAlchemy relationship. This is reported for awareness; "
                 "nothing was added to the schema. See `unmapped_column_refs.json`.")
        L.append("")
        L.append("| Model | Table | Attribute | First location |")
        L.append("|-------|-------|-----------|----------------|")
        for model, refs in sorted(unmapped.items()):
            table = refs[0]["table"]
            for ref in refs[:10]:
                loc = f"{ref['file'].split('/')[-1]}:L{ref['line']}"
                L.append(f"| `{model}` | `{table}` | `{ref['attribute']}` | {loc} |")
    else:
        L.append("_Every model attribute referenced in the code is either a declared backend column "
                 "or a declared SQLAlchemy relationship._")
    L.append("")

    # Raw SQL snippet references (static grep, informational)
    raw_hits = _find_raw_sql_references()
    L.append("## 5. Raw SQL / non-ORM query references (informational)")
    L.append("")
    L.append("These are textual references to table names or column literals outside the model "
             "attribute path that the AST scanner treats as raw SQL / dynamic fragments. They are "
             "reported so they are not forgotten by the ORM-only analyzer.")
    L.append("")
    if raw_hits:
        L.append("| File | Line | Snippet |")
        L.append("|------|------|---------|")
        for path, line, snippet in raw_hits[:120]:
            L.append(f"| `{path}` | {line} | `{snippet}` |")
        if len(raw_hits) > 120:
            L.append(f"| … | … | {len(raw_hits) - 120} more in `raw_sql_references.json` |")
    else:
        L.append("_No obvious raw SQL statements found._")
    L.append("")
    L.append("## 6. Expected file inventory")
    L.append("")
    L.append("| File | Purpose |")
    L.append("|------|---------|")
    L.append("| `schema_audit/expected_schema.json` | Expected contract (machine-readable) |")
    L.append("| `schema_audit/schema_contract.md` | Expected contract (human-readable) |")
    L.append("| `schema_audit/query_column_dependencies.json` | Query → column mapping |")
    L.append("| `schema_audit/unmapped_column_refs.json` | Model attrs that are neither column nor relationship |")
    L.append("| `schema_audit/raw_sql_references.json` | Raw-SQL textual references |")
    L.append("| `schema_audit/verify_schema.py` | READ-ONLY PostgreSQL verifier |")
    L.append("| `schema_audit/generate_expected_schema.py` | Contract generator |")
    L.append("| `schema_audit/scan_query_columns.py` | Query-column scanner |")
    L.append("| `schema_audit/render_schema_contract.py` | Contract renderer |")
    L.append("| `schema_audit/render_audit_report.py` | This report generator |")
    L.append("| `schema_audit/README.md` | How to use the audit tooling |")
    L.append("")

    return "\n".join(L)


def _model_map() -> dict[str, tuple[str, set[str], set[str]]]:
    """class_name -> (table_name, {columns}, {relationship attrs}) from the models."""
    import sys
    backend_root = str(THIS_DIR.parent)
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    import app.models  # noqa: F401
    from app.models.base import Base

    mapping: dict[str, tuple[str, set[str], set[str]]] = {}
    for mapper in Base.registry.mappers:  # type: ignore[attr-defined]
        cls = mapper.class_
        table = mapper.local_table
        if table is None or cls is None:
            continue
        rels = {rel.key for rel in mapper.relationships}
        mapping[cls.__name__] = (table.name, {c.name for c in table.columns}, rels)
    return mapping


def _find_unmapped_column_refs() -> dict[str, list[dict]]:
    """Class-name attribute references that are neither columns nor relationships.

    This highlights API/service code that reads a model attribute that is
    neither a database column nor a declared relationship (possible drift),
    without inventing fields.
    """
    import ast

    model_map = _model_map()
    results: dict[str, list[dict]] = {}
    root = THIS_DIR.parent
    for path in sorted(root.glob("app/**/*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                cls_name = node.value.id
                if cls_name in model_map:
                    table, cols, rels = model_map[cls_name]
                    if node.attr not in cols and node.attr not in rels:
                        d = results.setdefault(cls_name, [])
                        d.append({
                            "model": cls_name,
                            "table": table,
                            "attribute": node.attr,
                            "file": str(path.relative_to(root)),
                            "line": node.lineno,
                        })
                    elif node.attr in rels:
                        # Track relationship references separately (informational, not drift).
                        pass
    return results


def _find_raw_sql_references(limit: int = 500) -> list[tuple[str, int, str]]:
    """Find textual raw-SQL / string column references (informational)."""
    hits = []
    root = THIS_DIR.parent
    # Only flag lines that look like actual SQLAlchemy raw-SQL usage (not stray matches).
    sql_keywords = ("SELECT ", "UPDATE ", "INSERT INTO", "DELETE FROM", "WHERE ", "JOIN ",
                    "FROM ", "GROUP BY", "ORDER BY")
    raw_builders = (".execute(", "op.execute(", "sa.text(", "text(", "execute_sql(")
    for path in sorted(root.glob("app/**/*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", '"""', "'''")):
                continue
            has_builder = any(b in line for b in raw_builders)
            has_sql = any(k in line for k in sql_keywords)
            if has_builder and has_sql:
                rel = str(path.relative_to(root))
                hits.append((rel, i, stripped[:160]))
                if len(hits) >= limit:
                    return hits
    return hits


def main() -> None:
    contract = json.loads((THIS_DIR / "expected_schema.json").read_text(encoding="utf-8"))
    deps = json.loads((THIS_DIR / "query_column_dependencies.json").read_text(encoding="utf-8"))
    text = render(contract, deps)

    out = THIS_DIR / "SCHEMA_AUDIT_REPORT.md"
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {out}")

    raw = _find_raw_sql_references()
    raw_path = THIS_DIR / "raw_sql_references.json"
    raw_path.write_text(json.dumps({"raw_sql_references": raw}, indent=2), encoding="utf-8")
    print(f"Wrote {raw_path} ({len(raw)} hits)")

    unmapped = _find_unmapped_column_refs()
    unmapped_path = THIS_DIR / "unmapped_column_refs.json"
    unmapped_path.write_text(json.dumps({"unmapped_column_refs": unmapped}, indent=2), encoding="utf-8")
    hits = sum(len(v) for v in unmapped.values())
    print(f"Wrote {unmapped_path} ({hits} refs)")


if __name__ == "__main__":
    main()
