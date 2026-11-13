"""Real controlled HTTP site and narrow external network environment substitutes."""

from __future__ import annotations

import asyncio
import os
import select
import socket
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from playwright._impl._driver import compute_driver_executable
from pydantic_ai.messages import ToolReturn

from validation.support.application import Application
from validation.support.core_processes import declare_core_command

HOST = "browser-scenario.localhost"
PUBLIC_IP = "93.184.215.14"
COOKIE = "synthetic-browser-context-cookie"


@dataclass
class BrowserSite:
    port: int
    private_requests: list[str] = field(default_factory=list)

    def url(self, path: str) -> str:
        return f"http://{HOST}:{self.port}{path}"


@contextmanager
def retained_browser_site(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> Iterator[BrowserSite]:
    app.evidence.register_secret(COOKIE)
    site = BrowserSite(0)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/denied-target":
                site.private_requests.append(self.path)
            if self.path == "/redirect-private":
                self.send_response(302)
                self.send_header(
                    "Location", f"http://127.0.0.1:{site.port}/denied-target"
                )
                self.end_headers()
                return
            authenticated = f"session={COOKIE}" in self.headers.get("Cookie", "")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            if self.path == "/prime":
                self.send_header(
                    "Set-Cookie", f"session={COOKIE}; HttpOnly; SameSite=Lax; Path=/"
                )
                body = b"<main>Session established</main>"
            else:
                body = (
                    b"<main>Signed in</main>"
                    if authenticated
                    else b"<main>Signed out</main>"
                )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    site.port = server.server_port
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_dns = socket.getaddrinfo
    original_connect = socket.socket.connect

    def dns(host, port, *args, **kwargs):
        if host in (HOST, PUBLIC_IP):
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (PUBLIC_IP, port or 80),
                )
            ]
        return original_dns(host, port, *args, **kwargs)

    def connect(sock, address):
        # Substitute only the declared external site's validated public IP.
        # Policy/DNS classification and proxy pinning stay real; transport goes
        # to the real loopback HTTP server instead of the variable Internet.
        # This proves policy/control flow, not real public routing or TLS.
        if isinstance(address, tuple) and address[:2] == (PUBLIC_IP, site.port):
            address = ("127.0.0.1", site.port)
        return original_connect(sock, address)

    driver, cli = compute_driver_executable()
    declare_core_command([driver, cli, "run-driver"])
    read_fd, write_fd = os.pipe()
    command = [
        "Xvfb",
        "-displayfd",
        str(write_fd),
        "-screen",
        "0",
        "1280x800x24",
        "-nolisten",
        "tcp",
    ]
    display = subprocess.Popen(
        declare_core_command(command),
        pass_fds=(write_fd,),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(write_fd)
    try:
        assert select.select([read_fd], [], [], 5)[0], "Fixture display did not start"
        number = os.read(read_fd, 64).decode().strip()
        assert number.isdecimal()
        # Xvfb is a real environmental dependency supplied by this fixture;
        # application-owned display startup/cleanup has separate acceptance.
        with monkeypatch.context() as scope:
            scope.setenv("DISPLAY", ":" + number)
            scope.setattr(socket, "getaddrinfo", dns)
            scope.setattr(socket.socket, "connect", connect)
            yield site
    finally:
        os.close(read_fd)
        if display.poll() is None:
            display.terminate()
        display.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


async def browser_turn(
    app: Application, vault_name: str, session_id: str, **kwargs
) -> str | ToolReturn:
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.execution_tasks import (
        ExecutionTaskKind,
        ExecutionTaskSource,
        chat_session_scope,
    )
    from core.runtime.state import get_runtime_context
    from core.tools.browser import BrowserTool

    runtime = get_runtime_context()
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        runtime.chat_session_access.ensure_session(session_id, vault_name)
        async with runtime.task_coordinator.track_current_task(
            kind=ExecutionTaskKind.CHAT,
            scope=chat_session_scope(session_id),
            source=ExecutionTaskSource.API,
            label="Browser context scenario",
            authority=LOCAL_USER_AUTHORITY,
        ):
            return await BrowserTool.get_tool().function(interactive=True, **kwargs)


@dataclass
class PausedDriver:
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    construction_deadline: asyncio.Event = field(default_factory=asyncio.Event)
    driver_pid: int | None = None
    browser_pid: int | None = None

    def resume(self) -> None:
        import signal

        if self.driver_pid is not None:
            try:
                os.kill(self.driver_pid, signal.SIGCONT)
            except ProcessLookupError:
                pass


@contextmanager
def paused_browser_driver(app: Application) -> Iterator[PausedDriver]:
    """Pause only the new real driver at its observed ready event.

    This is an external process/IPC failure, not a replacement of application
    services. Always resume it so teardown can reap real browser children.
    """
    import signal
    from pathlib import Path

    from core.logger import capture_validation_events
    from validation.support.browser.browser_runtime import descendant_identities

    baseline = {r["pid"] for r in descendant_identities()}
    paused = PausedDriver()

    def observe(record: dict) -> None:
        app.events.capture(record)
        if record.get("data", {}).get("event") == "browser_context_creation_failed":
            paused.construction_deadline.set()
        if (
            record.get("data", {}).get("event") != "browser_engine_started"
            or paused.driver_pid is not None
        ):
            return
        rows = descendant_identities()
        direct = [
            r["pid"]
            for r in rows
            if r["parent"] == os.getpid() and r["pid"] not in baseline
        ]
        assert len(direct) == 1, direct
        paused.driver_pid = direct[0]
        browsers = [
            r["pid"]
            for r in rows
            if r["parent"] == paused.driver_pid
            and Path(f"/proc/{r['pid']}/comm").read_text().strip() == "chrome"
        ]
        assert len(browsers) == 1, browsers
        paused.browser_pid = browsers[0]
        os.kill(paused.driver_pid, signal.SIGSTOP)
        paused.ready.set()

    with capture_validation_events(
        app.evidence.artifacts / "paused-driver",
        sink=observe,
        projector=app.evidence.redact,
    ):
        try:
            yield paused
        finally:
            paused.resume()
