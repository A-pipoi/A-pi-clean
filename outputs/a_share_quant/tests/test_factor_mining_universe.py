import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quant.data.adapters import fetch_tushare_listing_dates
from quant.data.filters import UniverseRules, filter_universe
from quant.data.index_members import fetch_tushare_index_member_history
from quant.data.schema import Bar
from quant.data.universe import (
    FACTOR_MINING_MIN_LISTING_DAYS,
    FACTOR_MINING_UNIVERSE_INDEX_CODE,
    HistoricalUniverse,
)
from quant.time_utils import parse_yyyymmdd


class _Frame:
    def __init__(self, rows):
        self.rows = rows
        self.empty = not rows

    def to_dict(self, orient="records"):
        return list(self.rows)


class FactorMiningUniverseTests(unittest.TestCase):
    def test_factor_universe_is_one_pre_registered_index_not_paper_seed_pool(self) -> None:
        from quant import api_server

        scope, pipeline = api_server._factor_mining_scope()
        self.assertEqual(FACTOR_MINING_UNIVERSE_INDEX_CODE, "000300.SH")
        self.assertIn("hs300-monthly-pit-v1", pipeline)
        self.assertNotEqual(scope, api_server._sealed_scope(api_server.DEFAULT_SYMBOLS, ""))
        self.assertEqual(FACTOR_MINING_MIN_LISTING_DAYS, 60)
        api_server._validate_factor_mining_universe_request({"universe_index_code": "000300.SH"})
        with self.assertRaisesRegex(ValueError, "不能切换指数"):
            api_server._validate_factor_mining_universe_request({"universe_index_code": "000905.SH"})
        with self.assertRaisesRegex(ValueError, "自选标的"):
            api_server._validate_factor_mining_universe_request({"symbols": ["000001.SZ"]})

    def test_csi500_reference_is_descriptive_and_cannot_enter_acceptance_gate(self) -> None:
        from quant import api_server
        from quant.factors.expression import build_factor_panel
        from tests.test_factor_mining import _bars

        bars = _bars(110, ("A", "B", "C", "D", "E"))
        panel = build_factor_panel(bars, data_version="csv-reference-fixture")
        with patch.object(api_server, "_factor_mining_comparison_panel", return_value=(panel, "fixture-v1")):
            reference = api_server._factor_mining_cross_universe_reference(
                ["cs_rank(close)"], start_date=bars[0].date, end_date=bars[-1].date
            )
        candidate_reference = reference["expressions"]["cs_rank(close)"]
        self.assertEqual(reference["index_code"], "000905.SH")
        self.assertEqual(reference["role"], "report_only_not_for_selection_or_acceptance")
        self.assertFalse(reference["significance_used_for_decision"])
        self.assertEqual(candidate_reference["status"], "available")
        self.assertNotIn("dsr_p_value", candidate_reference["horizons"]["5"])
        self.assertNotIn("bh_adjusted_p_value", candidate_reference["horizons"]["5"])

    def test_monthly_index_history_keeps_all_snapshots_and_cache_version(self) -> None:
        all_rows = [
            {"index_code": "000300.SH", "con_code": "000001.SZ", "trade_date": "2023-06-30", "weight": 0.3},
            {"index_code": "000300.SH", "con_code": "600000.SH", "trade_date": "2024-01-31", "weight": 0.4},
            {"index_code": "000300.SH", "con_code": "600000.SH", "trade_date": "2024-01-31", "weight": 0.2},
            {"index_code": "000300.SH", "con_code": "000002.SZ", "trade_date": "2024-07-31", "weight": 0.5},
        ]

        def query(_token, _api, params, **_kwargs):
            start = parse_yyyymmdd(params["start_date"])
            end = parse_yyyymmdd(params["end_date"])
            return _Frame([row for row in all_rows if start <= date.fromisoformat(row["trade_date"]) <= end])

        with tempfile.TemporaryDirectory() as directory, patch(
            "quant.data.index_members._tushare_http_query", side_effect=query
        ) as mocked:
            result = fetch_tushare_index_member_history(
                "000300.SH",
                start_date=date(2023, 12, 15),
                end_date=date(2024, 8, 15),
                token="test-token",
                cache_dir=Path(directory),
            )
            first_call_count = mocked.call_count
            cached = fetch_tushare_index_member_history(
                "000300.SH",
                start_date=date(2023, 12, 15),
                end_date=date(2024, 8, 15),
                token="test-token",
                cache_dir=Path(directory),
            )

        self.assertGreater(first_call_count, 1)
        self.assertEqual(mocked.call_count, first_call_count)
        self.assertEqual(result["snapshot_count"], 3)
        self.assertEqual(result["data_version"], cached["data_version"])
        universe = HistoricalUniverse.from_index_weight_history(result)
        self.assertEqual(universe.symbols(date(2023, 12, 15)), frozenset({"000001.SZ"}))
        self.assertEqual(universe.symbols(date(2024, 3, 1)), frozenset({"600000.SH"}))
        self.assertEqual(universe.symbols(date(2024, 8, 1)), frozenset({"000002.SZ"}))
        self.assertEqual(
            universe.symbols_for_range(date(2023, 12, 15), date(2024, 8, 15)),
            frozenset({"000001.SZ", "600000.SH", "000002.SZ"}),
        )

    def test_history_without_snapshot_before_start_fails_closed(self) -> None:
        history = HistoricalUniverse({date(2024, 2, 1): {"600000.SH"}})
        with self.assertRaisesRegex(ValueError, "on or before"):
            history.symbols_for_range(date(2024, 1, 1), date(2024, 3, 1))

    def test_universe_summary_is_identical_in_sealed_metadata_and_parameters(self) -> None:
        from quant import api_server

        summary = {
            "schema_version": "hs300-monthly-pit-v1",
            "index_code": "000300.SH",
            "requested_start": "2024-01-01",
            "requested_end": "2025-12-31",
            "snapshot_start": "2023-01-31",
            "snapshot_end": "2025-12-31",
            "snapshot_count": 36,
            "member_data_version": "members-v1",
            "min_listing_days": 60,
            "candidate_symbol_count": 340,
            "sample_symbol_count": 310,
        }
        ledger = Mock()
        ledger.get_sealed_test.return_value = None
        ledger.create_sealed_test.return_value = {"id": 9}
        bars = [Bar("000001.SZ", date(2024, 1, 1) + timedelta(days=i), 1, 1, 1, 1, 1) for i in range(100)]
        with patch.object(api_server, "RESEARCH_LEDGER", ledger):
            created = api_server._ensure_sealed_snapshot(
                "baseline",
                "factor-mining-test-pipeline",
                bars,
                SimpleNamespace(metadata={}, version="fixture-v1"),
                symbols=["000001.SZ"],
                adjust="",
                sealed_days=30,
                pit_data={"research_universe": {"index_code": "000300.SH"}},
                pit_metadata={"research_universe": summary},
            )
        self.assertEqual(created["id"], 9)
        kwargs = ledger.create_sealed_test.call_args.kwargs
        self.assertEqual(kwargs["metadata"]["research_universe"], summary)
        self.assertEqual(kwargs["parameters"]["research_universe"], summary)

    def test_listing_age_and_membership_are_evaluated_at_each_historical_date(self) -> None:
        universe = HistoricalUniverse(
            {
                date(2024, 1, 1): {"000001.SZ", "600000.SH"},
                date(2024, 2, 1): {"600000.SH", "000002.SZ"},
            }
        )
        day = date(2024, 4, 1)
        bars = [
            Bar("000001.SZ", day, 10, 10, 10, 10, 1000),
            Bar("600000.SH", day, 10, 10, 10, 10, 1000),
            Bar("000002.SZ", day, 10, 10, 10, 10, 1000, is_st=True),
        ]
        filtered = filter_universe(
            bars,
            None,
            UniverseRules(min_listing_days=60, exclude_st=False, exclude_paused=False, exclude_delisted=False),
            listing_dates={
                "000001.SZ": date(2020, 1, 1),
                "600000.SH": date(2024, 3, 15),
                "000002.SZ": date(2024, 2, 1),
            },
            historical_universe=universe,
        )
        self.assertEqual([bar.symbol for bar in filtered], ["000002.SZ"])
        self.assertTrue(filtered[0].is_st)

    def test_listing_age_filter_requires_point_in_time_safe_listing_dates(self) -> None:
        with self.assertRaisesRegex(ValueError, "stock_basic.list_date"):
            filter_universe(
                [Bar("000001.SZ", date(2024, 1, 2), 1, 1, 1, 1, 1)],
                None,
                UniverseRules(min_listing_days=60),
            )

    def test_listing_dates_include_delisted_stock_basic_status(self) -> None:
        frames = {
            "L": _Frame([{"ts_code": "000001.SZ", "list_date": "19910403"}]),
            "D": _Frame([{"ts_code": "000002.SZ", "list_date": "20000101", "delist_date": "20250905"}]),
            "P": _Frame([]),
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "quant.data.adapters._tushare_http_query",
            side_effect=lambda _token, _api, params, **_kwargs: frames[params["list_status"]],
        ) as mocked:
            values, metadata = fetch_tushare_listing_dates(
                ["000001.SZ", "000002.SZ"], token="test-token", cache_dir=Path(directory)
            )
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(values["000001.SZ"], date(1991, 4, 3))
        self.assertEqual(values["000002.SZ"], date(2000, 1, 1))
        self.assertEqual(metadata["delisting_dates"]["000002.SZ"], date(2025, 9, 5))
        self.assertFalse(metadata["stale"])


if __name__ == "__main__":
    unittest.main()
