from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from quant.api.mining_research import (
    MINING_REQUEST_ID,
    _evaluate_factor_expression_request,
    _run_factor_mining_request,
)
from quant.research import ResearchLedger
from tests.test_factor_mining import _bars


def test_invalid_manual_expression_is_counted_without_registration(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    bars = _bars()
    deps = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        _validate_factor_mining_universe_request=lambda _: None,
        _factor_mining_dataset=lambda: ({"id": 17, "data_version": "v1"}, bars, bars),
        _as_int=int,
        _pit_observations_for_bars=lambda *_: ([], [], {}, {}),
    )
    with pytest.raises(ValueError) as error:
        _evaluate_factor_expression_request({"expression": "close /"}, deps=deps)
    assert error.value.api_code == "expression_syntax_error"
    assert ledger.factor_mining_trial_total(17) == 1
    run = ledger.factor_mining_runs()[0]
    assert run["status"] == "evaluation_failed" and run["result"]["candidates"] == []


def test_partial_or_postprocessing_failure_cannot_refund_counted_hypotheses(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3")

    def fail(*_args, **_kwargs):
        run_id = MINING_REQUEST_ID.get()
        ledger.save_factor_mining_run(
            run_id,
            sealed_test_id=17,
            data_version="v",
            n_tested=0,
            result={"candidates": []},
            status="evaluation_in_progress",
        )
        ledger.record_factor_mining_attempts(run_id, 8)
        ledger.record_factor_mining_attempts(run_id, 4)
        raise RuntimeError("provider unavailable during comparison")

    with (
        patch(
            "quant.api.mining_research._run_factor_mining_request_impl",
            side_effect=fail,
        ),
        pytest.raises(RuntimeError),
    ):
        _run_factor_mining_request({}, deps=SimpleNamespace(RESEARCH_LEDGER=ledger))
    assert ledger.factor_mining_trial_total(17) == 8
    assert ledger.factor_mining_runs()[0]["status"] == "evaluation_failed"


def test_acceptance_waits_for_exploratory_run_to_finalize_without_recounting(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    ledger.save_factor_mining_run(
        "r",
        sealed_test_id=17,
        data_version="v",
        n_tested=8,
        result={},
        status="evaluation_in_progress",
    )
    assert (
        ledger.claim_factor_mining_acceptance("r")["code"]
        == "factor_mining_still_running"
    )
    ledger.update_factor_mining_run("r", {"candidates": []})
    assert ledger.factor_mining_trial_total(17) == 8
    assert ledger.claim_factor_mining_acceptance("r")["allowed"]
    assert not ledger.claim_factor_mining_acceptance("r")["allowed"]


def test_manual_report_failure_is_also_counted(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    bars = _bars()
    deps = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        _validate_factor_mining_universe_request=lambda _: None,
        _factor_mining_dataset=lambda: ({"id": 17, "data_version": "v1"}, bars, bars),
        _as_int=int,
        _pit_observations_for_bars=lambda *_: ([], [], {}, {}),
    )
    with (
        patch(
            "quant.api.mining_research.evaluate_factor_panel",
            side_effect=RuntimeError("report failed"),
        ),
        pytest.raises(RuntimeError),
    ):
        _evaluate_factor_expression_request({"expression": "close"}, deps=deps)
    assert ledger.factor_mining_trial_total(17) == 1
    assert ledger.factor_mining_runs()[0]["status"] == "evaluation_failed"


def test_nonempty_gp_candidates_evaluate_only_real_training_range(tmp_path):
    ledger = ResearchLedger(tmp_path / "research.sqlite3")
    all_bars = _bars(days=90)
    cutoff = sorted({bar.date for bar in all_bars})[60]
    training = list(reversed([bar for bar in all_bars if bar.date < cutoff]))
    factor_store = Mock()
    factor_store.definitions.return_value = []
    factor_store.expression_library.return_value = []
    factor_store.save_mined_candidates.return_value = [
        {"expression": "close", "key": "candidate-close"}
    ]
    deps = SimpleNamespace(
        RESEARCH_LEDGER=ledger,
        FACTOR_STORE=factor_store,
        _validate_factor_mining_universe_request=lambda _: None,
        _factor_mining_dataset=lambda: (
            {
                "id": 17,
                "data_version": "v1",
                "sealed_start": str(cutoff),
                "sealed_end": str(max(bar.date for bar in all_bars)),
            },
            all_bars,
            training,
        ),
        _as_int=int,
        _bounded_int=lambda value, low, high: max(low, min(high, int(value))),
        _pit_observations_for_bars=lambda *_: ([], [], {}, {}),
        _object_list=lambda value: value if isinstance(value, list) else [],
        _object_mapping=dict,
        _factor_mining_cross_universe_reference=Mock(return_value={}),
    )

    def mine(_bars, **options):
        options["attempt_callback"](4)
        return SimpleNamespace(
            as_dict=lambda: {
                "n_tested": 4,
                "candidates": [{"expression": "close"}],
            }
        )

    with (
        patch.dict("os.environ", {"TUSHARE_TOKEN": "isolated-test-only"}),
        patch("quant.api.mining_research.run_factor_mining", side_effect=mine),
        patch(
            "quant.api.mining_research.evaluate_factor_panel",
            return_value={"horizons": {}},
        ) as evaluate,
        patch("quant.api.mining_research.training_portfolios", return_value={}),
        patch("quant.api.mining_research.queue_decay_alerts", return_value={}),
    ):
        saved = _run_factor_mining_request({}, deps=deps)
    panel = evaluate.call_args.args[0]
    options = evaluate.call_args.kwargs
    assert options["start"] == min(bar.date for bar in training)
    assert options["end"] == max(bar.date for bar in training) < cutoff
    assert all(day < cutoff for day in panel.dates)
    assert options["n_tested"] == ledger.factor_mining_trial_total(17) == 4
    assert saved["result"]["candidates"][0]["factor_key"] == "candidate-close"
    factor_store.save_mined_candidates.assert_called_once()
