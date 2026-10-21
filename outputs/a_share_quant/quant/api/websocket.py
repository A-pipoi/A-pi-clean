from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from email.message import Message
from io import BufferedIOBase
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

_JOB_ID = re.compile(r"^BT-[A-Za-z0-9-]{8,80}$")
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _WebSocketHandler(Protocol):
    headers: Message[str, str]
    wfile: BufferedIOBase
    close_connection: bool

    def send_error(self, code: int, message: str | None = None) -> None: ...

    def send_response(self, code: int, message: str | None = None) -> None: ...

    def send_header(self, keyword: str, value: str) -> None: ...

    def end_headers(self) -> None: ...


@runtime_checkable
class _SupportsInt(Protocol):
    def __int__(self) -> int: ...


@runtime_checkable
class _SupportsIndex(Protocol):
    def __index__(self) -> int: ...


def _progress(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, (str, bytes, bytearray, _SupportsInt, _SupportsIndex)):
        return int(value)
    raise TypeError("job progress must be integer-compatible")


def handle_backtest_websocket(
    handler: _WebSocketHandler,
    job_id: str,
    read_job: Callable[[str], dict[str, object] | None],
    *,
    interval_seconds: float = 0.75,
    max_seconds: float = 240.0,
) -> None:
    """Upgrade a same-origin HTTP connection and stream compact job events."""
    if not _JOB_ID.fullmatch(job_id):
        handler.send_error(404)
        return
    host = str(handler.headers.get("Host") or "").strip().lower()
    origin = str(handler.headers.get("Origin") or "").strip()
    origin_host = urlsplit(origin).netloc.lower() if origin else ""
    if not host or not origin_host or origin_host != host:
        handler.send_error(403, "websocket origin rejected")
        return
    key = str(handler.headers.get("Sec-WebSocket-Key") or "").strip()
    try:
        decoded_key = base64.b64decode(key, validate=True)
    except (ValueError, TypeError):
        decoded_key = b""
    if (
        handler.headers.get("Upgrade", "").lower() != "websocket"
        or "upgrade" not in handler.headers.get("Connection", "").lower()
        or handler.headers.get("Sec-WebSocket-Version") != "13"
        or len(decoded_key) != 16
    ):
        handler.send_error(400, "invalid websocket handshake")
        return
    accept = base64.b64encode(
        hashlib.sha1((key + _GUID).encode("ascii"), usedforsecurity=False).digest()
    ).decode("ascii")
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept)
    handler.end_headers()
    handler.close_connection = True
    deadline = time.monotonic() + max_seconds
    previous_signature: tuple[object, ...] | None = None
    try:
        while time.monotonic() < deadline:
            job = read_job(job_id)
            if job is None:
                _write_text_frame(
                    handler,
                    {
                        "job_id": job_id,
                        "status": "not_found",
                        "progress": 0,
                        "message": "回测任务不存在",
                    },
                )
                return
            status = str(job.get("status") or "queued")
            event = {
                "job_id": job_id,
                "status": status,
                "progress": _progress(job.get("progress")),
                "message": str(job.get("message") or "回测任务处理中"),
                "error": job.get("error") if status == "failed" else None,
            }
            signature = (status, event["progress"], event["message"], event["error"])
            if signature != previous_signature or status in {"completed", "failed", "cancelled"}:
                _write_text_frame(handler, event)
                previous_signature = signature
            if status in {"completed", "failed", "cancelled"}:
                return
            time.sleep(interval_seconds)
    except (BrokenPipeError, ConnectionResetError, OSError):
        return


def _write_text_frame(
    handler: _WebSocketHandler, payload: Mapping[str, object]
) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    length = len(encoded)
    if length < 126:
        frame = bytes((0x81, length)) + encoded
    elif length <= 0xFFFF:
        frame = bytes((0x81, 126)) + length.to_bytes(2, "big") + encoded
    else:
        frame = bytes((0x81, 127)) + length.to_bytes(8, "big") + encoded
    handler.wfile.write(frame)
    handler.wfile.flush()
