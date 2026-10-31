"""External DNS/socket fault inputs; MCP's admission and connection logic stay real."""

import asyncio
import socket
from threading import Event

import httpcore


class DNSAnswers:
    def __init__(self, monkeypatch):
        self.answers = {}
        self.requests = []
        self.hold = False
        self.entered = Event()
        self.release = Event()
        self.exited = Event()
        original = socket.getaddrinfo

        def resolve(host, port, *args, **kwargs):
            if host not in self.answers:
                return original(host, port, *args, **kwargs)
            self.requests.append(host)
            if self.hold:
                self.entered.set()
                try:
                    if not self.release.wait(5):
                        raise TimeoutError("Controlled external DNS was not released")
                finally:
                    self.exited.set()
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (ip, port or 0),
                )
                for ip in self.answers[host]
            ]

        # DNS is an external dependency. Controlled answers make rebinding and
        # mixed-address rejection deterministic without an external DNS server.
        # No in-app resolver or policy is replaced; socket.getaddrinfo is the
        # OS boundary. This does not prove behavior of a live DNS deployment.
        monkeypatch.setattr(socket, "getaddrinfo", resolve)


class NetworkFailures(httpcore.AsyncNetworkBackend):
    def __init__(self, *, delay=0):
        self.calls = []
        self.delay = delay
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.hold = False

    async def connect_tcp(self, host, port, timeout=None, **kwargs):
        self.calls.append({"host": host, "port": port, "timeout": timeout})
        self.entered.set()
        # Only the external connection attempt is simulated. Real MCP policy
        # chooses numeric targets and computes remaining budgets. Real TLS and
        # socket success are proved separately against the local HTTPS server.
        if self.hold:
            await self.release.wait()
        if self.delay:
            await asyncio.sleep(min(self.delay, timeout or self.delay))
        raise httpcore.ConnectTimeout("Controlled external connection timeout")
