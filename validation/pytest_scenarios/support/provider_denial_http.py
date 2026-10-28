"""A real provider endpoint rejects typed decision requests."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class DenialHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.calls.append(
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        )
        body = json.dumps({"message": "Access denied", "code": "denied"}).encode()
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextmanager
def denial_provider():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DenialHandler)
    server.calls = []
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
