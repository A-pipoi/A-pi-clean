"""Print reviewable apply_patch changes for an API service extraction.

This tool reads source and emits patches; it never writes project files.
"""

from __future__ import annotations

import ast
import builtins
import copy
import json
import symtable
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source_path = root / "quant" / "api_server.py"
source = source_path.read_text(encoding="utf-8-sig")
lines = source.splitlines()
tree = ast.parse(source)
kind = sys.argv[1]
handler = next(
    node
    for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "ApiHandler"
)
post = next(
    node
    for node in handler.body
    if isinstance(node, ast.FunctionDef) and node.name == "do_POST"
)
if kind == "sealed":
    branch = next(
        node
        for node in post.body
        if isinstance(node, ast.If)
        and '"/api/validation/sealed"' in ast.get_source_segment(source, node.test)
    )
    body = copy.deepcopy(branch.body[:-1])
    start, end = branch.lineno - 1, branch.end_lineno
    function_name, class_name, factory_name, module = (
        "run_sealed_request",
        "SealedValidationDependencies",
        "_sealed_validation_dependencies",
        "sealed_service",
    )
elif kind == "backtest":
    index = next(
        i
        for i, node in enumerate(post.body)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "experiment_id"
            for target in node.targets
        )
    )
    body = copy.deepcopy(post.body[index:])
    start, end = post.body[index].lineno - 1, post.end_lineno
    function_name, class_name, factory_name, module = (
        "run_backtest_request",
        "BacktestDependencies",
        "_backtest_dependencies",
        "backtest_service",
    )
else:
    raise ValueError(kind)


class TransportTransformer(ast.NodeTransformer):
    def visit_Assign(self, node):
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "_read_payload"
        ):
            return None
        return self.generic_visit(node)

    def visit_Expr(self, node):
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "_send_json"
        ):
            return ast.copy_location(
                ast.Return(ast.Tuple(elts=node.value.args, ctx=ast.Load())), node
            )
        return self.generic_visit(node)


body = [TransportTransformer().visit(node) for node in body]
body = [node for node in body if node is not None]
function = ast.FunctionDef(
    name=function_name,
    args=ast.arguments(
        posonlyargs=[],
        args=[
            ast.arg(
                arg="payload", annotation=ast.parse("dict[str, Any]", mode="eval").body
            ),
            ast.arg(arg="deps", annotation=ast.Name(id=class_name, ctx=ast.Load())),
        ],
        kwonlyargs=[],
        kw_defaults=[],
        defaults=[],
    ),
    body=body,
    decorator_list=[],
    returns=ast.parse("tuple[int, dict[str, object]]", mode="eval").body,
)
ast.fix_missing_locations(function)
temporary = ast.unparse(function)
tables = symtable.symtable(temporary, "service", "exec")


def find_function(table):
    if table.get_name() == function_name and table.get_type() == "function":
        return table
    for child in table.get_children():
        found = find_function(child)
        if found is not None:
            return found
    return None


symbols = find_function(tables)
if not (symbols is not None):
    raise AssertionError("Verification gate failed: scripts/plan_api_extraction.py:127")
external: set[str] = set()


def collect(table):
    external.update(
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_global() and symbol.is_referenced()
    )
    for child in table.get_children():
        collect(child)


collect(symbols)
external -= set(dir(builtins)) | {"Any", class_name}
imports: dict[str, ast.AST] = {}
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

dependency_names = sorted(
    name
    for name in external
    if name not in imports
    or name.startswith("_")
    or name
    in {"TushareDailyAdapter", "build_strategy", "run_sealed_test", "validate_strategy"}
)
stable_names = sorted(external - set(dependency_names))
known_types = {
    "RESEARCH_LEDGER": "ResearchLedger",
    "FACTOR_STORE": "FactorRegistryStore",
    "FEATURE_STORE": "FeatureStore",
    "DATA_CACHE_DIR": "Path",
    "SEALED_TEST_DAYS": "int",
    "SEALED_MIN_OBSERVATIONS": "int",
    "FACTOR_PIPELINE_VERSION": "str",
    "FACTOR_MINING_UNIVERSE_INDEX_CODE": "str",
    "DEFAULT_SYMBOLS": "list[str]",
    "CHINA_TIMEZONE": "tzinfo",
}
fields = [
    (name, known_types.get(name, "Callable[..., Any]")) for name in dependency_names
]


class DependencyTransformer(ast.NodeTransformer):
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in dependency_names:
            return ast.copy_location(
                ast.Attribute(
                    value=ast.Name(id="deps", ctx=ast.Load()),
                    attr=node.id,
                    ctx=ast.Load(),
                ),
                node,
            )
        return node


function = DependencyTransformer().visit(function)
ast.fix_missing_locations(function)


# Return annotations from existing branches contain object-valued dictionaries.
# An unreachable transport return is removed so every route returns its tuple.
class ReturnCleanup(ast.NodeTransformer):
    def generic_visit(self, node):
        node = super().generic_visit(node)
        for field in ("body", "orelse", "finalbody"):
            values = getattr(node, field, None)
            if isinstance(values, list):
                clean = []
                for item in values:
                    if clean and isinstance(clean[-1], ast.Return):
                        break
                    clean.append(item)
                setattr(node, field, clean)
        return node


function = ReturnCleanup().visit(function)
header = [
    '"""'
    + (
        "One-shot sealed PIT acceptance."
        if kind == "sealed"
        else "Audited synchronous backtest orchestration."
    )
    + '"""',
    "from __future__ import annotations",
    "",
    "from collections.abc import Callable",
    "from dataclasses import dataclass",
    "from datetime import tzinfo",
    "from pathlib import Path",
    "from typing import Any",
    "from quant.research import ResearchLedger",
    "from quant.factors import FactorRegistryStore",
    "from quant.features import FeatureStore",
]
header.extend(ast.unparse(imports[name]) for name in stable_names)
header.extend(["", "", "@dataclass(frozen=True)", "class " + class_name + ":"])
header.extend("    " + name + ": " + annotation for name, annotation in fields)
module_source = "\n".join(header) + "\n\n\n" + ast.unparse(function) + "\n"
factory = (
    "def "
    + factory_name
    + "() -> "
    + class_name
    + ":\n    return "
    + class_name
    + "(\n"
    + "\n".join("        " + name + "=" + name + "," for name in dependency_names)
    + "\n    )\n\n\n"
)
replacement = []
if kind == "sealed":
    replacement.append('        if path == "/api/validation/sealed":')
indent = "            " if kind == "sealed" else "        "
replacement += [
    indent + "try:",
    indent + "    payload = self._read_payload()",
    indent + "except (TypeError, ValueError, json.JSONDecodeError) as error:",
    indent + '    self._send_json(400, {"ok": False, "error": str(error)})',
    indent + "    return",
    indent + "self._send_json(*" + function_name + "(payload, " + factory_name + "()))",
]
if kind == "sealed":
    replacement.append(indent + "return")
old_lines = lines[start:end]
patch = (
    "*** Begin Patch\n*** Add File: "
    + str(root / "quant" / "api" / (module + ".py"))
    + "\n"
    + "\n".join("+" + line for line in module_source.splitlines())
    + "\n*** Update File: "
    + str(source_path)
    + "\n@@\n from .api.workspace_factors import build_live_factor_rows\n+from .api."
    + module
    + " import "
    + class_name
    + ", "
    + function_name
    + "\n@@\n"
    + "\n".join("-" + line for line in old_lines)
    + "\n"
    + "\n".join("+" + line for line in replacement)
    + "\n@@\n-def _require_strategy_dependencies(strategy_name: str) -> None:\n"
    + "\n".join("+" + line for line in factory.splitlines())
    + "\n+def _require_strategy_dependencies(strategy_name: str) -> None:\n*** End Patch"
)
print(
    json.dumps(
        {"kind": kind, "dependencies": fields, "patch": patch}, ensure_ascii=True
    )
)
