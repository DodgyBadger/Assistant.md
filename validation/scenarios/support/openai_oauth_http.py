"""Local OAuth provider replies for the real HTTP token adapter."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def oauth_provider() -> Iterator[tuple[str, list[dict[str, list[str]]]]]:
    requests: list[dict[str, list[str]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            form = parse_qs(
                self.rfile.read(int(self.headers["Content-Length"])).decode()
            )
            requests.append(form)
            if form.get("code") == ["SUCCESS_CODE"]:
                status, body = 200, {
                    "access_token": "PRIVATE_SAVED_ACCESS",
                    "refresh_token": "PRIVATE_REFRESH",
                    "expires_in": 1,
                    "account_id": "validation-account",
                }
            elif form.get("grant_type") == ["refresh_token"]:
                status, body = 400, {
                    "error": "invalid_grant",
                    "error_description": "Refresh expired PRIVATE_REFRESH",
                    "access_token": "PRIVATE_RESPONSE_ACCESS",
                }
            else:
                status, body = 400, {
                    "error": "invalid_grant",
                    "error_description": "Authorization expired PRIVATE_CODE",
                    "access_token": "PRIVATE_ACCESS",
                }
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/token", requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
