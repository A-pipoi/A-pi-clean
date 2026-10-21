from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC
from email.message import Message
from io import BytesIO
from pathlib import Path

from quant.api.paper_routes import _optional_float
from quant.api.portfolio_routes import (
    PortfolioApiDependencies,
    handle_portfolio_optimize,
)
from quant.api.websocket import handle_backtest_websocket
from quant.strategies.baseline import DualHorizonMomentum, LowVolatilityDefensive


class _Handler:
    def __init__(self) -> None:
        self.headers = Message()
        for key, value in {
            "Host": "127.0.0.1:8000",
            "Origin": "http://127.0.0.1:8000",
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        }.items():
            self.headers[key] = value
        self.wfile = BytesIO()
        self.close_connection = False

    def send_error(self, code: int, message: str | None = None) -> None:
        raise AssertionError((code, message))

    def send_response(self, code: int, message: str | None = None) -> None:
        self.response = (code, message)

    def send_header(self, keyword: str, value: str) -> None:
        pass

    def end_headers(self) -> None:
        pass


class TargetedTypingBehaviorTests(unittest.TestCase):
    def test_optional_float_preserves_json_number_inputs(self) -> None:
        self.assertIsNone(_optional_float(None))
        self.assertIsNone(_optional_float(""))
        self.assertEqual(_optional_float("12.5"), 12.5)
        self.assertEqual(_optional_float(12), 12.0)
        with self.assertRaises(TypeError):
            _optional_float({"price": 12.5})

    def test_websocket_accepts_string_progress_from_job_store(self) -> None:
        handler = _Handler()
        handle_backtest_websocket(
            handler,
            "BT-20260928-ABCDEF12",
            lambda _job_id: {"status": "completed", "progress": "100", "message": "done"},
        )
        self.assertIn(b'"progress":100', handler.wfile.getvalue())

    def test_strategy_subclasses_forward_typed_options(self) -> None:
        momentum = DualHorizonMomentum(allocation_mode="inverse_volatility", fundamental_values={})
        defensive = LowVolatilityDefensive(allocation_mode="volatility_targeted", factor_definitions=[])
        self.assertEqual(momentum.allocation_mode, "inverse_volatility")
        self.assertEqual(defensive.allocation_mode, "volatility_targeted")

    def test_non_mapping_pit_source_metadata_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel_path = Path(directory) / "panel.json"
            panel_path.write_text(
                json.dumps({"schema_version": "pit-industry-brinson-v1", "source_metadata": None}),
                encoding="utf-8",
            )
            deps = PortfolioApiDependencies(
                daily_adapter=lambda *args, **kwargs: None,
                cache_dir=Path(directory),
                token_provider=lambda: None,
                pit_panel_path=lambda: str(panel_path),
                symbols=lambda value: list(value),
                shanghai_timezone=UTC,
            )
            with self.assertRaisesRegex(ValueError, "供应方"):
                handle_portfolio_optimize({"symbols": ["000001.SZ"]}, deps)


if __name__ == "__main__":
    unittest.main()
