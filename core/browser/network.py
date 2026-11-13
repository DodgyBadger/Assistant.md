"""Bounded proxy-side destination checks for browser TCP connections.

This is not an OS egress sandbox. The managed browser must use this fixed
proxy without a direct fallback; arbitrary trusted container code is outside
this boundary. No application payload or credential is inspected or logged.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from urllib.parse import urlsplit

from core.logger import UnifiedLogger
from core.web.errors import WebUrlPolicyError
from core.web.security import resolve_public_url

logger = UnifiedLogger(tag="browser-network")
Resolved = tuple[str, tuple[str, ...]]


class BrowserNetworkError(RuntimeError):
    """A controlled browser destination or network-resource refusal."""


class BrowserResolver:
    """Keep timed-out DNS jobs admitted until their actual worker completes."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="browser-dns"
        )
        self._jobs: set[Future[Resolved]] = set()
        self._lock = Lock()
        self._closed = False

    async def resolve(self, url: str) -> Resolved:
        with self._lock:
            if self._closed or len(self._jobs) >= 4:
                raise BrowserNetworkError("Browser DNS capacity is unavailable")
            job = self._executor.submit(resolve_public_url, url)
            self._jobs.add(job)
        job.add_done_callback(self._completed)
        pending = asyncio.wrap_future(job)
        # Retrieving a late failure prevents abandoned futures from leaking
        # diagnostics after a caller's timeout. Admission follows the real job.
        pending.add_done_callback(_consume_failure)
        try:
            async with asyncio.timeout(5):
                return await asyncio.shield(pending)
        except TimeoutError as exc:
            raise BrowserNetworkError("Browser DNS deadline exceeded") from exc

    def _completed(self, job: Future[Resolved]) -> None:
        with self._lock:
            self._jobs.discard(job)

    async def close(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        try:
            async with asyncio.timeout(5):
                while True:
                    with self._lock:
                        if not self._jobs:
                            return
                    await asyncio.sleep(0.01)
        except TimeoutError as exc:
            raise BrowserNetworkError(
                "Browser DNS worker cleanup is unconfirmed"
            ) from exc


class BrowserEgress:
    """CONNECT-only SOCKS5 gate; resolve once and dial a validated IP literal."""

    def __init__(self, resolver: BrowserResolver) -> None:
        self.resolver = resolver
        self._server: asyncio.Server | None = None
        self._handlers: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._closed = False

    async def start(self) -> str:
        self._server = await asyncio.start_server(
            self._accept, "127.0.0.1", 0, limit=16384, backlog=32
        )
        return f"socks5://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._closed or len(self._handlers) >= 64:
            writer.close()
            return
        self._writers.add(writer)
        task = asyncio.create_task(self._handle(reader, writer))
        self._handlers.add(task)
        task.add_done_callback(self._handlers.discard)
        task.add_done_callback(_consume_failure)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        upstream: asyncio.StreamWriter | None = None
        established = False
        try:
            async with asyncio.timeout(10):
                version, count = await reader.readexactly(2)
                if version != 5 or not 1 <= count <= 16:
                    raise BrowserNetworkError("invalid_handshake")
                methods = await reader.readexactly(count)
                if 0 not in methods:
                    writer.write(b"\x05\xff")
                    await writer.drain()
                    return
                writer.write(b"\x05\x00")
                await writer.drain()
                version, command, reserved, kind = await reader.readexactly(4)
                if version != 5 or command != 1 or reserved != 0:
                    raise BrowserNetworkError("unsupported_command")
                host = await _read_host(reader, kind)
                port = int.from_bytes(await reader.readexactly(2), "big")
                if not port:
                    raise BrowserNetworkError("invalid_port")
                formatted = f"[{host}]" if ":" in host else host
                url = f"http://{formatted}:{port}/"
                if urlsplit(url).hostname != host.lower():
                    raise BrowserNetworkError("invalid_host")
                _, addresses = await self.resolver.resolve(url)
                # Do not pass the hostname to the connector: a second DNS lookup
                # could produce a private destination after policy approval.
                address = ipaddress.ip_address(addresses[0])
                family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
                incoming, upstream = await asyncio.open_connection(
                    str(address), port, family=family
                )
                self._writers.add(upstream)
                writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
                await writer.drain()
                established = True
            await _relay(reader, writer, incoming, upstream)
        except (
            WebUrlPolicyError,
            BrowserNetworkError,
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            UnicodeError,
            ValueError,
        ) as exc:
            # Never retain hostnames, payload bytes or transport exception text.
            reason = (
                "destination_denied"
                if isinstance(exc, WebUrlPolicyError)
                else "connection_failed"
            )
            logger.set_sinks(["validation"]).info(
                "Browser connection refused",
                data={
                    "event": "browser_connection_refused",
                    "status": "refused",
                    "reason": reason,
                },
            )
            if not established:
                writer.write(b"\x05\x02\x00\x01\x00\x00\x00\x00\x00\x00")
        finally:
            for connection in (upstream, writer):
                if connection is not None:
                    await _close_writer(connection)
                    self._writers.discard(connection)

    async def close(self) -> None:
        self._closed = True
        server, self._server = self._server, None
        if server is not None:
            server.close()
        try:
            for task in self._handlers.copy():
                task.cancel()
            if self._handlers:
                await asyncio.gather(*self._handlers, return_exceptions=True)
        finally:
            try:
                for writer in self._writers.copy():
                    await _close_writer(writer)
                self._writers.clear()
            finally:
                # Python 3.13 waits for accepted clients too: close their
                # handlers/transports before waiting for the server itself.
                if server is not None:
                    async with asyncio.timeout(3):
                        await server.wait_closed()


async def _read_host(reader: asyncio.StreamReader, kind: int) -> str:
    if kind in (1, 4):
        size = 4 if kind == 1 else 16
        return str(ipaddress.ip_address(await reader.readexactly(size)))
    if kind != 3:
        raise BrowserNetworkError("invalid_address_type")
    size = (await reader.readexactly(1))[0]
    if not 1 <= size <= 253:
        raise BrowserNetworkError("invalid_domain_length")
    host = (await reader.readexactly(size)).decode("ascii").lower()
    if not all(char.isascii() and (char.isalnum() or char in ".-_") for char in host):
        raise BrowserNetworkError("invalid_domain")
    return host


async def _relay(
    client: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    remote: asyncio.StreamReader,
    remote_writer: asyncio.StreamWriter,
) -> None:
    last_activity = asyncio.get_running_loop().time()

    async def pump(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
        nonlocal last_activity
        while True:
            chunk = await source.read(16384)
            if not chunk:
                if target.can_write_eof():
                    target.write_eof()
                    await target.drain()
                return
            last_activity = asyncio.get_running_loop().time()
            target.write(chunk)
            await target.drain()

    async def idle_deadline() -> None:
        while True:
            remaining = 60 - (asyncio.get_running_loop().time() - last_activity)
            if remaining <= 0:
                raise TimeoutError("Browser tunnel is idle")
            await asyncio.sleep(remaining)

    pumps = {
        asyncio.create_task(pump(client, remote_writer)),
        asyncio.create_task(pump(remote, client_writer)),
    }
    monitor = asyncio.create_task(idle_deadline())
    tasks = pumps | {monitor}
    try:
        while pumps:
            done, _ = await asyncio.wait(
                pumps | {monitor}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
                pumps.discard(task)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        async with asyncio.timeout(1):
            await writer.wait_closed()
    except (OSError, TimeoutError):
        writer.transport.abort()


def _consume_failure[T](future: asyncio.Future[T]) -> None:
    if not future.cancelled():
        future.exception()
