import sys
import tempfile
import types
import unittest
from datetime import date
from unittest.mock import patch

from quant.data import AkshareDailyAdapter
from quant.data import adapters as adapters_module


class _FakeFrame:
    empty = False

    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient="records"):
        self.assert_orient = orient
        return self.rows


class AkshareAdapterTests(unittest.TestCase):
    def test_converts_official_columns_and_volume_lots_to_shares(self) -> None:
        calls = []

        def stock_zh_a_hist(**kwargs):
            calls.append(kwargs)
            return _FakeFrame(
                [
                    {
                        "日期": "2024-01-03",
                        "开盘": 10,
                        "最高": 11,
                        "最低": 9.5,
                        "收盘": 10.5,
                        "成交量": 120,
                    },
                    {
                        "日期": "2024-01-02",
                        "开盘": 9,
                        "最高": 10,
                        "最低": 8.5,
                        "收盘": 9.5,
                        "成交量": 100,
                    },
                ]
            )

        fake_akshare = types.SimpleNamespace(stock_zh_a_hist=stock_zh_a_hist)
        with patch.dict(sys.modules, {"akshare": fake_akshare}):
            bars = AkshareDailyAdapter(["600000.SH"], adjust="hfq").daily_bars(
                start=date(2024, 1, 2), end=date(2024, 1, 3)
            )

        self.assertEqual(
            [bar.date for bar in bars], [date(2024, 1, 2), date(2024, 1, 3)]
        )
        self.assertEqual(bars[0].symbol, "600000.SH")
        self.assertEqual(bars[0].volume, 10_000)
        self.assertEqual(calls[0]["symbol"], "600000")
        self.assertEqual(calls[0]["adjust"], "hfq")

    def test_rejects_invalid_symbols(self) -> None:
        with self.assertRaises(ValueError):
            AkshareDailyAdapter(["not-a-code"])

    def test_persists_cache_and_reuses_it_without_network_call(self) -> None:
        calls = []

        def stock_zh_a_hist(**kwargs):
            calls.append(kwargs)
            return _FakeFrame(
                [
                    {
                        "日期": "2024-02-01",
                        "开盘": 10,
                        "最高": 11,
                        "最低": 9.5,
                        "收盘": 10.5,
                        "成交量": 120,
                    }
                ]
            )

        fake_akshare = types.SimpleNamespace(stock_zh_a_hist=stock_zh_a_hist)
        with (
            tempfile.TemporaryDirectory() as cache_dir,
            patch.dict(sys.modules, {"akshare": fake_akshare}),
            patch.object(adapters_module, "_AKSHARE_BAR_CACHE", {}),
        ):
            first = AkshareDailyAdapter(["000001"], cache_dir=cache_dir).daily_bars(
                start=date(2024, 2, 1), end=date(2024, 2, 1)
            )
            self.assertEqual(len(first), 1)
            self.assertEqual(len(calls), 1)

            def should_not_call(**kwargs):
                raise AssertionError(
                    "network should not be called for a fresh disk cache"
                )

            second_fake = types.SimpleNamespace(stock_zh_a_hist=should_not_call)
            with (
                patch.dict(sys.modules, {"akshare": second_fake}),
                patch.object(adapters_module, "_AKSHARE_BAR_CACHE", {}),
            ):
                second = AkshareDailyAdapter(
                    ["000001"], cache_dir=cache_dir
                ).daily_bars(start=date(2024, 2, 1), end=date(2024, 2, 1))
            self.assertEqual(second, first)


if __name__ == "__main__":
    unittest.main()
