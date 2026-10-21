from __future__ import annotations

import unittest
from datetime import date, timedelta

from quant.portfolio import (
    PAPER_RISK_LIMITS,
    check_order_limits,
    estimate_portfolio_risk,
)
from quant.strategies import (
    CrossSectionalMomentum,
    DualHorizonMomentum,
    LowVolatilityDefensive,
    build_strategy,
    resolve_strategy_config,
)


class StrategyRegistryAndRiskTests(unittest.TestCase):
    def test_strategy_presets_build_distinct_implementations(self) -> None:
        self.assertIsInstance(build_strategy("baseline", rebalance_every=5), CrossSectionalMomentum)
        self.assertIsInstance(build_strategy("momentum", rebalance_every=5), DualHorizonMomentum)
        self.assertIsInstance(build_strategy("defensive", rebalance_every=5), LowVolatilityDefensive)

    def test_strategy_parameters_are_fixed_to_audited_presets(self) -> None:
        preset, lookback, top_n = resolve_strategy_config("momentum", 12, 5)
        self.assertEqual((preset.key, lookback, top_n), ("momentum", 12, 5))
        with self.assertRaises(ValueError):
            resolve_strategy_config("momentum", 3, 10)

    def test_buy_that_breaks_single_name_and_industry_caps_is_rejected(self) -> None:
        violations = check_order_limits(
            {"000001.SZ": 1_000},
            {"000001.SZ": 10.0, "600000.SH": 10.0},
            {"000001.SZ": "银行", "600000.SH": "银行"},
            100_000.0,
            "600000.SH",
            "BUY",
            4_500,
            PAPER_RISK_LIMITS,
        )
        self.assertIn("single_name", {item.rule for item in violations})
        self.assertIn("industry", {item.rule for item in violations})

    def test_sell_is_allowed_to_reduce_an_existing_exposure(self) -> None:
        violations = check_order_limits(
            {"000001.SZ": 50_000},
            {"000001.SZ": 10.0},
            {"000001.SZ": "银行"},
            100_000.0,
            "000001.SZ",
            "SELL",
            1,
            PAPER_RISK_LIMITS,
        )
        self.assertEqual(violations, [])

    def test_pretrade_risk_uses_rolling_covariance_and_fails_closed_when_missing(self) -> None:
        returns = {"A": {}, "B": {}}
        for index in range(40):
            day = date(2024, 1, 1) + timedelta(days=index)
            shared = 0.02 if index % 2 else -0.02
            returns["A"][day] = shared
            returns["B"][day] = shared * 0.9
        risk = estimate_portfolio_risk({"A": 0.5, "B": 0.5}, returns)
        self.assertEqual(risk["status"], "available")
        self.assertGreater(risk["annualized_volatility"], 0.15)
        self.assertGreater(risk["var_95"], PAPER_RISK_LIMITS.max_var_95)
        self.assertGreater(risk["es_95"], PAPER_RISK_LIMITS.max_es_95)
        self.assertGreater(risk["daily_variance"], PAPER_RISK_LIMITS.max_daily_variance)
        self.assertGreater(risk["max_pairwise_correlation"], PAPER_RISK_LIMITS.max_pairwise_correlation)
        self.assertIn("pre_trade_daily_full_covariance", risk["basis"])
        violations = check_order_limits(
            {},
            {"A": 10.0},
            {"A": "Tech"},
            100_000,
            "A",
            "BUY",
            100,
            PAPER_RISK_LIMITS,
            risk_estimate={"status": "insufficient_data", "observations": 0},
        )
        self.assertIn("risk_data", {item.rule for item in violations})

    def test_volatility_circuit_breaker_blocks_new_buy_but_allows_exit(self) -> None:
        breached = {"status": "available", "annualized_volatility": 0.20, "var_95": 0.02}
        buy = check_order_limits(
            {}, {"A": 10.0}, {"A": "Tech"}, 100_000, "A", "BUY", 100,
            PAPER_RISK_LIMITS, risk_estimate=breached,
        )
        sell = check_order_limits(
            {"A": 1_000}, {"A": 10.0}, {"A": "Tech"}, 100_000, "A", "SELL", 100,
            PAPER_RISK_LIMITS, risk_estimate=breached,
        )
        self.assertIn("volatility_circuit_breaker", {item.rule for item in buy})
        self.assertIn("var_95", {item.rule for item in buy})
        self.assertEqual(sell, [])

    def test_es_covariance_and_pairwise_correlation_limits_block_new_buys(self) -> None:
        estimate = {
            "status": "available",
            "annualized_volatility": 0.10,
            "var_95": 0.01,
            "es_95": 0.03,
            "daily_variance": 0.0002,
            "max_pairwise_correlation": 0.98,
        }
        violations = check_order_limits(
            {"A": 100}, {"A": 10.0, "B": 10.0}, {"A": "Tech", "B": "Finance"},
            100_000, "B", "BUY", 100, PAPER_RISK_LIMITS, risk_estimate=estimate,
        )
        rules = {item.rule for item in violations}
        self.assertIn("expected_shortfall_95", rules)
        self.assertIn("covariance_limit", rules)
        self.assertIn("correlation_limit", rules)


if __name__ == "__main__":
    unittest.main()
