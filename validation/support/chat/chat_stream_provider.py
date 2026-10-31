"""A bounded local streaming provider with observable requests and release gates."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread


class StreamingProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.started = Event()
        self.release = Event()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        assert self.path == "/v1/chat/completions"
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.requests.append(request)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for delta, finish in (
                ({"role": "assistant", "content": "first delta"}, None),
            ):
                self.wfile.write(self.chunk(delta, finish))
                self.wfile.flush()
            server.started.set()
            if not server.release.wait(30):
                return
            self.wfile.write(self.chunk({"content": " final"}, None))
            self.wfile.write(self.chunk({}, "stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    def chunk(delta, finish):
        return (
            "data: "
            + json.dumps(
                {
                    "id": "chatcmpl-controlled",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "session-ops-model",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
            )
            + "\n\n"
        ).encode()

    def log_message(self, format, *args):
        pass


@contextmanager
def streaming_provider():
    server = StreamingProvider()
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
