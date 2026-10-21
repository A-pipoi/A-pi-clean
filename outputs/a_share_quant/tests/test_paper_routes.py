import unittest
from datetime import timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from quant.api.paper_routes import handle_paper_cycle


class ManualPaperCycleRouteTests(unittest.TestCase):
    def test_manual_cycle_uses_the_same_account_entry_as_automatic_scheduler(self) -> None:
        universe = {
            "mode": "pit_index_members",
            "index_code": "000300.SH",
            "membership_date": "2026-09-28",
            "count": 2,
            "symbols": ["000001.SZ", "600000.SH"],
        }
        captured = {}

        def sealed_gate(*args):
            captured["gate_args"] = args
            return {"passed": False, "reason": "PIT seal not passed"}

        def run_cycle(_store, **kwargs):
            captured["cycle_kwargs"] = kwargs
            return {"skipped": True, "generated_orders": 0}

        scheduler = SimpleNamespace(
            strategy="baseline",
            rebalance="weekly",
            universe_index="000300.SH",
            run_once=lambda: captured.update(shared_entry=True) or {"skipped": True, "generated_orders": 0},
            remember_universe_snapshot=lambda snapshot, symbols: captured.update(
                remembered_universe=(dict(snapshot), list(symbols))
            ),
            status=dict,
        )

        deps = SimpleNamespace(
            store=SimpleNamespace(state=lambda: {"positions": []}, active_order_symbols=list),
            cache_dir=Path("cache"),
            timezone=timezone(timedelta(hours=8)),
            default_universe_index="000300.SH",
            resolve_universe=lambda *_args, **_kwargs: dict(universe),
            token_provider=lambda: "test-token",
            symbols=lambda values: list(values),
            get_strategy_preset=lambda _name: SimpleNamespace(lookback=20, top_n=5),
            bounded_int=lambda value, _minimum, _maximum: int(value),
            resolve_strategy_config=lambda _name, lookback, top_n: (None, lookback, top_n),
            factor_store=SimpleNamespace(definitions=lambda **_kwargs: []),
            sealed_gate=sealed_gate,
            run_strategy_cycle=run_cycle,
            enrich_state=lambda state: state,
            risk_limits_payload=dict,
            scheduler=lambda: scheduler,
        )
        responses = []

        handle_paper_cycle(lambda status, body: responses.append((status, body)), {}, deps)

        self.assertEqual(responses[0][0], 200)
        self.assertTrue(captured["shared_entry"])
        self.assertNotIn("cycle_kwargs", captured)
        self.assertNotIn("gate_args", captured)


if __name__ == "__main__":
    unittest.main()
