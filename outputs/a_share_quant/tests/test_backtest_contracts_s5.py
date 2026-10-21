"""Backtest contract: preset params, audit preflight, progress stages."""

from __future__ import annotations

import unittest
from pathlib import Path

import pytest

from quant.data.extension_store import ExtensionStore
from quant.data.research_eligibility import preflight_audit_evidence
from quant.strategies import StrategyPresetConflict, resolve_strategy_config


def test_resolve_strategy_config_exposes_expected_fields():
    with pytest.raises(StrategyPresetConflict) as raised:
        resolve_strategy_config("momentum", 12, 10)
    err = raised.value
    assert err.code == "strategy_preset_conflict"
    assert err.expected == {"strategy": "momentum", "lookback": 12, "top_n": 5}
    assert err.got == {"lookback": 12, "top_n": 10}


def test_preflight_skips_when_audit_filter_disabled(tmp_path):
    store = ExtensionStore(tmp_path / "raw.sqlite3")
    store.initialize()
    assert (
        preflight_audit_evidence({"data_quality_policy": True}, ["600000.SH"], store=store)[
            "required"
        ]
        is False
    )


def test_preflight_blocks_when_audit_enabled_without_evidence(tmp_path):
    store = ExtensionStore(tmp_path / "raw.sqlite3")
    store.initialize()
    with pytest.raises(ValueError, match="审计"):
        preflight_audit_evidence(
            {"data_quality_policy": True, "exclude_nonstandard_audit": True},
            ["600000.SH"],
            store=store,
        )




def test_preflight_blocks_partial_fina_audit_coverage(tmp_path):
    """One covered symbol must not let a sparse universe pass preflight."""
    store = ExtensionStore(tmp_path / "raw.sqlite3")
    store.initialize()
    from quant.data.extension_sync import sync

    sync(
        store,
        "fina_audit",
        {},
        "",
        fetch=lambda *a: [
            {
                "ts_code": "000001.SZ",
                "ann_date": "20240115",
                "end_date": "20231231",
                "audit_result": "标准无保留意见",
            }
        ],
    )
    with pytest.raises(ValueError, match="覆盖不足|fina_audit"):
        preflight_audit_evidence(
            {"data_quality_policy": True, "exclude_nonstandard_audit": True},
            ["000001.SZ", "600519.SH"],
            store=store,
        )

class BacktestProgressContractTests(unittest.TestCase):
    def test_progress_helpers_exist_in_service_source(self) -> None:
        text = Path("quant/api/backtest_service.py").read_text(encoding="utf-8")
        self.assertIn("正在解析回测宇宙", text)
        self.assertIn("preflight_audit_evidence(payload, list(symbols))", text)
        self.assertIn("DATA_PROGRESS.set(_on_data_progress)", text)
        self.assertIn("行情拉取完成", text)
        self.assertIn("StrategyPresetConflict as error", text)
