"""Image fixture inputs and captured OpenAI-compatible image parts."""

import base64
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from validation.scenarios.support.code_tool_chat import configure_provider


def seed_delegate_image(app, vault):
    image = Path("validation/fixtures/files/test_image.jpg").read_bytes()
    (vault / "images").mkdir()
    (vault / "images/test_image.jpg").write_bytes(image)
    app.write(
        vault,
        "notes/0.md",
        "Review this embedded image.\n\n![Example](../images/test_image.jpg)\n",
    )
    return image


def image_payloads(value):
    if isinstance(value, dict):
        if value.get("type") == "image_url":
            uri = value["image_url"]["url"]
            prefix, encoded = uri.split(",", 1)
            assert prefix == "data:image/jpeg;base64"
            yield base64.b64decode(encoded, validate=True)
        for child in value.values():
            yield from image_payloads(child)
    elif isinstance(value, list):
        for child in value:
            yield from image_payloads(child)


class ImageProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), ImageHandler)
        self.calls = []


class ImageHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(request)
        if len(server.calls) == 1:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "image-read",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps(
                                {"operation": "read", "path": "notes/0.md"}
                            ),
                        },
                    }
                ],
            }
        else:
            delta = {"role": "assistant", "content": "Child completed."}
        chunks = []
        for item, finish in (
            (delta, None),
            ({}, "tool_calls" if "tool_calls" in delta else "stop"),
        ):
            payload = {
                "id": "image-child",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "code-tool-model",
                "choices": [{"index": 0, "delta": item, "finish_reason": finish}],
            }
            chunks.append("data: " + json.dumps(payload) + "\n\n")
        body = ("".join(chunks) + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextmanager
def image_provider(app):
    server = ImageProvider()
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
        app.evidence.save_json("delegate-image-provider-requests.json", server.calls)
