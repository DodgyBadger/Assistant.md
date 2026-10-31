"""Controlled external TypeSafe HTTP responses; all application services are real."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.calls.append(
            {
                "body": json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                ),
                "authorization": self.headers.get("authorization"),
            }
        )
        assert self.server.release.wait(
            20
        ), "external decision response barrier expired"
        body = json.dumps(
            {
                "model": "jev-1.13.0",
                "answers": {
                    "needs_review": {"type": "noul", "noul": 0.8},
                    "area": {
                        "type": "choice",
                        "choice": "goal",
                        "confidence": 0.7,
                        "probabilities": {"goal": 0.85, "decision": 0.15},
                    },
                },
                "usage": {"input_tokens": 18, "output_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Expected when a real client deadline closes the held HTTP request.
            pass

    def log_message(self, *args):
        pass


@contextmanager
def decision_provider():
    # Only the remote provider is substituted: live calls require credentials
    # and variable output. SDK, typed adapter, factory, secrets and logs run real.
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.calls = []
    server.release = Event()
    server.release.set()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def configure_decision(app, server):
    key = "decision-runtime-validation-key"
    app.evidence.register_secret(key)
    for name, value in (
        ("DECISION_RUNTIME_KEY", key),
        ("DECISION_RUNTIME_URL", f"http://127.0.0.1:{server.server_port}"),
    ):
        response = app.request(
            "/api/system/secrets", "PUT", {"name": name, "value": value}
        )
        assert response.status_code == 200, response.text
    for path, data in (
        (
            "/api/system/providers/typesafe",
            {"api_key": "DECISION_RUNTIME_KEY", "base_url": "DECISION_RUNTIME_URL"},
        ),
        (
            "/api/system/models/decision-runtime",
            {
                "provider": "typesafe",
                "model_string": "jev-latest",
                "capabilities": ["decision"],
                "alternate_aliases": ["decision-history"],
            },
        ),
    ):
        response = app.request(path, "PUT", data)
        assert response.status_code == 200, response.text
