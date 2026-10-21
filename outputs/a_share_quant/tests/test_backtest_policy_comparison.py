from __future__ import annotations

from quant.backtest.comparison import (
    build_policy_comparison,
    run_four_policy_comparison,
)


def cell(experiment_id: int, sell: bool = False) -> dict[str, object]:
    return {
        "ok": True,
        "research": {"experiment_id": experiment_id},
        "metrics": {
            "annualized_return": 0.1,
            "sharpe": 1.0,
            "max_drawdown": -0.2,
            "turnover": 2.0,
        },
        "equity_curve": [{"date": "2024-01-04"}, {"date": "2024-01-05"}],
        "fills_detail": [
            {"symbol": "A.SH", "side": "SELL", "date": "2024-01-05"}
        ] if sell else [],
    }


def test_four_policy_runner_uses_one_snapshot_and_persists_report() -> None:
    shared_snapshot = object()
    seen: list[tuple[dict[str, object], object]] = []
    saved: list[tuple[int, dict[str, object]]] = []

    def run_cell(payload, snapshot):
        seen.append((payload, snapshot))
        return 200, cell(len(seen), payload["signal_adjust"] == "raw" and payload["corporate_actions"] == "off")

    report = run_four_policy_comparison(
        {"strategy": "baseline"},
        shared_snapshot,
        remaining_budget=4,
        run_cell=run_cell,
        persist=lambda experiment_id, value: saved.append((experiment_id, value)),
        event_dates={"A.SH": "2024-01-04"},
    )

    assert len(seen) == 4
    assert all(snapshot is shared_snapshot for _, snapshot in seen)
    assert report["ex_date_false_sell_count"] == 1
    assert report["cells"][0]["annualized_turnover"] == 252
    assert saved[0][0] == 4
    assert saved[0][1]["status"] == "complete"


def test_budget_preflight_does_not_start_any_cell() -> None:
    def should_not_run(*_args):
        raise AssertionError("cell started before budget preflight")

    try:
        run_four_policy_comparison(
            {}, object(), remaining_budget=3, run_cell=should_not_run,
            persist=should_not_run, event_dates={},
        )
    except ValueError as error:
        assert "four" in str(error)
    else:
        raise AssertionError("expected budget preflight failure")


def test_comparison_rejects_partial_quadrants() -> None:
    try:
        build_policy_comparison({}, {})
    except ValueError as error:
        assert "incomplete" in str(error)
    else:
        raise AssertionError("expected incomplete comparison failure")
