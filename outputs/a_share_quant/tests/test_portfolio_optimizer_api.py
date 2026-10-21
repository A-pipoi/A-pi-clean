from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quant.api.portfolio_routes import (
    PortfolioApiDependencies,
    handle_portfolio_optimize,
)


class PortfolioOptimizerApiTests(unittest.TestCase):
    def test_unconfigured_strict_pit_panel_fails_closed_before_data_fetch(self) -> None:
        def unexpected_adapter(*args, **kwargs):
            raise AssertionError("market data must not be fetched without a strict PIT panel")

        deps = PortfolioApiDependencies(
            daily_adapter=unexpected_adapter,
            cache_dir=Path(tempfile.gettempdir()),
            token_provider=lambda: None,
            pit_panel_path=lambda: "",
            symbols=lambda value: list(value),
            shanghai_timezone=__import__("datetime").timezone.utc,
        )
        with self.assertRaisesRegex(RuntimeError, "fail-closed"):
            handle_portfolio_optimize({"symbols": ["000001.SZ"]}, deps)

    def test_optimizer_caps_candidate_count(self) -> None:
        deps = PortfolioApiDependencies(
            daily_adapter=lambda *args, **kwargs: None,
            cache_dir=Path(tempfile.gettempdir()),
            token_provider=lambda: None,
            pit_panel_path=lambda: "",
            symbols=lambda value: list(value),
            shanghai_timezone=__import__("datetime").timezone.utc,
        )
        with self.assertRaisesRegex(ValueError, "最多接受 120"):
            handle_portfolio_optimize({"symbols": [f"{index:06d}.SZ" for index in range(121)]}, deps)


if __name__ == "__main__":
    unittest.main()
