"""Loopback device OAuth protocol with pending then authorized polls."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs

DEVICE_ID = "device-validation-private-id"
USER_CODE = "VALIDATION-CODE"
AUTH_CODE = "device-validation-private-auth-code"
VERIFIER = "device-validation-private-verifier"
ACCESS = "device-validation-private-access-token"
REFRESH = "device-validation-private-refresh-token"
API_KEY = "device-validation-private-api-key"


class Server(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.polls = 0


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "account_id": self.headers.get("ChatGPT-Account-ID"),
            }
        )
        body = json.dumps({"object": "list", "data": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        server = self.server
        raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
        if self.path == "/token":
            payload = parse_qs(raw)
            response = {
                "access_token": ACCESS,
                "refresh_token": REFRESH,
                "expires_in": 3600,
                "account_id": "validation-account",
            }
            status = 200
        else:
            payload = json.loads(raw)
            if self.path == "/usercode":
                response = {
                    "device_auth_id": DEVICE_ID,
                    "user_code": USER_CODE,
                    "interval": 1,
                }
                status = 200
            else:
                assert self.path == "/poll"
                server.polls += 1
                status = 403 if server.polls == 1 else 200
                response = (
                    {}
                    if status == 403
                    else {"authorization_code": AUTH_CODE, "code_verifier": VERIFIER}
                )
        server.requests.append({"path": self.path, "payload": payload})
        body = json.dumps(response).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextmanager
def device_provider(app):
    app.evidence.register_secret(
        DEVICE_ID, USER_CODE, AUTH_CODE, VERIFIER, ACCESS, REFRESH
    )
    server = Server()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        app.evidence.save_json("device-oauth-requests.json", server.requests)


async def send_provider_request():
    import httpx

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.llm.model_utils import get_provider_config
    from core.llm.openai_runtime import build_openai_provider_with_resolution

    async with httpx.AsyncClient() as client:
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            result = build_openai_provider_with_resolution(
                provider_config=get_provider_config("openai"), http_client=client
            )
            await result.provider.client.models.list()
            return result.resolution
