# Backend Database Schema Compatibility Audit (read-only)

This folder contains a **read-only** database schema compatibility audit for the
Pratikshya Fashon backend. It does **not** connect to a database by default,
does **not** modify migrations, and the included verifier is forced to run in
PostgreSQL **read-only** mode.

## What is here

| File | Purpose |
|------|---------|
| `generate_expected_schema.py` | Inspects the registered SQLAlchemy models and emits `expected_schema.json`. |
| `expected_schema.json` | **Machine-readable expected schema contract** (tables, columns, types, nullability, PK, FK, unique constraints, indexes). |
| `schema_contract.md` | Human-readable per-table rendering of `expected_schema.json`. |
| `scan_query_columns.py` | Static AST scan of API/service queries; emits `query_column_dependencies.json`. |
| `query_column_dependencies.json` | **Machine-readable query → column dependency map** (which API/service query expression touches which columns). |
| `render_schema_contract.py` | Regenerates `schema_contract.md`. |
| `render_audit_report.py` | Regenerates `SCHEMA_AUDIT_REPORT.md` and `raw_sql_references.json`. |
| `SCHEMA_AUDIT_REPORT.md` | Summary audit report. |
| `verify_schema.py` | **Safe local-only READ-ONLY verifier** against a real PostgreSQL server. |

## Expected contract (why it exists)
The backend model layer is the source of the *expected* schema. The verifier
compares the real PostgreSQL catalog against that contract. If the existing
server schema has extra columns/tables — which the README says is authoritative
— those are reported as `EXTRA ...` (info) rather than treated as errors.

## Regenerating the contract & query map (from code)
From `backend/` with the project dependencies installed:

```bash
python schema_audit/generate_expected_schema.py   # -> expected_schema.json
python schema_audit/scan_query_columns.py         # -> query_column_dependencies.json
python schema_audit/render_schema_contract.py     # -> schema_contract.md
python schema_audit/render_audit_report.py        # -> SCHEMA_AUDIT_REPORT.md
```

These commands never touch a database.

## Running the READ-ONLY verifier against your PostgreSQL server

The verifier reads credentials from **your private `backend/.env`** (or from
`PGHOST`/`PGPORT`/`PGDATABASE`/`PGUSER`/`PGPASSWORD`). It does **not** print
credentials, tokens, or row contents.

```bash
cd backend
python schema_audit/verify_schema.py                 # audits the `pratikshya` schema
python schema_audit/verify_schema.py --schema public # audits the `public` schema
python schema_audit/verify_schema.py --output report.json
```

What it does:
1. `SET default_transaction_read_only = on`.
2. Runs `SELECT`s only against `information_schema` and `pg_catalog`.
3. Rolls back the transaction and closes the connection.
4. Reports `MISSING TABLE`, `MISSING COLUMN`, `TYPE MISMATCH`,
   `NULLABILITY MISMATCH`, `MISSING PK`, `MISSING FK`, `MISSING UNIQUE`,
   `MISSING INDEX`, `EXTRA TABLE`, `EXTRA COLUMN`, and info-level
   `EXTRA FK` / `EXTRA INDEX`.

## Safety notes
- The script never executes `ALTER`, `CREATE`, `DROP`, `INSERT`, `UPDATE`,
  `DELETE` or `SELECT` over application tables.
- Index matching is by **column signature + uniqueness**, not by index name.
  This avoids false positives caused by Model-vs-Alembic index name differences.
- The existing server schema is authoritative. This audit never changes it.
