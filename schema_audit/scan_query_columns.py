#!/usr/bin/env python3
"""Static scan of the backend for query column dependencies.

Read-only by design: it does not import or connect to a database.

It parses the backend Python sources with ``ast`` and records every SQLAlchemy
query expression (``select``, ``update``, ``delete``, ``insert`` and chained
``where``/``filter``/``order_by``/``group_by``/``having``/``join``/``select_from``)
together with the model columns those expressions reference.

Output: ``backend/schema_audit/query_column_dependencies.json``.

Caveat: this is a heuristic static analysis.  It records columns referenced by
model attributes inside query calls; it does not interpret dynamic/computed
expressions, raw SQL strings or runtime-generated filters.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

QUERY_CALLS = {
    "select",
    "select_from",
    "update",
    "delete",
    "insert",
    "where",
    "filter",
    "order_by",
    "group_by",
    "having",
    "join",
    "outerjoin",
    "values",
}


def _load_model_map() -> Dict[str, Tuple[str, Set[str]]]:
    """class_name -> (table_name, {column names})."""
    import app.models  # noqa: F401
    from app.models.base import Base

    mapping: Dict[str, Tuple[str, Set[str]]] = {}
    for mapper in Base.registry.mappers:  # type: ignore[attr-defined]
        cls = mapper.class_
        table = mapper.local_table
        if table is None or cls is None:
            continue
        columns = {c.name for c in table.columns}
        mapping[cls.__name__] = (table.name, columns)
    return mapping


def _attr_chain(node: ast.AST) -> str:
    """Render a dotted attribute/name expression (e.g. OrderModel.status)."""
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _collect_entity_and_columns(
    call: ast.Call,
    model_map: Dict[str, Tuple[str, Set[str]]],
) -> Tuple[List[str], Dict[str, Set[str]], Set[str]]:
    """Return (entities, columns_by_table, selected_entity_names).

    ``entities`` = table names whose model appears directly as an argument.
    ``columns_by_table`` = {table_name: set(column names referenced on that model)}.
    ``selected_entity_names`` = model class names passed directly to select().
    """
    entities: List[str] = []
    columns_by_table: Dict[str, Set[str]] = {}
    selected_names: Set[str] = set()

    # Walk only the arguments of this call (not the chained ``func``), so a
    # ``.where()`` only records columns from its own predicate and a ``.order_by()``
    # only records the columns it sorts by.
    arg_nodes: List[ast.AST] = list(call.args) + [kw.value for kw in call.keywords]
    for top in arg_nodes:
        for node in ast.walk(top):
            # Direct entity reference: select(ProductModel), select_from(OrderModel), ...
            if isinstance(node, ast.Name) and node.id in model_map:
                entity, _ = model_map[node.id]
                if entity not in entities:
                    entities.append(entity)
                if isinstance(node.ctx, ast.Load):
                    # only treat it as a "selected entity" if it looks like a table being passed
                    # directly (we cannot tell here, but it is harmless to list it).
                    selected_names.add(node.id)

            # Attribute reference: ProductModel.status, UserModel.id, ...
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                cls_name = node.value.id
                if cls_name in model_map:
                    table, cols = model_map[cls_name]
                    if node.attr in cols:
                        columns_by_table.setdefault(table, set()).add(node.attr)

    return entities, columns_by_table, sorted(selected_names)


def _enclosing_functions(tree: ast.AST) -> Dict[Tuple[int, int], List[str]]:
    """Map (line, col) start of each Call to the enclosing function names."""
    enclosing: Dict[Tuple[int, int], List[str]] = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: List[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            key = (node.lineno, node.col_offset)
            enclosing[key] = list(self.stack)
            self.generic_visit(node)

    Visitor().visit(tree)
    return enclosing


def scan() -> Dict[str, Any]:
    model_map = _load_model_map()

    records: List[Dict[str, Any]] = []
    seen = set()
    files = sorted(BACKEND_ROOT.glob("app/**/*.py"))

    for file_path in files:
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            continue

        enclosing = _enclosing_functions(tree)

        class QueryVisitor(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                call_attr: str = ""
                if isinstance(node.func, ast.Name):
                    call_attr = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    call_attr = node.func.attr
                else:
                    call_attr = ""

                if call_attr in QUERY_CALLS:
                    rel_path = file_path.relative_to(BACKEND_ROOT)
                    entities, columns_by_table, selected_names = _collect_entity_and_columns(
                        node, model_map
                    )
                    if entities or any(columns_by_table.values()):
                        all_columns = sorted({c for v in columns_by_table.values() for c in v})
                        key = (
                            str(rel_path),
                            node.lineno,
                            call_attr,
                            tuple(sorted(entities)),
                            tuple(all_columns),
                        )
                        if key in seen:
                            return
                        seen.add(key)
                        funcs = enclosing.get((node.lineno, node.col_offset), [])
                        record = {
                            "file": str(rel_path),
                            "line": node.lineno,
                            "call": call_attr,
                            "function": funcs[0] if funcs else None,
                            "entities": entities,
                            "selected_entities": selected_names,
                            "columns": all_columns,
                            "columns_by_table": {
                                k: sorted(v) for k, v in sorted(columns_by_table.items())
                            },
                        }
                        records.append(record)
                self.generic_visit(node)

        QueryVisitor().visit(tree)

    return {
        "meta": {
            "generator": "backend/schema_audit/scan_query_columns.py",
            "method": "AST static analysis of SQLAlchemy select/update/delete/insert "
                      "and where/filter/order_by/group_by/having/join/select_from calls",
            "note": "Heuristic only; raw SQL strings and runtime-built expressions are not parsed.",
            "files_scanned": len(files),
            "records": len(records),
        },
        "queries": records,
    }


def main() -> None:
    result = scan()
    out = Path(__file__).resolve().parent / "query_column_dependencies.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    print(f"  records={result['meta']['records']}")


if __name__ == "__main__":
    main()
