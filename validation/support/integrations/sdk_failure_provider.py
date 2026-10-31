"""Local external SDK endpoints; application providers and error handling run real."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

PRIVATE = (
    "sdk-validation-secret",
    "PRIVATE_SDK_INPUT",
    "PRIVATE_SDK_REFRESH",
    "PRIVATE_SDK_BODY",
    "SECRET_BYTES",
    "PRIVATE_SDK_BEARER",
    "user:pass",
    "PRIVATE_SDK_QUERY",
)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.requests.append(self.path)
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = self.server.payload
        self.send_response(403)
        self.send_header(
            "Content-Type",
            "text/plain" if isinstance(body, str) else "application/json",
        )
        body = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextmanager
def sdk_provider(app, provider, response_kind):
    # Only remote HTTP output is controlled. The installed SDK's supported
    # endpoint environment variable points to this server; no factory, app
    # service, exception normalizer or logger is patched.
    app.evidence.register_secret(*PRIVATE)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    structured = {
        "error": {
            "message": 'Safe SDK rejection api_key="sdk-validation-secret" refresh_token=PRIVATE_SDK_REFRESH '
            + "data:application/pdf;base64,SECRET_BYTES Bearer PRIVATE_SDK_BEARER "
            + "https://user:pass@example.org/report?token=PRIVATE_SDK_QUERY\n"
            + "x" * 2000,
            "input": "PRIVATE_SDK_INPUT",
            "code": 403,
            "type": "permission_error",
            "status": "PERMISSION_DENIED",
        }
    }
    server.payload = {
        "structured": structured,
        "text": "PRIVATE_SDK_BODY",
        "list": ["PRIVATE_SDK_BODY"],
        "detail": {"detail": [{"input": "PRIVATE_SDK_BODY"}]},
    }[response_kind]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        app.evidence.save_json(
            "sdk-http-requests.json",
            {"provider": provider, "requests": server.requests},
        )
