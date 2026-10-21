"""Explicit local/public origin boundary and bounded HTTP transport."""

from __future__ import annotations

import os
import socket as socket_module
import ssl
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from socket import socket
from typing import Any
from urllib.parse import urlsplit


def allowed_request(
    host: str,
    origin: str | None,
    peer: str,
    forwarded_proto: str | None,
    *,
    direct_tls_connection: bool = False,
) -> tuple[bool, bool]:
    local = urlsplit("http://" + host)
    if local.username or local.password or not local.hostname:
        return False, False
    public = os.getenv("A_PI_PUBLIC_ORIGIN", "").rstrip("/")
    public_url = urlsplit(public)
    trusted = {
        value.strip()
        for value in os.getenv("A_PI_TRUSTED_PROXIES", "").split(",")
        if value.strip()
    }
    if (
        public
        and public_url.scheme == "https"
        and local.netloc.lower() == public_url.netloc.lower()
    ):
        secure = peer in trusted and forwarded_proto == "https"
        return secure and (not origin or origin.rstrip("/") == public), True
    if local.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return False, False
    expected_origin = ("https://" if direct_tls_connection else "http://") + host
    return (not origin or origin.rstrip("/") == expected_origin), direct_tls_connection


def is_direct_tls(connection: object) -> bool:
    """Return true only when this process accepted an actual TLS socket."""
    return isinstance(connection, ssl.SSLSocket)


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def server_bind(self) -> None:
        # Windows: ThreadingHTTPServer sets SO_REUSEADDR, which silently allows
        # a second listener to share port 8000 and steal TLS handshakes with a
        # stale certificate. SO_EXCLUSIVEADDRUSE makes the second bind fail
        # loudly instead, so launcher races converge to a single instance.
        # It only works when SO_REUSEADDR is NOT set on the same socket.
        if sys.platform == "win32" and hasattr(socket_module, "SO_EXCLUSIVEADDRUSE"):
            self.allow_reuse_address = False
            self.socket.setsockopt(
                socket_module.SOL_SOCKET, socket_module.SO_EXCLUSIVEADDRUSE, 1
            )
        super().server_bind()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.slots = threading.BoundedSemaphore(64)
        self.tls_context: ssl.SSLContext | None = None
        super().__init__(*args, **kwargs)

    def enable_tls(self, certificate: Path, private_key: Path) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate, private_key)
        self.tls_context = context

    def get_request(self) -> tuple[socket, Any]:
        connection, address = super().get_request()
        connection.settimeout(15)
        if self.tls_context is not None:
            # Defer handshakes to bounded request threads so idle TLS clients
            # cannot block the listening loop and all health requests.
            connection = self.tls_context.wrap_socket(
                connection, server_side=True, do_handshake_on_connect=False
            )
        return connection, address

    def process_request(
        self, request: socket | tuple[bytes, socket], client_address: Any
    ) -> None:
        if not self.slots.acquire(blocking=False):
            try:
                if isinstance(request, socket) and not is_direct_tls(request):
                    request.sendall(
                        b"HTTP/1.1 503 Service Unavailable\r\n"
                        b"Connection: close\r\nContent-Length: 0\r\n"
                        b"Retry-After: 5\r\n\r\n"
                    )
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(
        self, request: socket | tuple[bytes, socket], client_address: Any
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()
