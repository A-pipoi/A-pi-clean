from __future__ import annotations

from pathlib import Path

import pytest

from quant.research.ledger import ResearchLedger


def result(version: str = "baseline-v2") -> dict:
    return {"ok": True, "strategy": "baseline", "strategy_label": "横截面基准",
            "strategy_version": version, "metrics": {"sharpe": .3},
            "final_equity": 110, "equity_curve": [{"date": "2026-10-12", "equity": 100},
                                                     {"date": "2026-10-13", "equity": 110}]}


def complete(ledger: ResearchLedger, job: str, data: dict) -> None:
    ledger.create_backtest_job(job, {"strategy": "not_the_actual_result"})
    ledger.update_backtest_job(job, status="completed", result=data,
                               completed_at="2026-10-13T08:00:00+00:00")


def test_default_is_explicit_persistent_and_account_isolated(tmp_path: Path) -> None:
    ledger = ResearchLedger(tmp_path / "one.sqlite3")
    other = ResearchLedger(tmp_path / "two.sqlite3")
    complete(ledger, "BT-one", result())
    assert ledger.get_default_backtest_display() is None
    saved = ledger.set_default_backtest_display("BT-one")
    assert saved["strategy"] == "baseline"
    assert saved["strategy_version"] == "baseline-v2"
    assert saved["backtest_date"] == "2026-10-13T08:00:00+00:00"
    assert saved["display_only"] is True
    assert ResearchLedger(ledger.path).get_default_backtest_display() == saved
    assert other.get_default_backtest_display() is None
    with pytest.raises(ValueError):
        other.set_default_backtest_display("BT-one")
    assert ledger.count("baseline") == 0


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
def test_unsuccessful_selection_preserves_previous(tmp_path: Path, status: str) -> None:
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    complete(ledger, "BT-original", result())
    saved = ledger.set_default_backtest_display("BT-original")
    ledger.create_backtest_job("BT-next", {})
    ledger.update_backtest_job("BT-next", status=status)
    with pytest.raises(ValueError):
        ledger.set_default_backtest_display("BT-next")
    assert ledger.get_default_backtest_display() == saved
    complete(ledger, "BT-success-next", result("new-version"))
    assert ledger.get_default_backtest_display() == saved


def test_old_version_not_inferred_and_snapshot_not_relabelled(tmp_path: Path) -> None:
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    old = result()
    old.pop("strategy_version")
    complete(ledger, "BT-old", old)
    saved = ledger.set_default_backtest_display("BT-old")
    assert saved["strategy_version"] == "未记录（旧产物）"
    ledger.update_backtest_job("BT-old", status="failed", result=result("changed"))
    assert ledger.get_default_backtest_display() == saved


@pytest.mark.parametrize("bad", [[], [{"date": "2026-10-12", "equity": "bad"}],
                                  [{"date": "2026-10-12", "equity": float("nan")}]] )
def test_invalid_artifact_rejected(tmp_path: Path, bad: list) -> None:
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    data = result()
    data["equity_curve"] = bad
    complete(ledger, "BT-bad", data)
    with pytest.raises(ValueError):
        ledger.set_default_backtest_display("BT-bad")
    assert ledger.get_default_backtest_display() is None
