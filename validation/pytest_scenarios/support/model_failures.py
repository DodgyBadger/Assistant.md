"""Controlled real provider transport and task event collection."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from validation.pytest_scenarios.application import Application

PRIVATE_VALUES = (
    "PRIVATE_PROVIDER_INPUT",
    "validation-provider-key",
    "PRIVATE_REFRESH",
    "PRIVATE_BEARER",
    "PRIVATE_DOCUMENT",
    "user:password",
    "PRIVATE_QUERY",
)


class _RejectingProvider(BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.requests.append((self.path, self.headers.get("Authorization", "")))
        body = json.dumps(
            {
                "error": {
                    "type": "invalid_request_error",
                    "code": "billing_error",
                    "message": (
                        'Your credit balance is too low. api_key="validation-provider-key" '
                        "refresh_token=PRIVATE_REFRESH Bearer PRIVATE_BEARER "
                        "data:application/pdf;base64,PRIVATE_DOCUMENT "
                        "https://user:password@example.test/path?token=PRIVATE_QUERY\n"
                        + "x" * 2000
                    ),
                    "input": "PRIVATE_PROVIDER_INPUT",
                }
            }
        ).encode()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def rejecting_provider():
    _RejectingProvider.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RejectingProvider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _RejectingProvider.requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def require_activity(app: Application, name: str, *, fields: dict[str, object]) -> None:
    """Match actual application activity values without evidence projection."""
    response = app.request("/api/system/activity-log", params={"limit": 1000})
    assert response.status_code == 200, response.text
    candidates = [
        entry["data"]
        for entry in response.json()["entries"]
        if entry["data"].get("event") == name
    ]
    assert any(
        all(row.get(key) == value for key, value in fields.items())
        for row in candidates
    ), (name, fields, candidates)
