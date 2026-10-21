import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from quant.execution.service import (
    _data_freshness,
    is_a_share_trading_day,
    run_strategy_cycle,
)
from quant.execution.store import PaperStore


class PaperCalendarTests(unittest.TestCase):
    def test_tushare_calendar_open_and_closed(self) -> None:
        def rows_for(is_open: int):
            return SimpleNamespace(
                to_dict=lambda orient: [{"cal_date": "20260924", "is_open": is_open}]
            )

        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "test-token"}),
            patch(
                "quant.execution.service._tushare_http_query", return_value=rows_for(1)
            ) as query,
        ):
            self.assertTrue(is_a_share_trading_day(date(2026, 9, 24)))
            query.assert_called_once_with(
                "test-token",
                "trade_cal",
                {"exchange": "SSE", "start_date": "20260924", "end_date": "20260924"},
                fields="cal_date,is_open",
            )
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "test-token"}),
            patch(
                "quant.execution.service._tushare_http_query", return_value=rows_for(0)
            ),
        ):
            self.assertFalse(is_a_share_trading_day(date(2026, 9, 24)))

    def test_missing_or_failed_calendar_fails_closed(self) -> None:
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "test-token"}),
            patch(
                "quant.execution.service._tushare_http_query",
                return_value=SimpleNamespace(to_dict=lambda orient: []),
            ),
            self.assertRaisesRegex(RuntimeError, "明确开休市状态"),
        ):
            is_a_share_trading_day(date(2026, 9, 24))
        with (
            patch.dict("os.environ", {"TUSHARE_TOKEN": "test-token"}),
            patch(
                "quant.execution.service._tushare_http_query",
                side_effect=RuntimeError("network down"),
            ),
            self.assertRaisesRegex(RuntimeError, "已禁止纸面委托"),
        ):
            is_a_share_trading_day(date(2026, 9, 24))

    def test_direct_cycle_skips_holiday_before_market_data(self) -> None:
        with TemporaryDirectory() as directory:
            store = PaperStore(str(Path(directory) / "paper.sqlite3"))
            with (
                patch(
                    "quant.execution.service._china_today",
                    return_value=date(2026, 9, 25),
                ),
                patch(
                    "quant.execution.service.is_a_share_trading_day", return_value=False
                ),
                patch("quant.execution.service.TushareDailyAdapter") as adapter,
            ):
                result = run_strategy_cycle(store, symbols=["000001.SZ"])
            store.close()

        adapter.assert_not_called()
        self.assertTrue(result["skipped"])
        self.assertFalse(result["paper_trading_allowed"])

    def test_today_live_bars_pass_freshness_gate(self) -> None:
        with patch(
            "quant.execution.service._china_today", return_value=date(2026, 9, 24)
        ):
            freshness = _data_freshness(
                {"data_source": "tushare_live", "latest_trade_date": "2026-09-24"},
                date(2026, 9, 24),
            )
        self.assertTrue(freshness["fresh"])
        self.assertEqual(freshness["max_staleness_days"], 0)

    def test_previous_day_bars_cannot_place_today_orders(self) -> None:
        with TemporaryDirectory() as directory:
            store = PaperStore(str(Path(directory) / "paper.sqlite3"))
            snapshot = SimpleNamespace(
                version="test",
                metadata={
                    "data_source": "tushare_live",
                    "latest_trade_date": "2026-09-23",
                    "limits_available": True,
                },
            )
            adapter = SimpleNamespace(
                daily_bars=lambda **kwargs: [
                    SimpleNamespace(date=date(2026, 9, 22)),
                    SimpleNamespace(date=date(2026, 9, 23)),
                ],
                snapshot=lambda: snapshot,
            )
            with (
                patch(
                    "quant.execution.service._china_today",
                    return_value=date(2026, 9, 24),
                ),
                patch(
                    "quant.execution.service.is_a_share_trading_day", return_value=True
                ),
                patch(
                    "quant.execution.service.TushareDailyAdapter", return_value=adapter
                ),
                patch(
                    "quant.execution.service.resolve_strategy_config",
                    return_value=(
                        SimpleNamespace(key="baseline", label="Baseline"),
                        1,
                        1,
                    ),
                ),
                patch("quant.execution.service.validate_strategy") as validate,
            ):
                result = run_strategy_cycle(store, symbols=["000001.SZ"])
            store.close()

        validate.assert_not_called()
        self.assertTrue(result["skipped"])
        self.assertIn("尚无 2026-09-24", result["skip_reason"])
        self.assertFalse(result["paper_trading_allowed"])


if __name__ == "__main__":
    unittest.main()
