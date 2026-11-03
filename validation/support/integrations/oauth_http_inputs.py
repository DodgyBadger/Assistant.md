"""Socket-backed Google OAuth responses supplied through its public client factory."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from urllib.parse import parse_qs, urlsplit

import httpx

TOKENS = (
    "initial-access-token",
    "initial-refresh-token",
    "refreshed-access-token",
    "client-secret",
    "replacement-secret",
)
REDIRECT = "https://assistant.example/api/system/connections/google/oauth/callback"


class OAuthServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []
        self.block = None
        self.entered = Event()
        self.release = Event()

    def factory(self):
        async def route(request):
            assert (request.url.host, request.url.path) in {
                ("oauth2.googleapis.com", "/token"),
                ("openidconnect.googleapis.com", "/v1/userinfo"),
            }
            request.url = request.url.copy_with(
                scheme="http", host="127.0.0.1", port=self.server_port
            )

        return httpx.AsyncClient(
            trust_env=False, timeout=15, event_hooks={"request": [route]}
        )

    async def wait_entered(self):
        assert await asyncio.to_thread(self.entered.wait, 10)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        server = self.server
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        fields = parse_qs(body)
        step = (
            "identity"
            if self.path == "/v1/userinfo"
            else (
                "refresh" if fields.get("grant_type") == ["refresh_token"] else "token"
            )
        )
        server.requests.append({"path": self.path, "body": fields, "step": step})
        if step == server.block:
            server.entered.set()
            if not server.release.wait(12):
                raise TimeoutError("OAuth provider workload was not released")
        if step == "identity":
            payload = {"sub": "owner-account", "email": "owner@example.com"}
        elif step == "refresh":
            payload = {
                "access_token": "refreshed-access-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        else:
            payload = {
                "access_token": "initial-access-token",
                "refresh_token": "initial-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/gmail.readonly",
            }
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


@contextmanager
def google_oauth_server(app):
    app.evidence.register_secret(*TOKENS)
    server = OAuthServer()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        for request in server.requests:
            for key in ("code", "code_verifier", "client_secret", "refresh_token"):
                app.evidence.register_secret(*request["body"].get(key, []))
        app.evidence.save_json("oauth-http-inputs.json", server.requests)


def google_setup(app, server, *, name="Google"):
    from core.connections import GoogleConnectionCreate
    from core.identity import ExecutionAuthority
    from core.integrations.google import GoogleOAuthCoordinator
    from core.runtime.state import get_runtime_context
    from core.secrets import get_encrypted_secrets_service

    runtime = get_runtime_context()
    owner = ExecutionAuthority("google-oauth-owner")
    connection = runtime.built_in_connections.create_google_connection_for_authority(
        owner,
        GoogleConnectionCreate(
            display_name=name, client_id="client.apps.googleusercontent.com"
        ),
    )
    runtime.google_connection.set_client_secret(
        owner, "client-secret", connection.connection_id
    )
    coordinator = GoogleOAuthCoordinator(
        connections=runtime.built_in_connections,
        google=runtime.google_connection,
        secrets=get_encrypted_secrets_service(),
        http_client_factory=server.factory,
    )
    return (
        owner,
        connection,
        runtime.google_connection,
        runtime.built_in_connections,
        coordinator,
    )


def start_google(app, coordinator, owner, connection):
    from core.integrations.google import GoogleCapability

    started = coordinator.start(
        authority=owner,
        redirect_uri=REDIRECT,
        capabilities=(GoogleCapability.GMAIL_READ,),
        connection_id=connection.connection_id,
    )
    query = parse_qs(urlsplit(started.authorization_url).query)
    app.evidence.register_secret(query["state"][0])
    return started, query
