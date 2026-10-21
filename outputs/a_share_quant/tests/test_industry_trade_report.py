import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from quant import api_server
from quant.data.schema import Bar


def _fill(symbol: str, notional: float = 1_000.0) -> SimpleNamespace:
    return SimpleNamespace(
        order=SimpleNamespace(symbol=symbol),
        date=date(2024, 1, 3),
        quantity=100,
        costs=SimpleNamespace(notional=notional),
    )


def _bar(symbol: str, industry: str | None = None) -> Bar:
    return Bar(
        symbol=symbol,
        date=date(2024, 1, 3),
        open=10,
        high=10,
        low=10,
        close=10,
        volume=10_000,
        industry=industry,
    )


class IndustryTradeReportTests(unittest.TestCase):
    def test_fills_use_stock_basic_industry_when_daily_bars_have_none(self) -> None:
        result = SimpleNamespace(fills=[_fill("600036.SH"), _fill("000001.SZ", 500)])
        metadata = {
            "600036.SH": {"industry": "银行"},
            "000001.SZ": {"industry": "银行"},
        }

        with patch.object(api_server, "get_tushare_stock_metadata", return_value=(metadata, False)) as lookup:
            report = api_server._backtest_operational_reports(
                result,
                [_bar("600036.SH"), _bar("000001.SZ")],
                max_participation=0.10,
            )["industry_trade"]

        lookup.assert_called_once()
        self.assertEqual(report["status"], "available")
        self.assertEqual(report["classified_fills"], 2)
        self.assertEqual(report["unclassified_fills"], 0)
        self.assertEqual(report["rows"][0]["industry"], "银行")
        self.assertEqual(report["rows"][0]["fills"], 2)
        self.assertIn("非点时收益归因", report["basis"])
        self.assertFalse(report["point_in_time"])

    def test_metadata_failure_is_explicit_and_does_not_abort_backtest_report(self) -> None:
        result = SimpleNamespace(fills=[_fill("600036.SH")])

        with patch.object(
            api_server,
            "get_tushare_stock_metadata",
            side_effect=RuntimeError("provider unavailable"),
        ):
            report = api_server._backtest_operational_reports(
                result,
                [_bar("600036.SH")],
                max_participation=0.10,
            )["industry_trade"]

        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["unclassified_fills"], 1)
        self.assertEqual(report["rows"][0]["industry"], "未知行业")
        self.assertTrue(any("provider unavailable" in warning for warning in report["warnings"]))

    def test_whitespace_bar_industry_does_not_mask_stock_basic_fallback(self) -> None:
        result = SimpleNamespace(fills=[_fill("SH.600036")])
        metadata = {"600036.SH": {"industry": "银行"}}

        with patch.object(api_server, "get_tushare_stock_metadata", return_value=(metadata, False)) as lookup:
            report = api_server._backtest_operational_reports(
                result,
                [_bar("SH.600036", "   ")],
                max_participation=0.10,
            )["industry_trade"]

        requested_symbols = set(lookup.call_args.args[0])
        self.assertIn("600036.SH", requested_symbols)
        self.assertEqual(report["status"], "available")
        self.assertEqual(report["rows"][0]["industry"], "银行")
        self.assertEqual(report["classified_fills"], 1)

    def test_current_stock_basic_classification_is_never_marked_pit(self) -> None:
        result = SimpleNamespace(fills=[_fill("600036.SH")])
        with patch.object(api_server, "get_tushare_stock_metadata", return_value=({"600036.SH": {"industry": "银行"}}, False)):
            report = api_server._backtest_operational_reports(
                result,
                [_bar("600036.SH")],
                max_participation=0.10,
            )["industry_trade"]
        self.assertEqual(report["classification_source"], "daily_bar_or_current_stock_basic")
        self.assertIs(report["point_in_time"], False)


if __name__ == "__main__":
    unittest.main()
