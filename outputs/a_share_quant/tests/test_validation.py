import json
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

from quant.data.pit import FeatureAvailabilityChecker, FutureInformationError
from quant.data.schema import Bar, FeatureObservation
from quant.strategies import CrossSectionalMomentum
from quant.validation import PurgedWalkForward, validate_strategy


class ValidationTests(unittest.TestCase):
    def test_purged_walk_forward_has_embargo(self) -> None:
        folds = PurgedWalkForward(5, 3, 2).split(list(range(20)))
        self.assertEqual(folds[0].train_indices, (0, 1, 2, 3, 4))
        self.assertEqual(folds[0].embargo_indices, (5, 6))
        self.assertEqual(folds[0].test_indices, (7, 8, 9))
        PurgedWalkForward(5, 3, 2).validate_no_overlap(folds)

    def test_future_feature_is_rejected(self) -> None:
        observation = FeatureObservation(
            symbol="000001.SZ",
            as_of=__import__("datetime").date(2024, 1, 2),
            available_at=datetime(2024, 1, 3, tzinfo=UTC),
            name="t_plus_one_close",
            value=1.0,
            source_version="fixture",
        )
        with self.assertRaises(FutureInformationError):
            FeatureAvailabilityChecker().check(observation)

    def test_strategy_validation_runs_real_oos_folds(self) -> None:
        cache_dir = Path(__file__).resolve().parents[1] / "data_cache"
        bars = []
        for filename in (
            "tushare_000001_SZ_2024-01-02_2026-09-18_raw.json",
            "tushare_000006_SZ_2024-01-02_2026-09-18_raw.json",
            "tushare_300001_SZ_2024-01-02_2026-09-18_raw.json",
            "tushare_600000_SH_2024-01-02_2026-09-18_raw.json",
        ):
            path = cache_dir / filename
            if not path.is_file():
                self.skipTest(f"real Tushare cache snapshot is not available: {path}")
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = sorted(payload.get("bars", []), key=lambda row: row["date"])[-90:]
            bars.extend(
                Bar(
                    symbol=row["symbol"],
                    date=date.fromisoformat(row["date"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    limit_up=row.get("limit_up"),
                    limit_down=row.get("limit_down"),
                    paused=bool(row.get("paused", False)),
                    listed=bool(row.get("listed", True)),
                    is_st=bool(row.get("is_st", False)),
                    industry=row.get("industry"),
                    is_delisted=bool(row.get("is_delisted", False)),
                )
                for row in rows
            )
        report = validate_strategy(
            bars,
            lambda: CrossSectionalMomentum(lookback=20, top_n=2, rebalance_every=5),
            minimum_oos_observations=45,
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.folds, 3)
        self.assertEqual(report.oos_observations, 45)
        self.assertTrue(all(check.status == "通过" for check in report.checks))
