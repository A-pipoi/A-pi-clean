import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from quant.api.paper_status import PaperStatusDependencies, paper_sop_payload


def test_status_universe_failure_remains_unaccepted_and_does_not_log_secret(caplog):
    china = timezone(timedelta(hours=8))
    now = datetime(2026, 10, 12, 18, 0, tzinfo=china)
    store = Mock()
    store.cycle_summary.return_value = {
        "cycles": 0,
        "orders": 0,
        "equity_observations": 0,
    }
    store.recent_cycles.return_value = []
    scheduler = Mock()
    scheduler.status.return_value = {"universe_index": "000300.SH", "universe": None}
    scheduler.resolve_universe_for_status.side_effect = RuntimeError("token=never-log")
    gate = Mock(return_value={"passed": False, "reason": "missing_pit_universe"})
    deps = PaperStatusDependencies(
        store=store,
        scheduler=lambda: scheduler,
        timezone=china,
        logger=logging.getLogger("paper-status-test"),
        object_mapping=lambda value: dict(value) if isinstance(value, dict) else {},
        optional_date=lambda value: None,
        enrich_state=lambda value: value,
        risk_limits_payload=dict,
        factor_registry_status=lambda value: {},
        paper_sealed_gate_status=gate,
        sealed_public_payload=lambda value: {},
        ledger_status=lambda value: {},
    )
    with patch("quant.api.paper_status.datetime") as clock:
        clock.now.return_value = now
        result = paper_sop_payload(deps)
    scheduler.resolve_universe_for_status.assert_called_once_with(now.date())
    gate.assert_called_once_with(scheduler.status.return_value)
    assert result["ready_for_stage_d"] is False
    assert result["paper_sealed_gate"]["passed"] is False
    assert result["stage"] == "A/B"
    assert "never-log" not in str(result)
    assert "RuntimeError" in caplog.text
    assert "never-log" not in caplog.text
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None
