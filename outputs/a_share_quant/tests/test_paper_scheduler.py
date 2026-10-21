import unittest
from datetime import date, datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from unittest.mock import patch

from quant.execution.scheduler import PaperScheduler, _next_scheduled_at
from quant.execution.store import PaperStore


class PaperSchedulerTests(unittest.TestCase):
    def test_weekend_sleep_does_not_request_market_data(self) -> None:
        with TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            scheduler = PaperScheduler(store, symbols=["000001"])
            with (
                patch(
                    "quant.execution.scheduler._china_today",
                    return_value=date(2026, 9, 20),
                ),
                patch("quant.execution.scheduler.run_strategy_cycle") as cycle,
            ):
                result = scheduler.run_once()
            store.close()

            cycle.assert_not_called()
            self.assertTrue(result["skipped"])
            self.assertIn("周末", result["skip_reason"])
            self.assertIsNone(result["cycle_date"])

    def test_before_1600_does_not_request_market_data(self) -> None:
        with TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            scheduler = PaperScheduler(store, symbols=["000001"])
            before_close = datetime(
                2026, 9, 22, 15, 59, tzinfo=timezone(timedelta(hours=8))
            )
            with (
                patch(
                    "quant.execution.scheduler._china_today",
                    return_value=before_close.date(),
                ),
                patch(
                    "quant.execution.scheduler._china_now", return_value=before_close
                ),
                patch("quant.execution.scheduler.run_strategy_cycle") as cycle,
            ):
                result = scheduler.run_once()
            store.close()

            cycle.assert_not_called()
            self.assertTrue(result["skipped"])
            self.assertIn("16:00", result["skip_reason"])
            self.assertEqual(result["data_freshness"]["source"], "before_schedule")

    def test_scheduler_start_within_window_recovers_today(self) -> None:
        with TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            scheduler = PaperScheduler(store, symbols=["000001"])
            after_close = datetime(
                2026, 9, 24, 16, 0, 1, tzinfo=timezone(timedelta(hours=8))
            )
            with patch(
                "quant.execution.scheduler._china_now", return_value=after_close
            ):
                next_run = _next_scheduled_at(scheduler)
            store.close()

            self.assertEqual(next_run, after_close)

    def test_at_1600_runs_once(self) -> None:
        with TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            scheduler = PaperScheduler(store, symbols=["000001"])
            at_close = datetime(2026, 9, 22, 16, 0, tzinfo=timezone(timedelta(hours=8)))
            expected = {
                "cycle_date": "2026-09-22",
                "generated_orders": 0,
                "simulated_orders": [],
            }
            with (
                patch(
                    "quant.execution.scheduler._china_today",
                    return_value=at_close.date(),
                ),
                patch("quant.execution.scheduler._china_now", return_value=at_close),
                patch(
                    "quant.execution.scheduler.is_a_share_trading_day",
                    return_value=True,
                ),
                patch(
                    "quant.execution.scheduler.run_strategy_cycle",
                    return_value=expected,
                ) as cycle,
            ):
                result = scheduler.run_once()
            store.close()

            cycle.assert_called_once()
            self.assertEqual(result, expected)

    def test_pit_universe_is_resolved_after_close_and_passed_to_sealed_gate(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            scheduler = PaperScheduler(
                store,
                symbols=["000001"],
                universe_index="000300.SH",
                universe_provider=lambda day: {
                    "mode": "pit_index_members",
                    "index_code": "000300.SH",
                    "as_of": day.isoformat(),
                    "membership_date": "2026-09-23",
                    "count": 2,
                    "symbols": ["000001.SZ", "600000.SH"],
                },
                sealed_test_provider=lambda symbols, universe: {
                    "passed": symbols == ["000001.SZ", "600000.SH"]
                    and universe.get("index_code") == "000300.SH"
                },
            )
            after_close = datetime(
                2026, 9, 24, 16, 0, tzinfo=timezone(timedelta(hours=8))
            )
            expected = {
                "cycle_date": "2026-09-24",
                "generated_orders": 0,
                "simulated_orders": [],
            }
            with (
                patch(
                    "quant.execution.scheduler._china_today",
                    return_value=after_close.date(),
                ),
                patch("quant.execution.scheduler._china_now", return_value=after_close),
                patch(
                    "quant.execution.scheduler.is_a_share_trading_day",
                    return_value=True,
                ),
                patch(
                    "quant.execution.scheduler.run_strategy_cycle",
                    return_value=expected,
                ) as cycle,
            ):
                scheduler.run_once()
            call = cycle.call_args.kwargs
            self.assertEqual(call["symbols"], ["000001.SZ", "600000.SH"])
            self.assertEqual(call["universe_snapshot"]["index_code"], "000300.SH")
            self.assertTrue(call["sealed_test"]["passed"])
            self.assertEqual(scheduler.status()["symbol_count"], 2)
            store.close()

    def test_status_universe_resolution_does_not_run_a_paper_cycle(self) -> None:
        with TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            calls = []
            scheduler = PaperScheduler(
                store,
                symbols=["000001"],
                universe_index="000300.SH",
                universe_provider=lambda day: (
                    calls.append(day)
                    or {
                        "mode": "pit_index_members",
                        "index_code": "000300.SH",
                        "membership_date": "2026-08-31",
                        "count": 2,
                        "symbols": ["000001.SZ", "600000.SH"],
                    }
                ),
            )
            as_of = date(2026, 9, 29)
            with patch("quant.execution.scheduler.run_strategy_cycle") as cycle:
                resolved = scheduler.resolve_universe_for_status(as_of)
            self.assertEqual(calls, [as_of])
            self.assertEqual(resolved["symbols"], ["000001.SZ", "600000.SH"])
            self.assertEqual(
                scheduler.status()["universe"]["membership_date"], "2026-08-31"
            )
            cycle.assert_not_called()
            store.close()

    def test_holiday_checks_only_calendar(self) -> None:
        with TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            scheduler = PaperScheduler(
                store,
                symbols=["000001"],
                sealed_test_provider=lambda: self.fail("sealed test ran on a holiday"),
            )
            after_close = datetime(
                2026, 9, 25, 16, 0, tzinfo=timezone(timedelta(hours=8))
            )
            with (
                patch(
                    "quant.execution.scheduler._china_today",
                    return_value=after_close.date(),
                ),
                patch("quant.execution.scheduler._china_now", return_value=after_close),
                patch(
                    "quant.execution.scheduler.is_a_share_trading_day",
                    return_value=False,
                ) as calendar,
                patch("quant.execution.scheduler.run_strategy_cycle") as cycle,
            ):
                result = scheduler.run_once()
            store.close()

            calendar.assert_called_once_with(after_close.date())
            cycle.assert_not_called()
            self.assertTrue(result["skipped"])
            self.assertEqual(result["data_freshness"]["source"], "market_closed")

    def test_calendar_failure_does_not_run_cycle(self) -> None:
        with TemporaryDirectory() as directory:
            store = PaperStore(f"{directory}/paper.sqlite3")
            scheduler = PaperScheduler(store, symbols=["000001"])
            after_close = datetime(
                2026, 9, 24, 16, 0, tzinfo=timezone(timedelta(hours=8))
            )
            with (
                patch(
                    "quant.execution.scheduler._china_today",
                    return_value=after_close.date(),
                ),
                patch("quant.execution.scheduler._china_now", return_value=after_close),
                patch(
                    "quant.execution.scheduler.is_a_share_trading_day",
                    side_effect=RuntimeError("calendar unavailable"),
                ),
                patch("quant.execution.scheduler.run_strategy_cycle") as cycle,
                self.assertRaisesRegex(RuntimeError, "calendar unavailable"),
            ):
                scheduler.run_once()
            store.close()

            cycle.assert_not_called()
            self.assertEqual(scheduler.status()["last_error"], "calendar unavailable")


if __name__ == "__main__":
    unittest.main()
