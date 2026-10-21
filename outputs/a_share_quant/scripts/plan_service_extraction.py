"""Generate explicit-dependency service extraction patches without writing files."""

from __future__ import annotations

import ast
import builtins
import copy
import json
import symtable
import sys
from pathlib import Path

GROUPS = {
    "mining_data": [
        "_fetch_factor_mining_pit_bundle",
        "_factor_mining_comparison_panel",
        "_factor_mining_cross_universe_reference",
        "_factor_mining_dataset",
    ],
    "mining_research": [
        "_factor_mining_scope",
        "_factor_mining_status",
        "_run_factor_mining_request",
        "_evaluate_factor_expression_request",
    ],
    "mining_acceptance": ["_accept_factor_mining_run", "_promote_mined_factor"],
    "sealed_snapshot": [
        "_sealed_pipeline_key",
        "_ensure_sealed_snapshot",
        "_research_bars",
        "_factor_snapshot",
        "_factor_snapshot_hash",
        "_sealed_result_passed",
    ],
    "paper_governance": [
        "_sealed_gate_payload",
        "_paper_sealed_gate_status",
        "_paper_cycle_sealed_gate_payload",
        "_validation_gate",
        "_factor_registry_status",
    ],
    "workspace_snapshot": ["_build_workspace_snapshot"],
    "paper_quality": [
        "_risk_limits_payload",
        "_limits_data_quality",
        "_enrich_paper_state",
        "_require_complete_universe",
    ],
    "audit_metadata": [
        "_record_research_experiment",
        "_current_git_revision",
        "_frontend_source_revision",
        "_cache_writable",
        "_sample_std",
        "_average",
    ],
}
root = Path(__file__).resolve().parents[1]
path = root / "quant" / "api_server.py"
source = path.read_text(encoding="utf-8-sig")
lines = source.splitlines()
tree = ast.parse(source)
module = sys.argv[1]
names = GROUPS[module]
originals = [
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name in names
]
if not (len(originals) == len(names)):
    raise AssertionError(
        "Verification gate failed: scripts/plan_service_extraction.py:70"
    )
if any(isinstance(node, ast.Global) for fn in originals for node in ast.walk(fn)):
    raise AssertionError("stateful function requires a lifecycle service")
class_name = "".join(part.title() for part in module.split("_")) + "Dependencies"
factory_name = "_" + module + "_dependencies"
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

external = set()
annotation_names = set()


def collect(table):
    external.update(
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_global() and symbol.is_referenced()
    )
    for child in table.get_children():
        collect(child)


def find_function(table, name):
    if table.get_name() == name and table.get_type() == "function":
        return table
    for child in table.get_children():
        found = find_function(child, name)
        if found is not None:
            return found
    return None


for fn in originals:
    table = symtable.symtable(ast.unparse(fn), "service", "exec")
    function_table = find_function(table, fn.name)
    if not (function_table is not None):
        raise AssertionError(
            "Verification gate failed: scripts/plan_service_extraction.py:115"
        )
    collect(function_table)
    for node in ast.walk(fn):
        annotations = []
        if isinstance(node, ast.arg) and node.annotation is not None:
            annotations.append(node.annotation)
        if isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        if isinstance(node, ast.FunctionDef) and node.returns is not None:
            annotations.append(node.returns)
        annotation_names.update(
            child.id
            for ann in annotations
            for child in ast.walk(ann)
            if isinstance(child, ast.Name)
        )
external -= set(dir(builtins)) | {"Any"}
dependencies = sorted(
    name
    for name in external
    if name not in imports or name.startswith("_") or name == "TushareDailyAdapter"
)
stable = sorted((external - set(dependencies)) | (annotation_names & set(imports)))
known = {
    "RESEARCH_LEDGER": "ResearchLedger",
    "FACTOR_STORE": "FactorRegistryStore",
    "FEATURE_STORE": "FeatureStore",
    "PAPER_STORE": "PaperStore",
    "PAPER_SCHEDULER": "PaperScheduler | None",
    "DATA_CACHE_DIR": "Path",
    "PAPER_DB_PATH": "Path",
    "FACTOR_DB_PATH": "Path",
    "RESEARCH_DB_PATH": "Path",
    "FEATURE_DB_PATH": "Path",
    "SEALED_TEST_DAYS": "int",
    "SEALED_MIN_OBSERVATIONS": "int",
    "FACTOR_MINING_HISTORY_DAYS": "int",
    "FACTOR_PIPELINE_VERSION": "str",
    "FACTOR_MINING_UNIVERSE_INDEX_CODE": "str",
    "DEFAULT_SYMBOLS": "list[str]",
    "CHINA_TIMEZONE": "tzinfo",
    "__file__": "str",
}
fields = [(name, known.get(name, "Callable[..., Any]")) for name in dependencies]


class DependencyTransformer(ast.NodeTransformer):
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in dependencies:
            return ast.copy_location(
                ast.Attribute(
                    value=ast.Name(id="deps", ctx=ast.Load()),
                    attr=node.id,
                    ctx=ast.Load(),
                ),
                node,
            )
        return node


functions = []
wrappers = []
for original in originals:
    fn = DependencyTransformer().visit(copy.deepcopy(original))
    fn.args.kwonlyargs.append(
        ast.arg(arg="deps", annotation=ast.Name(id=class_name, ctx=ast.Load()))
    )
    fn.args.kw_defaults.append(None)
    ast.fix_missing_locations(fn)
    functions.append(ast.unparse(fn))
    wrapper = copy.deepcopy(original)
    positional = [
        ast.Name(id=arg.arg, ctx=ast.Load())
        for arg in wrapper.args.posonlyargs + wrapper.args.args
    ]
    if wrapper.args.vararg:
        positional.append(
            ast.Starred(
                ast.Name(id=wrapper.args.vararg.arg, ctx=ast.Load()), ctx=ast.Load()
            )
        )
    keywords = [
        ast.keyword(arg=arg.arg, value=ast.Name(id=arg.arg, ctx=ast.Load()))
        for arg in wrapper.args.kwonlyargs
    ]
    if wrapper.args.kwarg:
        keywords.append(
            ast.keyword(
                arg=None, value=ast.Name(id=wrapper.args.kwarg.arg, ctx=ast.Load())
            )
        )
    keywords.append(
        ast.keyword(
            arg="deps",
            value=ast.Call(
                func=ast.Name(id=factory_name, ctx=ast.Load()), args=[], keywords=[]
            ),
        )
    )
    wrapper.body = [
        ast.Return(
            ast.Call(
                func=ast.Name(id=original.name + "_service", ctx=ast.Load()),
                args=positional,
                keywords=keywords,
            )
        )
    ]
    ast.fix_missing_locations(wrapper)
    wrappers.append(ast.unparse(wrapper))
header = [
    '"""'
    + module.replace("_", " ").capitalize()
    + ' services with explicit runtime dependencies."""',
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
    "from quant.execution import PaperStore, PaperScheduler",
]
header.extend(ast.unparse(imports[name]) for name in stable)
header.extend(["", "", "@dataclass(frozen=True)", "class " + class_name + ":"])
header.extend("    " + name + ": " + annotation for name, annotation in fields)
module_source = "\n".join(header) + "\n\n\n" + "\n\n\n".join(functions) + "\n"
factory = (
    "def "
    + factory_name
    + "() -> "
    + class_name
    + ":\n    return "
    + class_name
    + "(\n"
    + "\n".join("        " + name + "=" + name + "," for name in dependencies)
    + "\n    )\n\n\n"
)
patch = (
    "*** Begin Patch\n*** Add File: "
    + str(root / "quant" / "api" / (module + ".py"))
    + "\n"
    + "\n".join("+" + line for line in module_source.splitlines())
)
patch += (
    "\n*** Update File: "
    + str(path)
    + "\n@@\n from .api.workspace_factors import build_live_factor_rows\n+from .api."
    + module
    + " import (\n+    "
    + class_name
    + ",\n"
    + "\n".join("+    " + name + " as " + name + "_service," for name in names)
    + "\n+)"
)
patch += (
    "\n@@\n-def _require_strategy_dependencies(strategy_name: str) -> None:\n"
    + "\n".join("+" + line for line in factory.splitlines())
    + "\n+def _require_strategy_dependencies(strategy_name: str) -> None:"
)
for original, wrapper in zip(originals, wrappers):
    patch += (
        "\n@@\n"
        + "\n".join(
            "-" + line for line in lines[original.lineno - 1 : original.end_lineno]
        )
        + "\n"
        + "\n".join("+" + line for line in wrapper.splitlines())
    )
patch += "\n*** End Patch"
print(
    json.dumps(
        {"module": module, "dependencies": fields, "patch": patch}, ensure_ascii=True
    )
)
