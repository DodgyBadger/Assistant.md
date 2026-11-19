"""Controlled external DNS latency for the real browser admission entry point."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Event

import pytest
from pydantic_ai.messages import ToolReturn


@dataclass
class HeldBrowserDNS:
    url: str = "https://held-browser.example/"
    entered: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)

    async def call(self) -> str | ToolReturn:
        # Exercise the real application loop: external DNS must not block it.
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
        from core.tools.browser import BrowserTool

        with use_execution_authority(LOCAL_USER_AUTHORITY):
            return await BrowserTool.get_tool().function(url=self.url)


@contextmanager
def held_browser_dns(monkeypatch: pytest.MonkeyPatch) -> Iterator[HeldBrowserDNS]:
    """Replace only one external DNS answer/delay, never application services.

    Admission, configuration, tool errors and URL rejection remain real. The
    eventual private address prevents a browser launch or external request;
    this proves admission under external latency, not Chromium lifecycle.
    """
    held = HeldBrowserDNS()
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host != "held-browser.example":
            return original(host, port, *args, **kwargs)
        held.entered.set()
        if not held.release.wait(15):
            raise TimeoutError("Controlled DNS response was not released")
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", port or 443),
            )
        ]

    with monkeypatch.context() as scope:
        scope.setattr(socket, "getaddrinfo", resolve)
        try:
            yield held
        finally:
            held.release.set()
