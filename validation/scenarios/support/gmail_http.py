"""Actual loopback HTTP Gmail provider inputs and real encrypted Google grants.

The request hook first validates the intended Google host/path, then routes every
request to the local server. This exercises client/resource semantics and actual
sockets, not Google's TLS endpoint or outbound-network policy.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import deque
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

import httpx

from validation.scenarios.application import Application

ACCESS_TOKEN = "gmail-local-access-token"
REFRESH_TOKEN = "gmail-local-refresh-token"
CLIENT_SECRET = "gmail-local-client-secret"


class GmailHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _GmailRequest)
        self.requests = []
        self.intended_requests = []
        self.responses = deque()
        self.retry_delays = []
        self.attachment_content = b"%PDF-1.7\nexample"

    async def retry_pause(self, seconds):
        self.retry_delays.append(seconds)
        await asyncio.sleep(0)

    def queue(
        self,
        *,
        status=200,
        payload=None,
        body=None,
        headers=None,
        chunked=False,
        delay=0,
    ):
        self.responses.append(
            {
                "status": status,
                "payload": payload,
                "body": body,
                "headers": headers or {},
                "chunked": chunked,
                "delay": delay,
            }
        )

    def response(self, method, path):
        if self.responses:
            return self.responses.popleft()
        if path.endswith("/messages"):
            payload = {
                "messages": [
                    {"id": "message-1", "threadId": "thread-1"},
                    {"id": "message-2", "threadId": "thread-2"},
                ],
                "resultSizeEstimate": 2,
                "nextPageToken": "next-page",
            }
        elif path.endswith("/drafts") and method == "POST":
            payload = {
                "id": "draft-1",
                "message": {"id": "draft-message-1", "threadId": "draft-thread-1"},
            }
        elif "/attachments/" in path:
            payload = {
                "size": len(self.attachment_content),
                "data": base64.urlsafe_b64encode(self.attachment_content)
                .decode()
                .rstrip("="),
            }
        elif path.endswith("/messages/message-1"):
            payload = message_payload("message-1", "thread-1")
        elif path.endswith("/messages/message-2"):
            payload = message_payload("message-2", "thread-2")
        elif path.endswith("/threads/thread-1"):
            payload = {
                "id": "thread-1",
                "historyId": "99",
                "messages": [
                    message_payload("message-1", "thread-1"),
                    message_payload("message-3", "thread-1"),
                ],
            }
        else:
            return {"status": 404, "payload": {"error": {"message": "not found"}}}
        return {"status": 200, "payload": payload}


class _GmailRequest(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        server: GmailHTTPServer = self.server
        content = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        path = self.path.split("?", 1)[0]
        server.requests.append(
            {
                "method": self.command,
                "path": path,
                "query": self.path.partition("?")[2],
                "authorization": self.headers.get("Authorization"),
                "body": content.decode("utf-8"),
            }
        )
        response = server.response(self.command, path)
        time.sleep(response.get("delay", 0))
        body = response.get("body")
        if body is None:
            body = json.dumps(response.get("payload", {})).encode()
        self.send_response(response.get("status", 200))
        headers = response.get("headers", {})
        self.send_header("Content-Type", "application/json")
        if response.get("chunked"):
            self.send_header("Transfer-Encoding", "chunked")
        elif "Content-Length" not in headers:
            self.send_header("Content-Length", str(len(body)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        try:
            if response.get("chunked"):
                self.wfile.write(
                    f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
                )
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        pass


@contextmanager
def gmail_server(app: Application):
    server = GmailHTTPServer()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        for request in server.requests:
            if request["body"]:
                payload = json.loads(request["body"])
                raw = payload.get("message", {}).get("raw")
                if raw:
                    app.evidence.register_secret(raw)
        app.evidence.save_json(
            "gmail-http-requests.json",
            {"intended": server.intended_requests, "received": server.requests},
        )


def http_factory(server: GmailHTTPServer, *, timeout=1):
    async def route(request: httpx.Request):
        assert request.url.scheme == "https"
        assert request.url.host == "gmail.googleapis.com"
        assert request.url.path.startswith("/gmail/v1/users/me/")
        server.intended_requests.append(
            {"method": request.method, "url": str(request.url)}
        )
        request.url = request.url.copy_with(
            scheme="http", host="127.0.0.1", port=server.server_port
        )

    return lambda: httpx.AsyncClient(
        trust_env=False, timeout=timeout, event_hooks={"request": [route]}
    )


def configure_gmail(
    app: Application,
    server: GmailHTTPServer,
    *,
    compose=True,
    download=True,
    draft_limit=20,
    name="Work",
    save_grant=True,
    grant_compose=None,
    is_default=True,
):
    from core.connections import GmailPreferences, GoogleConnectionCreate
    from core.identity import LOCAL_USER_AUTHORITY
    from core.integrations.google import (
        GMAIL_COMPOSE_SCOPE,
        GMAIL_READONLY_SCOPE,
        GOOGLE_IDENTITY_SCOPES,
        GoogleOAuthTokenState,
    )
    from core.integrations.google.gmail import GmailAPIClient
    from core.integrations.google.gmail_service import GmailResourceService
    from core.runtime.state import get_runtime_context

    app.evidence.register_secret(ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET)
    runtime = get_runtime_context()
    authority = LOCAL_USER_AUTHORITY
    connection = runtime.built_in_connections.create_google_connection_for_authority(
        authority,
        GoogleConnectionCreate(
            display_name=name,
            client_id="gmail-validation-client",
            is_default=is_default,
            gmail=GmailPreferences(
                attachment_download_enabled=download,
                attachment_max_mb=7,
                draft_creation_enabled=compose,
                draft_max_characters=draft_limit,
            ),
        ),
    )
    runtime.google_connection.set_client_secret(
        authority, CLIENT_SECRET, connection.connection_id
    )
    if save_grant:
        runtime.google_connection.save_token_state(
            authority,
            GoogleOAuthTokenState(
                access_token=ACCESS_TOKEN,
                refresh_token=REFRESH_TOKEN,
                expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                scopes=(
                    *GOOGLE_IDENTITY_SCOPES,
                    GMAIL_READONLY_SCOPE,
                    *(
                        [GMAIL_COMPOSE_SCOPE]
                        if (compose if grant_compose is None else grant_compose)
                        else []
                    ),
                ),
                account_id="gmail-owner",
                account_email="owner@example.com",
            ),
            connection.connection_id,
        )

    def client_for(caller):
        # The supported factory receives authority only. This fixture resolves
        # its saved default grant; it does not prove per-selector token routing.
        return GmailAPIClient(
            access_token_provider=lambda: runtime.google_oauth.access_token(
                caller, connection.connection_id
            ),
            http_client_factory=http_factory(server),
            sleep=server.retry_pause,
        )

    service = GmailResourceService(
        connections=runtime.built_in_connections,
        google=runtime.google_connection,
        oauth=runtime.google_oauth,
        client_factory=client_for,
    )
    runtime.gmail = service
    client = client_for(authority)
    return authority, connection, service, client


def corrupt_google_grant(app: Application, connection, authority):
    from core.oauth import EncryptedOAuthStorage
    from core.secrets import get_encrypted_secrets_service

    storage = EncryptedOAuthStorage(
        secrets=get_encrypted_secrets_service(),
        authority=authority,
        namespace=f"oauth.google.{connection.connection_id}",
    )
    payload = storage.get_sync("token-state", collection="google")
    assert payload is not None
    payload["scopes"] = 42
    storage.put_sync("token-state", payload, collection="google")


def message_payload(message_id: str, thread_id: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "threadId": thread_id,
        "historyId": "42",
        "internalDate": "1700000000000",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "Hello world preview",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Subject", "value": "Known message"},
                {"name": "From", "value": "Sender <sender@example.com>"},
                {"name": "To", "value": "owner@example.com"},
                {"name": "Date", "value": "Tue, 1 Jan 2026 10:00:00 +0000"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": "SGVsbG8gd29ybGQhIEV4dHJh"},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "body": {"attachmentId": "attachment-1", "size": 1234},
                },
            ],
        },
    }


def draft_message(request):
    from email import policy
    from email.parser import BytesParser

    encoded = json.loads(request["body"])["message"]["raw"]
    return BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    )
