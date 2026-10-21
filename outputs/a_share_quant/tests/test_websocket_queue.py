import unittest
from email.message import Message
from io import BytesIO
from unittest.mock import patch

from quant.api.websocket import handle_backtest_websocket
from quant.backtest.queueing import queue_mode


class _Handler:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = Message()
        for key, value in headers.items():
            self.headers[key] = value
        self.wfile = BytesIO()
        self.response = None
        self.sent_headers = {}
        self.close_connection = False
        self.error = None

    def send_response(self, status, message=None):
        self.response = status

    def send_header(self, key, value):
        self.sent_headers[key] = value

    def end_headers(self):
        pass

    def send_error(self, code, message=None):
        self.error = (code, message)


class WebSocketAndQueueTests(unittest.TestCase):
    def _headers(self, *, origin="http://127.0.0.1:8000"):
        return {
            "Host": "127.0.0.1:8000",
            "Origin": origin,
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        }

    def test_same_origin_handshake_streams_compact_terminal_event(self):
        handler = _Handler(self._headers())
        handle_backtest_websocket(
            handler,
            "BT-20260928-ABCDEF12",
            lambda _job_id: {"status": "completed", "progress": 100, "message": "done"},
        )
        self.assertEqual(handler.response, 101)
        self.assertEqual(
            handler.sent_headers["Sec-WebSocket-Accept"], "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        )
        self.assertIn(b'"status":"completed"', handler.wfile.getvalue())

    def test_cross_origin_handshake_is_rejected(self):
        handler = _Handler(self._headers(origin="http://attacker.invalid"))
        handle_backtest_websocket(handler, "BT-20260928-ABCDEF12", lambda _job_id: None)
        self.assertEqual(handler.error[0], 403)

    def test_job_id_and_upgrade_are_validated(self):
        handler = _Handler(self._headers())
        handle_backtest_websocket(handler, "bad", lambda _job_id: None)
        self.assertEqual(handler.error[0], 404)

    def test_queue_mode_defaults_to_local_and_rejects_unknown_modes(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(queue_mode(), "local")
        with (
            patch.dict("os.environ", {"A_PI_BACKTEST_QUEUE": "typo"}),
            self.assertRaisesRegex(RuntimeError, "must be 'local' or 'redis'"),
        ):
            queue_mode()


if __name__ == "__main__":
    unittest.main()
