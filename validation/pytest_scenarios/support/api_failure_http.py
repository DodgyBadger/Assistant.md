"""Local HTTP failure inputs for real provider retry classification."""

from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class FailureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status = int(self.path.strip("/"))
        self.send_response(status)
        if status == 429:
            self.send_header("Retry-After", "11")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def failure_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FailureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
