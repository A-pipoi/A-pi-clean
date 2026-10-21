"""Reporting helpers for the four raw/adjusted and actions-off/on policies."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

POLICIES = (
    ("raw", "off"),
    ("adj", "off"),
    ("raw", "on"),
    ("adj", "on"),
)


def build_policy_comparison(
    results: Mapping[tuple[str, str], Mapping[str, Any]],
    event_dates: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Create a comparable, explicit report; values come from ordinary runs."""
    missing = [f"{signal}/{actions}" for signal, actions in POLICIES if (signal, actions) not in results]
    if missing:
        raise ValueError(f"incomplete policy comparison: missing {', '.join(missing)}")
    cells: list[dict[str, object]] = []
    for signal, actions in POLICIES:
        result = results[(signal, actions)]
        metrics = result.get("metrics")
        if not isinstance(metrics, Mapping):
            raise TypeError(f"missing metrics for {signal}/{actions}")
        equity_curve = result.get("equity_curve", [])
        session_count = len(equity_curve) if isinstance(equity_curve, list) else 0
        turnover = metrics.get("turnover")
        annualized_turnover = (
            float(turnover) * 252 / session_count
            if turnover is not None and session_count > 0 else None
        )
        cells.append({
            "signal_adjust": signal,
            "corporate_actions": actions,
            "annualized_return_after_cost": metrics.get("annualized_return"),
            "sharpe_after_cost": metrics.get("sharpe"),
            "max_drawdown": metrics.get("max_drawdown"),
            "annualized_turnover": annualized_turnover,
            "experiment_id": (result.get("research") or {}).get("experiment_id")
            if isinstance(result.get("research"), Mapping) else None,
        })
    if event_dates is None:
        event_dates = _observed_event_dates(results[("raw", "on")])
    adjusted_dates = _ex_date_next_session_sells(results[("adj", "off")], event_dates)
    raw_dates = _ex_date_next_session_sells(results[("raw", "off")], event_dates)
    raw_only = sorted(raw_dates - adjusted_dates)
    return {
        "version": "price-consistency-p2-v1",
        "status": "complete",
        "cells": cells,
        "ex_date_false_sell_count": len(raw_only),
        "ex_date_false_sell_definition": (
            "distinct symbol/next-session sell pairs present in raw/off but absent in adj/off; "
            "diagnostic proxy, not proof each sell was false"
        ),
        "ex_date_raw_only_sell_pairs": [
            {"symbol": symbol, "session": session} for symbol, session in raw_only
        ],
    }


def persist_policy_comparison(ledger: Any, experiment_id: int, report: dict[str, object]) -> None:
    """Attach the final four-cell report to an existing ordinary budget entry."""
    if report.get("status") != "complete":
        raise ValueError("only complete policy comparisons may be persisted")
    ledger.update_experiment_parameters(experiment_id, {"policy_comparison": report})


def run_four_policy_comparison(
    payload: Mapping[str, Any],
    prepared_market_data: object,
    *,
    remaining_budget: int,
    run_cell: Callable[[dict[str, Any], object], tuple[int, dict[str, Any]]],
    persist: Callable[[int, dict[str, object]], None],
    event_dates: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Run four ordinary budgeted cells over one immutable prepared snapshot.

    ``prepared_market_data`` is deliberately passed by identity to every cell;
    callers must provide one PIT-clean, immutable bars/snapshot pack. Each cell
    still uses its normal experiment ledger and sealed-validation path.
    """
    if remaining_budget < len(POLICIES):
        raise ValueError("policy comparison requires four remaining research-budget slots")
    completed: dict[tuple[str, str], dict[str, Any]] = {}
    experiment_ids: list[int] = []
    for signal_adjust, corporate_actions in POLICIES:
        cell_payload = dict(payload)
        cell_payload.update({
            "signal_adjust": signal_adjust,
            "corporate_actions": corporate_actions,
            "kind": "backtest",
        })
        status, result = run_cell(cell_payload, prepared_market_data)
        if status != 200 or result.get("ok") is not True:
            raise RuntimeError(
                f"policy comparison incomplete at {signal_adjust}/{corporate_actions}; "
                f"{len(completed)} ordinary experiments may already have consumed budget"
            )
        completed[(signal_adjust, corporate_actions)] = result
        research = result.get("research")
        if isinstance(research, Mapping) and isinstance(research.get("experiment_id"), int):
            experiment_ids.append(research["experiment_id"])
    report = build_policy_comparison(completed, event_dates)
    report["experiment_ids"] = experiment_ids
    if not experiment_ids:
        raise ValueError("completed cells did not return ordinary research experiment identifiers")
    persist(experiment_ids[-1], report)
    return report


def _ex_date_next_session_sells(
    result: Mapping[str, Any], event_dates: Mapping[str, str]
) -> set[tuple[str, str]]:
    curve = result.get("equity_curve", [])
    sessions = sorted(
        str(item["date"]) for item in curve
        if isinstance(item, Mapping) and item.get("date")
    )
    next_sessions: dict[str, set[str]] = {}
    for event_key, ex_date in event_dates.items():
        symbol = event_key.rsplit("@", 1)[0]
        following = next((session for session in sessions if session > ex_date), None)
        if following:
            next_sessions.setdefault(symbol, set()).add(following)
    raw_fills = result.get("fills_detail", result.get("fills", []))
    output: set[tuple[str, str]] = set()
    for fill in raw_fills if isinstance(raw_fills, list) else []:
        if not isinstance(fill, Mapping):
            continue
        symbol = str(fill.get("symbol") or fill.get("ts_code") or "")
        side = str(fill.get("side") or fill.get("direction") or "").upper()
        session = str(fill.get("date") or "")
        if symbol in next_sessions and side == "SELL" and session in next_sessions[symbol]:
            output.add((symbol, session))
    return output


def _observed_event_dates(result: Mapping[str, Any]) -> dict[str, str]:
    report = result.get("corporate_action_report")
    entitlements = report.get("entitlements", []) if isinstance(report, Mapping) else []
    return {
        f"{item['symbol']}@{item['ex_date']}": str(item["ex_date"])
        for item in entitlements
        if isinstance(item, Mapping) and item.get("symbol") and item.get("ex_date")
    }
