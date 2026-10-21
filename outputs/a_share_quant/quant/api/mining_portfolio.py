"""Server-owned cycle policy and read-only portfolio report refresh."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import Any

from quant.factors.expression import build_factor_panel, evaluate_expression
from quant.mining.portfolio_metrics import (
    PortfolioPolicy,
    deflated_sharpe,
    portfolio_report,
)


def cycle_policy(path: Path, sealed_id: int) -> PortfolioPolicy:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='portfolio_policies'"
        ).fetchone()
        row = (
            db.execute(
                "SELECT policy FROM portfolio_policies WHERE sealed_id=?", (sealed_id,)
            ).fetchone()
            if exists
            else None
        )
    return PortfolioPolicy.parse(json.loads(row[0])) if row else PortfolioPolicy()


def set_cycle_policy(path: Path, sealed_id: int, payload: dict) -> dict:
    policy = PortfolioPolicy.parse(payload)
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT status,archived_at FROM sealed_tests WHERE id=?", (sealed_id,)
        ).fetchone()
        count = db.execute(
            "SELECT COALESCE(SUM(n_tested),0) FROM factor_mining_runs "
            "WHERE sealed_test_id=?",
            (sealed_id,),
        ).fetchone()[0]
        acceptance = db.execute(
            "SELECT 1 FROM factor_mining_acceptances WHERE sealed_test_id=?",
            (sealed_id,),
        ).fetchone()
        arena_exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='arena_seasons'"
        ).fetchone()
        arena = arena_exists and db.execute(
            "SELECT 1 FROM arena_seasons WHERE sealed_id=?", (sealed_id,)
        ).fetchone()
        if not row or row[0] != "sealed" or row[1] or count or acceptance or arena:
            raise PermissionError(
                "Pre-register portfolio policy before the first counted trial; "
                "and before arena creation; archive for changes"
            )
        db.execute(
            "CREATE TABLE IF NOT EXISTS portfolio_policies("
            "sealed_id INTEGER PRIMARY KEY,policy TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO portfolio_policies VALUES(?,?) ON CONFLICT(sealed_id) "
            "DO UPDATE SET policy=excluded.policy",
            (sealed_id, json.dumps(asdict(policy))),
        )
    return asdict(policy)


def trial_population(runs: list[dict], quantiles: str = "10") -> list[float]:
    result: list[float] = []
    for run in runs:
        document = run.get("result", {})
        generations = document.get("generation_log", [])
        if generations and quantiles == "10":
            result.extend(
                value
                for generation in generations
                for item in generation.get("population", [])
                if type(value := item.get("portfolio_daily_sharpe")) in (int, float)
                and math.isfinite(value)
            )
            continue
        # Each stored attempted GP individual is represented, not just winners.
        attempts = document.get("portfolio_trial_sharpes")
        if isinstance(attempts, list):
            result.extend(
                x for x in attempts if type(x) in (int, float) and math.isfinite(x)
            )
            continue
        for candidate in document.get("candidates", []):
            report = (
                candidate.get("evaluation", {}).get("portfolio", {}).get(quantiles, {})
            )
            value = report.get("psr", {}).get("daily_sharpe")
            if type(value) in (int, float) and math.isfinite(value):
                result.append(value)
    return result


def training_portfolios(
    panel: Any,
    values: Any,
    *,
    ledger: Any,
    sealed_id: int,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    policy = cycle_policy(ledger.path, sealed_id)
    total = ledger.factor_mining_trial_total(sealed_id)
    runs = ledger.factor_mining_runs_for_sealed_test(sealed_id)
    return {
        str(q): portfolio_report(
            panel,
            values,
            n_tested=total,
            policy=replace(policy, quantiles=q),
            trial_sharpes=trial_population(runs, str(q)),
            start=start,
            end=end,
        )
        for q in (5, 10)
    }


def refresh_portfolios(report: dict, *, ledger: Any, sealed_id: int) -> dict:
    """Refresh N from the ledger without reopening prices or charging trials."""
    total = ledger.factor_mining_trial_total(sealed_id)
    runs = ledger.factor_mining_runs_for_sealed_test(sealed_id)
    for quantiles, item in report.get("portfolio", {}).items():
        returns = [row["return"] for row in item.get("curve", [])]
        item["dsr"] = deflated_sharpe(
            returns, n_tested=total, trial_sharpes=trial_population(runs, quantiles)
        )
    return report


def factor_portfolio_request(payload: dict, *, deps: Any) -> dict:
    if set(payload) != {"factor_key"} or not isinstance(payload["factor_key"], str):
        raise ValueError("Only factor_key is accepted; trial count is server-owned")
    key = payload["factor_key"]
    definitions = {
        item.key: item for item in deps.FACTOR_STORE.definitions(enabled_only=False)
    }
    library = {item["key"]: item for item in deps.FACTOR_STORE.expression_library()}
    if key not in definitions and key not in library:
        raise LookupError("Factor not found")
    sealed, bars, training = deps._factor_mining_dataset()
    if deps.RESEARCH_LEDGER.factor_mining_acceptance(int(sealed["id"])):
        # Do not fetch a new training history following sealed disclosure.
        raise PermissionError("Review saved training reports after sealed acceptance")
    observations, industries, quality, _ = deps._pit_observations_for_bars(sealed, bars)
    dates = {bar.date for bar in training}
    panel = build_factor_panel(
        training,
        data_version=str(sealed["data_version"]),
        observations=[x for x in observations if x.as_of in dates],
        industry_observations=[x for x in industries if x.as_of in dates],
        pit_quality=quality,
    )
    if key in library:
        item = library[key]
        if int(item["sealed_test_id"]) != int(sealed["id"]):
            raise PermissionError(
                "Factor belongs to a different cycle; use its saved report"
            )
        values = evaluate_expression(item["expression"], panel).values
    else:
        values = deps._factor_values_for_definition(definitions[key], training, panel)
    if values is None:
        return {
            "factor_key": key,
            "status": "unavailable",
            "reason": "PIT values unavailable",
        }
    return {
        "factor_key": key,
        "portfolio": training_portfolios(
            panel, values, ledger=deps.RESEARCH_LEDGER, sealed_id=int(sealed["id"])
        ),
        "sealed_dates_used": False,
        "trial_count_changed": False,
    }
