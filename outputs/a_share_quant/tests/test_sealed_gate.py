import unittest
from unittest.mock import patch

from quant.api_server import (
    _paper_cycle_sealed_gate_payload,
    _paper_sealed_gate_status,
    _sealed_result_passed,
)


class SealedGateTests(unittest.TestCase):
    def test_later_snapshot_requires_identical_immutable_membership(self) -> None:
        import copy
        seal = {
            "id": 4, "strategy_version": "baseline", "status": "passed",
            "sealed_end": "2026-09-29",
            "metadata": {"research_universe": {"index_code": "000300.SH", "pit_membership": True}},
            "parameters": {"rebalance": "weekly"},
            "result": {"passed": True, "factor_snapshot_hash": "current"},
            "pit_data": {"index_code": "000300.SH", "snapshots": [
                {"trade_date": "2026-08-31", "members": [
                    {"ts_code": "000001.SZ", "weight": 1},
                    {"ts_code": "600036.SH", "weight": 2},
                ]},
            ]},
        }
        status = {
            "strategy": "baseline", "rebalance": "weekly",
            "symbols": ["600036.SH", "000001.SZ"], "universe_index": "000300.SH",
            "universe": {"mode": "pit_index_members", "index_code": "000300.SH",
                         "membership_date": "2026-09-30", "count": 2},
        }
        cases = ["same", "changed", "missing", "future", "duplicate", "stale", "factor", "frequency"]
        for case in cases:
            with self.subTest(case=case):
                candidate, request = copy.deepcopy(seal), copy.deepcopy(status)
                if case == "changed":
                    request["symbols"][0] = "600000.SH"
                elif case == "missing":
                    candidate.pop("pit_data")
                elif case == "future":
                    candidate["pit_data"]["snapshots"][0]["trade_date"] = "2026-09-30"
                elif case == "duplicate":
                    candidate["pit_data"]["snapshots"][0]["members"][1]["ts_code"] = "000001.SZ"
                elif case == "stale":
                    request["universe"]["stale"] = True
                elif case == "frequency":
                    request["rebalance"] = "daily"
                with (
                    patch("quant.api_server.RESEARCH_LEDGER") as ledger,
                    patch.object(ledger, "latest_sealed_test_for_universe", return_value=candidate),
                    patch("quant.api_server._factor_snapshot_hash", return_value="changed" if case == "factor" else "current"),
                ):
                    result = _paper_sealed_gate_status(request)
                self.assertEqual(result["passed"], case == "same")
                if case == "same":
                    self.assertEqual(result["membership_evidence"]["mode"], "identical_constituents")

    def test_paper_gate_uses_resolved_pit_constituents_not_default_seed_pool(self) -> None:
        symbols = [f"{number:06d}.SZ" for number in range(300)]
        scheduler_status = {
            "strategy": "baseline",
            "symbols": symbols,
            "symbol_count": 300,
            "universe_index": "000300.SH",
            "universe": {
                "mode": "pit_index_members",
                "index_code": "000300.SH",
                "count": 300,
                "membership_date": "2026-09-28",
            },
        }
        seal = {
            "id": 31,
            "strategy_version": "baseline",
            "status": "passed",
            "sealed_start": "2026-07-01",
            "sealed_end": "2026-09-29",
            "metadata": {
                "research_universe": {
                    "mode": "pit_historical_index",
                    "pit_membership": True,
                    "index_code": "000300.SH",
                }
            },
            "parameters": {
                "sealed_acceptance_protocol": "pit_historical_index_v1",
                "rebalance": "weekly",
            },
            "result": {"passed": True, "factor_snapshot_hash": "current"},
        }
        with (
            patch("quant.api_server.RESEARCH_LEDGER") as ledger,
            patch.object(ledger, "latest_sealed_test_for_universe", return_value=seal) as lookup,
            patch("quant.api_server._factor_snapshot_hash", return_value="current"),
        ):
            result = _paper_sealed_gate_status(scheduler_status)

        lookup.assert_called_once_with("baseline", index_code="000300.SH", protocol="pit_historical_index_v1", include_bars=False)
        ledger.record_sealed_result.assert_not_called()
        ledger.create_sealed_test.assert_not_called()
        ledger.record.assert_not_called()
        self.assertTrue(result["passed"])
        self.assertEqual(result["symbol_count"], 300)
        self.assertEqual(result["universe"]["membership_date"], "2026-09-28")

    def test_pit_gate_stays_blocked_until_membership_is_resolved(self) -> None:
        scheduler_status = {
            "strategy": "baseline",
            "symbols": ["000001"],  # legacy fallback must not be treated as the PIT pool
            "universe_index": "000300.SH",
            "universe": None,
        }
        with patch("quant.api_server._sealed_gate_payload") as lookup:
            result = _paper_sealed_gate_status(scheduler_status)

        lookup.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "unresolved_universe")

    def test_manual_cycle_gate_uses_resolved_pit_universe_and_rebalance(self) -> None:
        symbols = ["000001.SZ", "600000.SH"]
        universe = {
            "mode": "pit_index_members",
            "index_code": "000300.SH",
            "membership_date": "2026-09-28",
            "count": 2,
        }
        with patch("quant.api_server._paper_sealed_gate_status", return_value={"passed": True}) as gate:
            result = _paper_cycle_sealed_gate_payload("baseline", symbols, "", universe, "weekly")

        self.assertTrue(result["passed"])
        gate.assert_called_once_with(
            {
                "strategy": "baseline",
                "symbols": symbols,
                "universe_index": "000300.SH",
                "universe": universe,
                "rebalance": "weekly",
            }
        )

    def test_manual_cycle_gate_rejects_explicit_pool_when_paper_is_pit_configured(self) -> None:
        with patch("quant.api_server._sealed_gate_payload") as fixed_gate:
            result = _paper_cycle_sealed_gate_payload(
                "baseline", ["000001.SZ"], "", {"mode": "explicit_symbols"}, "weekly"
            )

        fixed_gate.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "unresolved_universe")

    def test_seal_requires_passed_result_and_current_factor_snapshot(self) -> None:
        seal = {
            "status": "passed",
            "result": {"passed": True, "factor_snapshot_hash": "current"},
        }
        with patch("quant.api_server._factor_snapshot_hash", return_value="current"):
            self.assertTrue(_sealed_result_passed(seal))
        with patch("quant.api_server._factor_snapshot_hash", return_value="changed"):
            self.assertFalse(_sealed_result_passed(seal))
        seal["result"]["passed"] = False
        with patch("quant.api_server._factor_snapshot_hash", return_value="current"):
            self.assertFalse(_sealed_result_passed(seal))


if __name__ == "__main__":
    unittest.main()
