#!/usr/bin/env python3
"""Render the machine-readable expected schema contract as a human-readable Markdown report.

Read-only by design. Usage:

    python schema_audit/render_schema_contract.py
"""
from __future__ import annotations

import json
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent


def _fmt_cols(cols):
    return ", ".join(cols) if cols else "(none)"


def render(contract: dict) -> str:
    lines: List[str] = []
    lines.append("# Expected Backend Schema Contract (human-readable)")
    lines.append("")
    lines.append("Generated from the backend SQLAlchemy models. No external database was accessed.")
    lines.append("")
    meta = contract["meta"]
    lines.append("- **Schema**: `%s`" % contract["schema"])
    lines.append("- **Tables**: %s" % meta["tables_count"])
    lines.append("- **Columns**: %s" % meta["columns_total"])
    lines.append("- **Foreign keys**: %s" % meta["foreign_keys_total"])
    lines.append("- **Unique constraints**: %s" % meta["unique_constraints_total"])
    lines.append("- **Indexes**: %s" % meta["indexes_total"])
    lines.append("")
    lines.append("> Index matching in the verification script is by column signature + ")
    lines.append("> uniqueness, not by index name, because Alembic/model naming conventions differ.")
    lines.append("")

    for table_name in sorted(contract["tables"].keys()):
        t = contract["tables"][table_name]
        lines.append("")
        lines.append(f"## `{table_name}`")
        lines.append("")
        lines.append(f"- **Schema**: `{t['schema']}`")
        lines.append(f"- **Primary key**: `{_fmt_cols(t['primary_key'])}`")
        lines.append("")
        lines.append("### Columns")
        lines.append("")
        if t["columns"]:
            lines.append("| Column | Type | Nullable | PK | Default (app-side) |")
            lines.append("|--------|------|----------|----|--------------------|")
            for c in t["columns"]:
                pk = "yes" if c["primary_key"] else ""
                default = c.get("app_default") or ""
                lines.append(
                    f"| `{c['name']}` | `{c['pg_datatype']}` | {str(c['nullable']).lower()} | {pk} | `{default}` |"
                )
        else:
            lines.append("_No columns declared (empty model stub)._")
        lines.append("")
        lines.append("### Foreign keys")
        lines.append("")
        if t["foreign_keys"]:
            for fk in t["foreign_keys"]:
                lines.append(
                    f"- `{fk['columns_signature']}` -> `{fk['referred_table']}` "
                    f"(`{fk['referred_signature']}`) ondelete={fk.get('ondelete') or 'NO ACTION'}"
                )
        else:
            lines.append("_None._")
        lines.append("")
        lines.append("### Unique constraints")
        lines.append("")
        if t["unique_constraints"]:
            for uc in t["unique_constraints"]:
                lines.append(f"- `{uc['name'] or '(unnamed)'}`: ({_fmt_cols(uc['columns'])})")
        else:
            lines.append("_None (unique columns are represented by unique indexes below)._")
        lines.append("")
        lines.append("### Indexes")
        lines.append("")
        if t["indexes"]:
            lines.append("| Index (name candidates) | Columns | Unique |")
            lines.append("|------------------------|---------|--------|")
            for ix in t["indexes"]:
                lines.append(
                    f"| `{ix['name']}` | ({_fmt_cols(ix['columns'])}) | {str(ix['unique']).lower()} |"
                )
        else:
            lines.append("_None._")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    contract = json.loads((THIS_DIR / "expected_schema.json").read_text(encoding="utf-8"))
    out = THIS_DIR / "schema_contract.md"
    out.write_text(render(contract), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
