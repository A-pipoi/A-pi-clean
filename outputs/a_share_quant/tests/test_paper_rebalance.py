from __future__ import annotations

import unittest
from datetime import date

from quant.execution.service import _rebalance_schedule


class PaperRebalanceScheduleTests(unittest.TestCase):
    def test_weekly_period_uses_exchange_sessions_after_persisted_anchor(self) -> None:
        anchor = date(2026, 9, 21)
        sessions = [
            date(2026, 9, 22),
            date(2026, 9, 23),
            date(2026, 9, 24),
            # Sep 25-27 were market holidays/weekend.
            date(2026, 9, 28),
        ]
        first = _rebalance_schedule(
            sessions,
            cycle_date=sessions[-1],
            last_rebalance_date=anchor,
            rebalance_every=5,
        )
        self.assertFalse(first["due"])
        self.assertEqual(first["trading_days_since_previous"], 4)

        sessions.append(date(2026, 9, 29))
        next_session = _rebalance_schedule(
            sessions,
            cycle_date=sessions[-1],
            last_rebalance_date=anchor,
            rebalance_every=5,
        )
        self.assertTrue(next_session["due"])
        self.assertEqual(next_session["trading_days_since_previous"], 5)

    def test_rolling_history_window_start_does_not_reset_rebalance_phase(self) -> None:
        anchor = date(2026, 9, 21)
        sessions = [date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)]
        result = _rebalance_schedule(
            sessions,
            cycle_date=sessions[-1],
            last_rebalance_date=anchor,
            rebalance_every=5,
        )
        self.assertFalse(result["due"])
        self.assertEqual(result["trading_days_since_previous"], 3)

    def test_first_cycle_is_due_and_rebalance_interval_must_be_positive(self) -> None:
        first = _rebalance_schedule(
            [date(2026, 9, 28)],
            cycle_date=date(2026, 9, 28),
            last_rebalance_date=None,
            rebalance_every=5,
        )
        self.assertTrue(first["due"])
        self.assertIsNone(first["trading_days_since_previous"])
        with self.assertRaisesRegex(ValueError, "rebalance_every"):
            _rebalance_schedule(
                [],
                cycle_date=date(2026, 9, 28),
                last_rebalance_date=None,
                rebalance_every=0,
            )


if __name__ == "__main__":
    unittest.main()
