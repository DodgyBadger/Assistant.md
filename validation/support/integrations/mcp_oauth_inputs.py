"""Explicit durable pending inputs and real local token exchanges for MCP resume."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs


class TokenHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        self.server.requests.append(parse_qs(body))
        payload = {
            "access_token": (
                {"value": "PRIVATE_OAUTH_CREDENTIAL"}
                if self.server.malformed
                else "validation-access-token"
            ),
            "refresh_token": (
                "PRIVATE_REFRESH_CREDENTIAL"
                if self.server.malformed
                else "validation-refresh-token"
            ),
            "token_type": "Bearer",
            "expires_in": 3600,
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
def mcp_token_server(app):
    app.evidence.register_secret(
        "validation-access-token",
        "validation-refresh-token",
        "PRIVATE_OAUTH_CREDENTIAL",
        "PRIVATE_REFRESH_CREDENTIAL",
        "validation-code-verifier",
        "validation-code",
        "validation-state",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), TokenHandler)
    server.daemon_threads = True
    server.requests = []
    server.malformed = False
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        app.evidence.save_json("mcp-oauth-token-requests.json", server.requests)


async def pending_input(
    service, owner, connection, server, *, operation="resume-operation"
):
    # Controlled encrypted input models an already-started authorization after
    # process loss. It does not establish discovery/start generation behavior.
    await service.oauth_storage(owner, connection.connection_id).put(
        "pending-authorization",
        {
            "state": "validation-state",
            "code_verifier": "validation-code-verifier",
            "redirect_uri": "https://assistant.example/api/oauth/callback",
            "token_endpoint": f"http://127.0.0.1:{server.server_port}/token",
            "client_id": "validation-client",
            "client_secret": None,
            "token_endpoint_auth_method": "none",
            "resource": None,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "operation_id": operation,
        },
        collection="assistantmd",
        ttl=300,
    )


@contextmanager
def reject_revocation(app):
    with sqlite3.connect(app.root / "system/access.db") as conn:
        conn.execute(
            "CREATE TRIGGER reject_mcp_revocation BEFORE UPDATE ON mcp_connections BEGIN SELECT RAISE(ABORT, 'controlled revocation constraint'); END"
        )
    try:
        yield
    finally:
        with sqlite3.connect(app.root / "system/access.db") as conn:
            conn.execute("DROP TRIGGER reject_mcp_revocation")


def activity(app):
    return [
        json.loads(line).get("data", {})
        for line in (app.root / "system/activity.log").read_text().splitlines()
        if line.strip()
    ]
