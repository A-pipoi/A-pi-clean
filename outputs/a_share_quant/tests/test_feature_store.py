import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from quant.data.schema import Bar
from quant.features.store import FEATURE_NAMES, FeatureStore


class FeatureStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = FeatureStore(Path(self.temp.name) / "features.sqlite3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _bars(count: int = 75) -> list[Bar]:
        start = date(2024, 1, 1)
        return [
            Bar("600000.SH", start + timedelta(days=index), 10 + index * 0.02,
                10.1 + index * 0.02, 9.9 + index * 0.02, 10 + index * 0.02,
                1000 + index * 5)
            for index in range(count)
        ]

    def test_materialized_features_are_versioned_and_available_only_next_session(self) -> None:
        bars = self._bars()
        metadata = self.store.materialize(bars, "tushare:test-v1")
        self.assertEqual(metadata["schema_version"], "daily-price-features-v2")
        self.assertGreater(metadata["rows"], 0)
        features = self.store.features_for_symbol(
            "600000.SH", before=bars[-1].date, data_version="tushare:test-v1"
        )
        self.assertIn(bars[-2].date, features)
        self.assertEqual(set(features[bars[-2].date]), set(FEATURE_NAMES))
        self.assertNotIn(bars[-1].date, features)
        self.assertEqual(
            self.store.features_for_symbol("600000.SH", before=bars[-1].date, data_version="other-version"),
            {},
        )

    def test_rows_are_immutable_within_a_data_version(self) -> None:
        bars = self._bars()
        self.store.materialize(bars, "v1")
        row_date = bars[60].date
        conflicting = [{
            "symbol": "600000.SH", "feature_date": row_date, "feature_name": FEATURE_NAMES[0],
            "feature_value": 999.0, "available_at": f"{row_date.isoformat()}T17:00:00+08:00",
            "data_version": "v1", "source_hash": "changed",
        }]
        with self.assertRaisesRegex(ValueError, "immutable feature row conflicts"):
            self.store.write_rows(conflicting)

    def test_warmup_history_is_used_but_only_requested_feature_dates_are_written(self) -> None:
        bars = self._bars(75)
        requested_start = bars[65].date
        metadata = self.store.materialize(bars, "warmup-v1", feature_start=requested_start)

        self.assertEqual(metadata["feature_start"], requested_start.isoformat())
        self.assertEqual(metadata["rows"], (len(bars) - 65) * len(FEATURE_NAMES))
        features = self.store.features_for_symbols(
            ["600000.SH"],
            before=bars[-1].date,
            after=requested_start - timedelta(days=1),
            data_version="warmup-v1",
        )
        dates = set(features["600000.SH"])
        self.assertIn(requested_start, dates)
        self.assertNotIn(bars[64].date, dates)

    def test_naive_timestamps_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.store.write_rows([{
                "symbol": "600000.SH", "feature_date": date(2024, 1, 1),
                "feature_name": FEATURE_NAMES[0], "feature_value": 0.1,
                "available_at": "2024-01-01T17:00:00", "data_version": "v1",
            }])


if __name__ == "__main__":
    unittest.main()
