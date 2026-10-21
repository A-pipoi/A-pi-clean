import unittest
from datetime import UTC, date, datetime

from quant.data import (
    DEFAULT_SYMBOLS,
    FeatureAvailabilityChecker,
    FeatureObservation,
    HistoricalUniverse,
)


class DataInterfaceTests(unittest.TestCase):
    def test_default_universe_is_fixed_and_diversified(self) -> None:
        self.assertEqual(len(DEFAULT_SYMBOLS), 24)
        self.assertEqual(len(set(DEFAULT_SYMBOLS)), len(DEFAULT_SYMBOLS))
        self.assertTrue(any(symbol.startswith("3") for symbol in DEFAULT_SYMBOLS))
        self.assertTrue(any(symbol.startswith("6") for symbol in DEFAULT_SYMBOLS))

    def test_historical_universe_does_not_use_current_constituents_for_past(self) -> None:
        universe = HistoricalUniverse(
            {
                date(2024, 1, 1): {"A", "B"},
                date(2024, 2, 1): {"B", "C"},
            }
        )
        self.assertEqual(universe.symbols(date(2024, 1, 20)), frozenset({"A", "B"}))
        self.assertFalse(universe.contains("C", date(2024, 1, 20)))

    def test_feature_available_on_cutoff_date_is_allowed(self) -> None:
        observation = FeatureObservation(
            "A",
            date(2024, 1, 2),
            datetime(2024, 1, 2, 6, tzinfo=UTC),
            "reported_feature",
            1.0,
            "fixture",
        )
        FeatureAvailabilityChecker().check(observation)

    def test_feature_published_after_a_share_close_is_rejected_same_day(self) -> None:
        observation = FeatureObservation(
            "A",
            date(2024, 1, 2),
            datetime(2024, 1, 2, 7, 1, tzinfo=UTC),
            "reported_feature",
            1.0,
            "fixture",
        )
        with self.assertRaises(ValueError):
            FeatureAvailabilityChecker().check(observation)
