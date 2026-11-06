"""Loopback-only synthetic website; never accepts real account credentials."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from urllib.parse import parse_qs


@contextmanager
def login_site() -> Iterator[tuple[str, str]]:
    password = secrets.token_urlsafe(24)
    sessions: set[str] = set()
    saved = ""
    release_font = Event()
    font_requested = Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass  # No request, credential, or session logging.

        def session(self) -> str:
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            return cookie["session"].value if "session" in cookie else ""

        def reply(self, status: int, body: str, cookie: str | None = None) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy", "default-src 'self'; form-action 'self'"
            )
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # An abandoned slow asset can outlive its browser page.

        def redirect(self, cookie: str | None = None) -> None:
            self.send_response(303)
            self.send_header("Location", "/workspace")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            if self.path == "/font":
                self.reply(
                    200,
                    '<link rel="stylesheet" href="/font.css"><main>Slow font page</main>',
                )
            elif self.path == "/font.css":
                data = b'@font-face { font-family: delayed; src: url("/font.woff2"); } main { font-family: delayed, sans-serif; }'
                self.send_response(200)
                self.send_header("Content-Type", "text/css")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/font.woff2":
                font_requested.set()
                release_font.wait(timeout=20)
                self.reply(404, "")
            elif self.path == "/font-state":
                self.reply(200, "requested" if font_requested.is_set() else "waiting")
            elif self.path == "/login":
                self.reply(
                    200,
                    '<main><form action="/login" method="post"><label>Password<input name="password" type="password" autocomplete="off"></label><button>Sign in</button></form></main>',
                )
            elif self.path == "/workspace" and self.session() in sessions:
                self.reply(
                    200,
                    '<main><h1>Private workspace</h1><form action="/save" method="post"><label>Project<input name="project"></label><label for="category">Category</label><select id="category" name="category"><option value="research">Research</option></select><button>Save project</button></form><output>'
                    + saved
                    + '</output><form action="/logout" method="post"><button>Sign out</button></form></main>',
                )
            else:
                self.reply(401, "<main>Sign in required</main>")

        def do_POST(self) -> None:
            nonlocal saved
            length = int(self.headers.get("Content-Length", "0"))
            if length > 4096:
                self.reply(413, "<main>Too large</main>")
                return
            fields = parse_qs(self.rfile.read(length).decode())
            if self.path == "/login" and secrets.compare_digest(
                fields.get("password", [""])[0], password
            ):
                token = secrets.token_urlsafe(32)
                sessions.add(token)
                self.redirect(f"session={token}; HttpOnly; SameSite=Strict; Path=/")
            elif self.path == "/save" and self.session() in sessions:
                # Only this known synthetic payload is accepted or reflected.
                if fields == {
                    "project": ["Browser feasibility"],
                    "category": ["research"],
                }:
                    saved = "Saved Browser feasibility (research)"
                    self.redirect()
                else:
                    self.reply(400, "<main>Unexpected project</main>")
            elif self.path == "/logout":
                sessions.discard(self.session())
                self.redirect("session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/")
            else:
                self.reply(401, "<main>Sign in required</main>")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", password
    finally:
        release_font.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
