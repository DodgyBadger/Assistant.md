"""Controlled external DNS/curl responses; all AssistantMD services remain real."""

from __future__ import annotations

import socket
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class RemoteWeb:
    responses: dict[str, tuple[int, dict[str, str], bytes]] = field(
        default_factory=dict
    )
    addresses: dict[str, tuple[str, ...]] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)

    def serve(self, url, body, *, status=200, headers=None):
        self.responses[url] = (status, headers or {"content-type": "text/html"}, body)
        self.addresses[urlsplit(url).hostname] = ("93.184.215.14",)


@contextmanager
def external_web(monkeypatch, app):
    remote = RemoteWeb()
    real_dns = socket.getaddrinfo
    real_run = subprocess.run

    def dns(host, port, *args, **kwargs):
        if host in remote.addresses:
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (ip, port or 0),
                )
                for ip in remote.addresses[host]
            ]
        return real_dns(host, port, *args, **kwargs)

    def curl(command, *args, **kwargs):
        if Path(command[0]).name != "curl":
            return real_run(command, *args, **kwargs)
        url = command[-1]
        assert url in remote.responses, f"Undeclared external HTTP response: {url}"
        # This double replaces only the external curl process/DNS environment:
        # live public services require egress and can vary or need credentials.
        # Real URL admission, redirect decisions, fetching orchestration, document
        # classification, extraction, rendering, writes and provenance are intact.
        # It does NOT prove curl's network implementation or public deployment;
        # the independent real-loopback curl scenario covers actual transfer.
        assert command[command.index("--noproxy") + 1] == "*"
        hostname = urlsplit(url).hostname
        pinned = command[command.index("--resolve") + 1]
        assert pinned.endswith(":" + remote.addresses[hostname][0])
        status, headers, body = remote.responses[url]
        remote.requests.append(url)
        Path(command[command.index("--dump-header") + 1]).write_text(
            f"HTTP/1.1 {status} Remote\r\n"
            + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            + "\r\n"
        )
        Path(command[command.index("--output") + 1]).write_bytes(body)
        return subprocess.CompletedProcess(
            command,
            0,
            f"__CURL_META__{status}|{url}|{remote.addresses[hostname][0]}|0.01",
            "",
        )

    with monkeypatch.context() as scope:
        scope.setattr(socket, "getaddrinfo", dns)
        scope.setattr(subprocess, "run", curl)
        try:
            yield remote
        finally:
            app.evidence.save_json(
                "external-web.json",
                {
                    "substitutions": [
                        "external DNS answers",
                        "external curl HTTP responses",
                    ],
                    "requests": remote.requests,
                    "addresses": remote.addresses,
                },
            )
