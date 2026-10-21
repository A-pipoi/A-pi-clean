"""Exercise helpers without executing archival, service or model workflows."""

import ast
import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from types import CodeType, FunctionType

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
GATED_SCRIPTS = [
    "scripts/auth_acceptance_server.py",
    "scripts/plan_api_extraction.py",
    "scripts/plan_service_extraction.py",
    "scripts/provider_crash_acceptance.py",
    "scripts/verify_backtest_safety_http.py",
    "deep_factor/llm_arena.py",
    "deep_factor/verify_deepseek_evolution.py",
]


def isolated_helper(source, name, namespace, optimization=0):
    helper = next(
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    module = compile(
        ast.Module(body=[helper], type_ignores=[]),
        "isolated-helper",
        "exec",
        optimize=optimization,
    )
    code = next(
        value
        for value in module.co_consts
        if isinstance(value, CodeType) and value.co_name == name
    )
    return FunctionType(code, namespace)


@pytest.mark.parametrize("optimization", [0, 1, 2])
def test_formal_service_checks_survive_optimization(optimization):
    source = ast.parse((SCRIPTS / "verify_formal_service.py").read_text("utf-8"))
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(source))
    require = isolated_helper(source, "require", {}, optimization)
    require(True, "pass")
    with pytest.raises(RuntimeError, match="^fixture gate rejected$"):
        require(False, "fixture gate rejected")


@pytest.mark.parametrize("optimization", [0, 1, 2])
@pytest.mark.parametrize("relative", GATED_SCRIPTS)
def test_verification_guards_are_not_optimized_away(relative, optimization):
    source = ast.parse((SCRIPTS.parent / relative).read_text("utf-8"))
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(source))
    gates = [
        node
        for node in ast.walk(source)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and any(
            isinstance(child, ast.Raise)
            and isinstance(child.exc, ast.Call)
            and isinstance(child.exc.func, ast.Name)
            and child.exc.func.id == "AssertionError"
            for child in node.body
        )
    ]
    assert gates
    for gate in gates:
        # Force only the isolated gate's condition to fail, not the real workflow.
        guard = copy.deepcopy(gate)
        guard.test = ast.Constant(value=True)
        guard.body = [
            ast.Raise(
                exc=ast.Call(
                    func=ast.Name(id="AssertionError", ctx=ast.Load()),
                    args=[ast.Constant(value="fixture rejected")],
                    keywords=[],
                )
            )
        ]
        helper = ast.FunctionDef(
            name="fixture",
            args=ast.arguments(
                posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
            ),
            body=[guard],
            decorator_list=[],
        )
        tree = ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[]))
        check = isolated_helper(tree, "fixture", {}, optimization)
        with pytest.raises(AssertionError, match="^fixture rejected$"):
            check()


def test_minimal_import_child_program_has_explicit_checks():
    source = ast.parse((SCRIPTS / "verify_minimal_import.py").read_text("utf-8"))
    assignment = next(
        node
        for node in ast.walk(source)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "code"
            for target in node.targets
        )
    )
    child = ast.parse(ast.literal_eval(assignment.value))
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(child))
    assert len([node for node in ast.walk(child) if isinstance(node, ast.Raise)]) == 2


def test_seal_fingerprint_quotes_schema_identifiers():
    source = ast.parse(
        (SCRIPTS / "archive_approved_baseline_cycle.py").read_text("utf-8")
    )
    fingerprint = isolated_helper(
        source, "preserved_seals", {"hashlib": hashlib, "json": json}
    )
    # In-memory fixture only. A comma/quote in a real SQLite column is data, not SQL.
    with sqlite3.connect(":memory:") as database:
        database.execute(
            'CREATE TABLE sealed_tests(id INTEGER, "odd""name,expression" TEXT, archived_at TEXT)'
        )
        database.execute(
            "INSERT INTO sealed_tests VALUES(1, ?, ?)", ("fixture", "first")
        )
        before = fingerprint(database)
        database.execute("UPDATE sealed_tests SET archived_at='second'")
        assert fingerprint(database) == before
        database.execute(
            'UPDATE sealed_tests SET "odd""name,expression"=?', ("changed",)
        )
        assert fingerprint(database) != before
