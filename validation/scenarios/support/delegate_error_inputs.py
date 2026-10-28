"""Real provider rejection and disconnected-response workloads."""

import json
import socket
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from validation.scenarios.support.code_tool_chat import configure_provider


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.calls.append(json.loads(body))
        if self.server.disconnect:
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        body = json.dumps(
            {
                "error": {
                    "message": "Your credit balance is too low for this request.",
                    "type": "insufficient_quota",
                    "code": "insufficient_quota",
                }
            }
        ).encode()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextmanager
def rejecting_provider(app, *, disconnect=False):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.calls = []
    server.disconnect = disconnect
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        configure_provider(app, server)
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        app.evidence.save_json("delegate-provider-failure-requests.json", server.calls)
