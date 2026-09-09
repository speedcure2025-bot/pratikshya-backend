#!/usr/bin/env python3
"""Generate the machine-readable expected database schema contract.

Read-only by design: this script NEVER connects to a database.  It inspects
the SQLAlchemy models that the backend registers (``app.models``) and writes a
JSON contract describing every backend table and its columns, types,
nullability, primary key, foreign keys, unique constraints and indexes.

Usage (from ``backend/``):

    python schema_audit/generate_expected_schema.py

The generator must be run with a Python environment that has the project's
SQLAlchemy + Pydantic dependencies installed (see ``requirements.txt``).

The produced contract is consumed by ``verify_schema.py``.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric, String, Text, Time
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.sqltypes import JSON
from sqlalchemy.sql.schema import Column, ForeignKeyConstraint, Index, UniqueConstraint

# Make the backend package importable when running this file from anywhere.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _describe_type(column: Column) -> Dict[str, Any]:
    """Normalise a SQLAlchemy column type to a PostgreSQL comparable shape."""
    t = column.type
    out: Dict[str, Any] = {
        "sqlalchemy_type": repr(t),
        "kind": None,
        "length": None,
        "precision": None,
        "scale": None,
        "timezone": None,
        "pg_datatype": None,
    }

    if isinstance(t, Text):
        out["kind"] = "text"
        out["pg_datatype"] = "text"
    elif isinstance(t, String):
        out["kind"] = "varchar"
        out["length"] = t.length
        out["pg_datatype"] = f"varchar({t.length})" if t.length else "varchar"
    elif isinstance(t, Integer):
        out["kind"] = "integer"
        out["pg_datatype"] = "integer"
    elif isinstance(t, Float):
        out["kind"] = "double_precision"
        out["pg_datatype"] = "double precision"
    elif isinstance(t, Numeric):
        out["kind"] = "numeric"
        out["precision"] = t.precision
        out["scale"] = t.scale
        if t.precision is not None:
            out["pg_datatype"] = f"numeric({t.precision},{t.scale})" if t.scale is not None else f"numeric({t.precision})"
        else:
            out["pg_datatype"] = "numeric"
    elif isinstance(t, Boolean):
        out["kind"] = "boolean"
        out["pg_datatype"] = "boolean"
    elif isinstance(t, Date):
        out["kind"] = "date"
        out["pg_datatype"] = "date"
    elif isinstance(t, Time):
        out["kind"] = "time"
        out["pg_datatype"] = "time without time zone"
    elif isinstance(t, DateTime):
        tz = bool(getattr(t, "timezone", False))
        out["kind"] = "timestamp"
        out["timezone"] = tz
        out["pg_datatype"] = "timestamp with time zone" if tz else "timestamp without time zone"
    elif isinstance(t, JSONB):
        out["kind"] = "jsonb"
        out["pg_datatype"] = "jsonb"
    elif isinstance(t, JSON):
        out["kind"] = "json"
        out["pg_datatype"] = "json"
    else:
        out["kind"] = str(type(t).__name__).lower()
        out["pg_datatype"] = repr(t)

    return out


def _default_repr(value: Any) -> Optional[str]:
    """Safe textual representation of a Python-side default (no secrets, no data)."""
    if value is None:
        return None
    if hasattr(value, "arg"):
        arg = value.arg
    else:
        arg = value
    if callable(arg):
        if getattr(arg, "__module__", None) == "builtins":
            return f"<builtin:{arg.__name__}>"
        return "<callable>"
    if isinstance(arg, (str, bool, int, float, type(None), datetime, date, time, Decimal)):
        return repr(arg)
    return repr(arg)


def _constraint_sort_key(signature: List[str]) -> str:
    return "|".join(signature)


def _describe_index(index: Index, table_name: str) -> Dict[str, Any]:
    columns = [c.name for c in index.columns]
    return {
        "name": index.name,
        "columns": columns,
        "columns_signature": "|".join(columns),
        "unique": bool(index.unique),
    }


def _extract_constraint_signature(constraint: Any) -> List[str]:
    if hasattr(constraint, "columns"):
        return [c.name for c in constraint.columns]
    if hasattr(constraint, "column_keys"):
        return list(constraint.column_keys)
    return []


def build_contract() -> Dict[str, Any]:
    import app.models  # noqa: F401  (registers all models)
    from app.models.base import Base

    metadata = Base.metadata
    schema_name = metadata.schema or "public"

    contract: Dict[str, Any] = {
        "meta": {
            "generator": "backend/schema_audit/generate_expected_schema.py",
            "source": "sqlalchemy-models (app.models)",
            "schema": schema_name,
            "tables_count": len(metadata.tables),
            "notes": [
                "Contract generated from backend SQLAlchemy models only; no external DB was accessed.",
                "Index matching in verify_schema.py is by column signature + uniqueness, "
                "not by index name, because Alembic/model naming conventions differ.",
                "Empty model stubs (e.g. inventory_*, media_*, chatbot_*, etc.) intentionally "
                "declare only id/created_at/updated_at in the backend code; any columns the "
                "real database has on those tables are reported as EXTRA COLUMN.",
            ],
        },
        "schema": schema_name,
        "tables": {},
    }

    for fullname in sorted(metadata.tables.keys()):
        table = metadata.tables[fullname]
        table_name = table.name

        columns: List[Dict[str, Any]] = []
        for col in table.columns:
            col_desc = {
                "name": col.name,
                **_describe_type(col),
                "nullable": bool(col.nullable),
                "primary_key": bool(col.primary_key),
                "unique": bool(col.unique),
                "index": bool(col.index),
                "app_default": _default_repr(col.default),
                "server_default": None if col.server_default is None else str(col.server_default.arg),
            }
            columns.append(col_desc)

        # Primary key
        pk = [c.name for c in table.primary_key.columns] if table.primary_key else []

        # Foreign keys
        fks = []
        for fk in table.foreign_key_constraints:
            constrained = [c.name for c in fk.columns]
            referred = [
                f"{fk_ref.column.table.name}.{fk_ref.column.name}" for fk_ref in fk.elements
            ]
            fks.append(
                {
                    "name": fk.name,
                    "columns": constrained,
                    "columns_signature": "|".join(constrained),
                    "referred_table": fk.referred_table.name if fk.referred_table is not None else None,
                    "referred_columns": referred,
                    "referred_signature": "|".join(referred),
                    "ondelete": fk.ondelete,
                    "onupdate": fk.onupdate,
                }
            )

        # Unique constraints (explicit UniqueConstraint tuples + unique columns not already
        # represented by a unique index are reported once under "unique_constraints").
        unique_constraints = []
        seen_unique_signatures = set()
        for constraint in sorted(table.constraints, key=lambda c: str(type(c).__name__)):
            if isinstance(constraint, UniqueConstraint):
                cols = _extract_constraint_signature(constraint)
                sig = "|".join(cols)
                if sig not in seen_unique_signatures:
                    unique_constraints.append(
                        {
                            "name": constraint.name,
                            "columns": cols,
                            "columns_signature": sig,
                        }
                    )
                    seen_unique_signatures.add(sig)

        # Indexes.  A column with unique=True also often produces a unique index; keep those
        # as indexes so verification can match them against pg_indexes.
        indexes = []
        seen_index_signatures = set()
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            desc = _describe_index(index, table_name)
            sig = f"{desc['columns_signature']}|{desc['unique']}"
            if sig not in seen_index_signatures:
                indexes.append(desc)
                seen_index_signatures.add(sig)

        contract["tables"][table_name] = {
            "schema": schema_name,
            "name": table_name,
            "columns": columns,
            "primary_key": pk,
            "foreign_keys": fks,
            "unique_constraints": unique_constraints,
            "indexes": indexes,
        }

    contract["meta"]["tables"] = list(contract["tables"].keys())
    contract["meta"]["columns_total"] = sum(
        len(t["columns"]) for t in contract["tables"].values()
    )
    contract["meta"]["foreign_keys_total"] = sum(
        len(t["foreign_keys"]) for t in contract["tables"].values()
    )
    contract["meta"]["indexes_total"] = sum(len(t["indexes"]) for t in contract["tables"].values())
    contract["meta"]["unique_constraints_total"] = sum(
        len(t["unique_constraints"]) for t in contract["tables"].values()
    )
    return contract


def main() -> None:
    contract = build_contract()
    out_path = Path(__file__).resolve().parent / "expected_schema.json"
    out_path.write_text(json.dumps(contract, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(f"  tables={contract['meta']['tables_count']} "
          f"columns={contract['meta']['columns_total']} "
          f"fks={contract['meta']['foreign_keys_total']} "
          f"unique_constraints={contract['meta']['unique_constraints_total']} "
          f"indexes={contract['meta']['indexes_total']}")


if __name__ == "__main__":
    main()
