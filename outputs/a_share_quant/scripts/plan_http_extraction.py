"""Emit a patch separating HTTP transport from backend service composition."""

import ast
import builtins
import copy
import json
import symtable
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "quant" / "api_server.py"
source = path.read_text(encoding="utf-8-sig")
lines = source.splitlines()
tree = ast.parse(source)
original = next(
    node
    for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "ApiHandler"
)
external = set()


def collect(table):
    external.update(
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_global() and symbol.is_referenced()
    )
    for child in table.get_children():
        collect(child)


collect(symtable.symtable(ast.unparse(original), "handler", "exec"))
imports = {}
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            copied = copy.deepcopy(node)
            copied.names = [copy.deepcopy(alias)]
            if isinstance(copied, ast.ImportFrom) and copied.level:
                copied.module = "quant" + ("." + copied.module if copied.module else "")
                copied.level = 0
            imports[name] = copied
external -= set(dir(builtins)) | {"Any", "ApiHandler"}
dependencies = sorted(
    name for name in external if name not in imports or name.startswith("_")
)
annotation_names = set()
for node in ast.walk(original):
    annotations = []
    if isinstance(node, ast.arg) and node.annotation is not None:
        annotations.append(node.annotation)
    if isinstance(node, ast.FunctionDef) and node.returns is not None:
        annotations.append(node.returns)
    annotation_names.update(
        child.id
        for ann in annotations
        for child in ast.walk(ann)
        if isinstance(child, ast.Name)
    )
stable = sorted((external - set(dependencies)) | (annotation_names & set(imports)))
known = {
    "RESEARCH_LEDGER": "ResearchLedger",
    "FEATURE_STORE": "FeatureStore",
    "PAPER_STORE": "PaperStore",
    "BACKTEST_JOBS": "dict[str, dict[str, object]]",
    "BACKTEST_JOBS_LOCK": "ContextManager[Any]",
    "BACKTEST_QUEUE_LIMIT": "int",
    "LAST_BACKUP": "dict[str, object] | None",
    "LOGGER": "logging.Logger",
}
for name in [
    "LOG_PATH",
    "BACKUP_DIR",
    "DATA_CACHE_DIR",
    "PAPER_DB_PATH",
    "FACTOR_DB_PATH",
    "RESEARCH_DB_PATH",
    "FEATURE_DB_PATH",
]:
    known[name] = "Path"


class Transform(ast.NodeTransformer):
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in dependencies:
            return ast.copy_location(
                ast.Attribute(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Name(id="self", ctx=ast.Load()),
                            attr="_services",
                            ctx=ast.Load(),
                        ),
                        args=[],
                        keywords=[],
                    ),
                    attr=node.id,
                    ctx=ast.Load(),
                ),
                node,
            )
        return node


transformed = Transform().visit(copy.deepcopy(original))
transformed.name = "HttpApiHandler"
service_method = ast.parse(
    'def _services(self) -> HttpHandlerDependencies:\n    raise NotImplementedError("Provide HTTP service dependencies")'
).body[0]
transformed.body.insert(0, service_method)
ast.fix_missing_locations(transformed)
header = [
    '"""HTTP transport and route dispatch for the A-pi services."""',
    "from __future__ import annotations",
    "from collections.abc import Callable",
    "from contextlib import AbstractContextManager as ContextManager",
    "from dataclasses import dataclass",
    "import logging",
    "from pathlib import Path",
    "from typing import Any",
    "from quant.research import ResearchLedger",
    "from quant.features import FeatureStore",
    "from quant.execution import PaperStore",
]
header.extend(ast.unparse(imports[name]) for name in stable)
header.extend(["", "@dataclass(frozen=True)", "class HttpHandlerDependencies:"])
header.extend(
    "    " + name + ": " + known.get(name, "Callable[..., Any]")
    for name in dependencies
)
module_source = "\n".join(header) + "\n\n" + ast.unparse(transformed) + "\n"
factory = (
    "def _http_dependencies() -> HttpHandlerDependencies:\n    return HttpHandlerDependencies(\n"
    + "\n".join("        " + name + "=" + name + "," for name in dependencies)
    + "\n    )\n\n\n"
)
replacement = [
    "class ApiHandler(HttpApiHandler):",
    "    def _services(self) -> HttpHandlerDependencies:",
    "        return _http_dependencies()",
]
patch = (
    "*** Begin Patch\n*** Add File: "
    + str(root / "quant" / "api" / "http_handler.py")
    + "\n"
    + "\n".join("+" + line for line in module_source.splitlines())
)
patch += (
    "\n*** Update File: "
    + str(path)
    + "\n@@\n from .api.workspace_factors import build_live_factor_rows\n+from .api.http_handler import HttpApiHandler, HttpHandlerDependencies"
)
patch += (
    "\n@@\n-def _require_strategy_dependencies(strategy_name: str) -> None:\n"
    + "\n".join("+" + line for line in factory.splitlines())
    + "\n+def _require_strategy_dependencies(strategy_name: str) -> None:"
)
patch += (
    "\n@@\n"
    + "\n".join("-" + line for line in lines[original.lineno - 1 : original.end_lineno])
    + "\n"
    + "\n".join("+" + line for line in replacement)
    + "\n*** End Patch"
)
print(json.dumps({"dependencies": dependencies, "patch": patch}, ensure_ascii=True))
