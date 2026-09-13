"""Principal-scoped retained MCP connections and settled tool catalogs."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.client import MessageHandler
from fastmcp.client.transports import (
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)
from mcp.types import Tool, ToolListChangedNotification

from core.advanced_shell.authority import require_advanced_shell_authority
from core.advanced_shell.preflight import (
    AdvancedShellPreflightSnapshot,
    AdvancedShellReadiness,
)
from core.advanced_shell.stdio import encode_structured_launch
from core.identity import ExecutionAuthority
from core.logger import UnifiedLogger
from core.tools.advanced_shell import ShellTransportConfig, build_fixed_ssh_command
from core.web.security import sanitize_url_for_log

from .models import (
    MCPAuthMode,
    MCPConnection,
    MCPConnectionTestResult,
    MCPTransport,
)
from .network import (
    MCPAsyncHTTPTransport,
    MCPNetworkPolicyError,
    validate_mcp_endpoint,
)
from .oauth_storage import (
    ConnectedMCPOAuth,
    EncryptedMCPOAuthStorage,
    has_mcp_oauth_tokens,
)
from .service import MCPConnectionService

MCP_CONNECT_TIMEOUT_SECONDS = 10.0
MCP_INIT_TIMEOUT_SECONDS = 8.0
MCP_READ_TIMEOUT_SECONDS = 30.0
MCP_CLOSE_TIMEOUT_SECONDS = 5.0
MCP_INVALIDATION_TIMEOUT_SECONDS = 5.0
MCP_IDLE_TIMEOUT_SECONDS = 15 * 60.0
MCP_MAX_TOOL_PAGES = 10
MCP_TEST_MAX_RETURNED_TOOL_NAMES = 100
MCP_CONNECT_ATTEMPTS = 2
DEFAULT_MCP_MAX_CONCURRENT_STDIO_LAUNCHES = 4

logger = UnifiedLogger(tag="mcp-manager")

_ConnectionKey = tuple[str, str, int]


class MCPAdvancedShellUnavailableError(RuntimeError):
    """Raised when authenticated advanced-shell readiness is unavailable."""


@dataclass(frozen=True)
class MCPUnavailableConnection:
    """Sanitized connection failure safe for user/model-facing summaries."""

    connection_id: str
    display_name: str
    status: str
    message: str


@dataclass(frozen=True)
class MCPReadinessSnapshot:
    """Settled ready and unavailable connections for one execution boundary."""

    leases: tuple[MCPConnectionLease, ...]
    unavailable: tuple[MCPUnavailableConnection, ...]

    async def close(self) -> None:
        """Release all ready connection leases."""
        await asyncio.gather(*(lease.close() for lease in self.leases))

    async def __aenter__(self) -> MCPReadinessSnapshot:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()


class MCPConnectionLease:
    """One active-use claim on a frozen managed client/catalog pair."""

    def __init__(
        self,
        *,
        connection: MCPConnection,
        client: Client,
        tools: tuple[Tool, ...],
        release: Callable[[], Awaitable[None]],
    ) -> None:
        self.connection = connection
        self.client = client
        self.tools = tools
        self._release = release
        self._closed = False

    async def close(self) -> None:
        """Release this lease exactly once."""
        if self._closed:
            return
        self._closed = True
        await self._release()

    async def __aenter__(self) -> MCPConnectionLease:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()


@dataclass
class _ManagedConnection:
    connection: MCPConnection
    client: Client
    tools: tuple[Tool, ...]
    active_leases: int
    last_used: float
    invalidated: bool = False


class MCPConnectionManager:
    """Own retained FastMCP clients without crossing principal boundaries."""

    def __init__(
        self,
        *,
        connections: MCPConnectionService,
        idle_timeout_seconds: float = MCP_IDLE_TIMEOUT_SECONDS,
        advanced_shell_stdio: ShellTransportConfig | None = None,
        advanced_shell_readiness: (
            Callable[[], Awaitable[AdvancedShellPreflightSnapshot]] | None
        ) = None,
        max_concurrent_stdio_launches: int = DEFAULT_MCP_MAX_CONCURRENT_STDIO_LAUNCHES,
    ) -> None:
        self._connections = connections
        self._idle_timeout_seconds = idle_timeout_seconds
        self._advanced_shell_stdio = advanced_shell_stdio
        self._advanced_shell_readiness = advanced_shell_readiness
        if max_concurrent_stdio_launches < 1:
            raise ValueError("MCP stdio launch concurrency must be at least one.")
        self._max_concurrent_stdio_launches = max_concurrent_stdio_launches
        self._stdio_launch_semaphore = asyncio.Semaphore(max_concurrent_stdio_launches)
        self._entries: dict[_ConnectionKey, _ManagedConnection] = {}
        self._locks: dict[_ConnectionKey, asyncio.Lock] = {}
        self._invalidation_epochs: dict[tuple[str, str], int] = {}
        self._state_lock = asyncio.Lock()
        self._closed = False
        self._loop = asyncio.get_running_loop()
        self._idle_task: asyncio.Task[None] | None = None
        self._invalidation_tasks: set[asyncio.Task[None]] = set()
        self._connection_tasks: set[asyncio.Task[_ManagedConnection]] = set()

    def start(self) -> None:
        """Start bounded idle-client eviction without connecting any server."""
        if self._idle_task is None:
            self._idle_task = asyncio.create_task(self._idle_eviction_loop())

    async def acquire_snapshot(
        self,
        authority: ExecutionAuthority,
    ) -> MCPReadinessSnapshot:
        """Settle every enabled connection as ready or sanitized unavailable."""
        connections = [
            connection
            for connection in self._connections.list_connections_for_authority(
                authority
            )
            if connection.enabled
        ]
        tasks = [
            asyncio.create_task(self.acquire(authority, connection))
            for connection in connections
        ]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            settled = await asyncio.gather(*tasks, return_exceptions=True)
            acquired = tuple(
                result for result in settled if isinstance(result, MCPConnectionLease)
            )
            await asyncio.gather(*(lease.close() for lease in acquired))
            raise
        leases: list[MCPConnectionLease] = []
        unavailable: list[MCPUnavailableConnection] = []
        for connection, result in zip(connections, results, strict=True):
            if isinstance(result, MCPConnectionLease):
                leases.append(result)
                continue
            unavailable.append(_unavailable(connection, result))
        return MCPReadinessSnapshot(tuple(leases), tuple(unavailable))

    async def test_connection(
        self,
        authority: ExecutionAuthority,
        connection: MCPConnection,
    ) -> MCPConnectionTestResult:
        """Warm one managed connection and return its sanitized effective catalog."""
        try:
            lease = await self._acquire(
                authority,
                connection,
                require_enabled=False,
            )
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            unavailable = _unavailable(connection, exc)
            return MCPConnectionTestResult(
                status=unavailable.status,
                ready=False,
                tool_count=None,
                tool_names=(),
                message=unavailable.message,
            )
        try:
            names = tuple(tool.name for tool in lease.tools)
            if connection.allowed_tools is not None:
                allowed = set(connection.allowed_tools)
                names = tuple(name for name in names if name in allowed)
            returned_names = names[:MCP_TEST_MAX_RETURNED_TOOL_NAMES]
            message = (
                "Connected successfully and discovered "
                f"{len(names)} available MCP tool(s)."
            )
            if len(names) > len(returned_names):
                message += f" Showing the first {len(returned_names)} names."
            return MCPConnectionTestResult(
                status="ready",
                ready=True,
                tool_count=len(names),
                tool_names=returned_names,
                message=message,
            )
        finally:
            await lease.close()

    async def acquire(
        self,
        authority: ExecutionAuthority,
        connection: MCPConnection,
    ) -> MCPConnectionLease:
        """Acquire one versioned connection, sharing concurrent cold starts."""
        return await self._acquire(authority, connection, require_enabled=True)

    async def _acquire(
        self,
        authority: ExecutionAuthority,
        connection: MCPConnection,
        *,
        require_enabled: bool,
    ) -> MCPConnectionLease:
        if self._closed:
            raise RuntimeError("MCP connection manager is closed.")
        authoritative = self._connections.get_connection_for_authority(
            authority, connection.connection_id
        )
        if authoritative is None or authoritative != connection:
            raise RuntimeError("MCP connection configuration is not current.")
        connection = authoritative
        if require_enabled and not connection.enabled:
            raise RuntimeError("MCP connection is disabled.")
        key = _key(authority, connection)
        async with self._state_lock:
            lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            async with self._state_lock:
                target = (authority.principal_id, connection.connection_id)
                invalidation_epoch = self._invalidation_epochs.get(target, 0)
                entry = self._entries.get(key)
                if entry is not None and not entry.invalidated:
                    if self._is_authorized(
                        authority,
                        connection,
                        require_enabled=require_enabled,
                    ):
                        entry.active_leases += 1
                        entry.last_used = time.monotonic()
                        return self._lease(key, entry)
                    entry.invalidated = True
                    if entry.active_leases == 0:
                        self._entries.pop(key, None)
                        stale_client = entry.client
                    else:
                        stale_client = None
                else:
                    stale_client = None

            if stale_client is not None:
                await _close_client(stale_client, connection=connection)

            async with self._state_lock:
                if self._closed:
                    raise RuntimeError("MCP connection manager is closed.")
                connection_task = asyncio.create_task(
                    self._connect_with_retry(authority, connection)
                )
                self._connection_tasks.add(connection_task)
            try:
                entry = await connection_task
            finally:
                async with self._state_lock:
                    self._connection_tasks.discard(connection_task)
            reject_entry = False
            manager_closed = False
            existing_entry: _ManagedConnection | None = None
            async with self._state_lock:
                if self._closed:
                    manager_closed = True
                else:
                    if self._invalidation_epochs.get(
                        target, 0
                    ) != invalidation_epoch or not self._is_authorized(
                        authority,
                        connection,
                        require_enabled=require_enabled,
                    ):
                        reject_entry = True
                    if not reject_entry:
                        existing = self._entries.get(key)
                        if existing is not None and not existing.invalidated:
                            existing.active_leases += 1
                            existing.last_used = time.monotonic()
                            existing_entry = existing
                        else:
                            entry.active_leases = 1
                            self._entries[key] = entry
            if manager_closed:
                await _close_client(entry.client, connection=entry.connection)
                raise RuntimeError("MCP connection manager is closed.")
            if existing_entry is not None:
                await _close_client(entry.client, connection=entry.connection)
                return self._lease(key, existing_entry)
            if not reject_entry:
                return self._lease(key, entry)
            await _close_client(entry.client, connection=entry.connection)
            raise RuntimeError("MCP connection configuration changed while connecting.")

    def _is_authorized(
        self,
        authority: ExecutionAuthority,
        connection: MCPConnection,
        *,
        require_enabled: bool,
    ) -> bool:
        """Confirm a lease candidate still matches active authoritative metadata."""
        current = self._connections.get_connection_for_authority(
            authority,
            connection.connection_id,
        )
        return (
            current is not None
            and current.config_version == connection.config_version
            and (current.enabled or not require_enabled)
        )

    def invalidate(self, principal_id: str, connection_id: str) -> None:
        """Acknowledge stale marking; existing leases finish and cleanup is observed."""
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            self._spawn_invalidation(principal_id, connection_id)
            return
        acknowledged: Future[None] = Future()

        def mark() -> None:
            try:
                self._spawn_invalidation(principal_id, connection_id)
            except Exception as exc:
                acknowledged.set_exception(exc)
            else:
                acknowledged.set_result(None)

        self._loop.call_soon_threadsafe(mark)
        acknowledged.result(timeout=MCP_INVALIDATION_TIMEOUT_SECONDS)

    async def evict_idle(self) -> int:
        """Close retained clients that have no active lease and exceeded idle TTL."""
        cutoff = time.monotonic() - self._idle_timeout_seconds
        async with self._state_lock:
            keys = [
                key
                for key, entry in self._entries.items()
                if entry.active_leases == 0 and entry.last_used <= cutoff
            ]
            entries = [self._entries.pop(key) for key in keys]
        await asyncio.gather(
            *(
                _close_client(entry.client, connection=entry.connection)
                for entry in entries
            )
        )
        return len(entries)

    async def shutdown(self) -> None:
        """Stop accepting leases and close every retained client."""
        async with self._state_lock:
            if self._closed:
                return
            self._closed = True
        idle_task = self._idle_task
        self._idle_task = None
        if idle_task is not None:
            idle_task.cancel()
            await asyncio.gather(idle_task, return_exceptions=True)
        async with self._state_lock:
            connection_tasks = tuple(self._connection_tasks)
        for task in connection_tasks:
            task.cancel()
        if connection_tasks:
            await asyncio.gather(*connection_tasks, return_exceptions=True)
        invalidation_tasks = tuple(self._invalidation_tasks)
        if invalidation_tasks:
            await asyncio.gather(*invalidation_tasks, return_exceptions=True)
        async with self._state_lock:
            entries = list(self._entries.values())
            self._entries.clear()
            self._locks.clear()
            self._invalidation_epochs.clear()
        await asyncio.gather(
            *(
                _close_client(entry.client, connection=entry.connection)
                for entry in entries
            )
        )

    def _spawn_invalidation(self, principal_id: str, connection_id: str) -> None:
        if self._closed:
            raise RuntimeError("MCP connection manager is closed.")
        # All state mutations run on this event loop without an intervening await.
        # The async state-lock sections likewise never suspend while changing entries.
        target = (principal_id, connection_id)
        self._invalidation_epochs[target] = self._invalidation_epochs.get(target, 0) + 1
        to_close: list[_ManagedConnection] = []
        for key, entry in list(self._entries.items()):
            if key[:2] != target:
                continue
            entry.invalidated = True
            if entry.active_leases == 0:
                self._entries.pop(key, None)
                to_close.append(entry)
        if not to_close:
            return
        task = asyncio.create_task(self._close_invalidated(to_close))
        self._invalidation_tasks.add(task)
        task.add_done_callback(self._invalidation_finished)

    def _invalidation_finished(self, task: asyncio.Task[None]) -> None:
        self._invalidation_tasks.discard(task)
        if task.cancelled():
            error_type = "CancelledError"
        else:
            error = task.exception()
            if error is None:
                return
            error_type = type(error).__name__
        logger.warning(
            "MCP invalidated client cleanup failed",
            data={"event": "mcp_invalidation_cleanup_failed", "error_type": error_type},
        )

    async def _idle_eviction_loop(self) -> None:
        interval = max(1.0, min(60.0, self._idle_timeout_seconds))
        while True:
            await asyncio.sleep(interval)
            await self.evict_idle()

    async def _connect(
        self,
        authority: ExecutionAuthority,
        connection: MCPConnection,
    ) -> _ManagedConnection:
        if connection.transport is MCPTransport.ADVANCED_SHELL_STDIO:
            return await self._connect_advanced_shell_stdio(authority, connection)
        url = _require_http_url(connection)
        credential = self._connections.resolve_credential(
            authority,
            connection.connection_id,
            expected_connection=connection,
        )
        oauth_storage = (
            self._connections.oauth_storage(
                authority, connection.connection_id, expected_connection=connection
            )
            if connection.auth_mode is MCPAuthMode.OAUTH
            else None
        )
        await validate_mcp_endpoint(
            url,
            allow_private_http=connection.allow_private_http,
        )
        if oauth_storage is not None and not await has_mcp_oauth_tokens(
            storage=oauth_storage,
            mcp_url=url,
        ):
            raise ValueError(
                "MCP OAuth authorization is required. Connect this server in System."
            )
        headers, auth = _build_auth(
            connection,
            credential,
            oauth_storage=oauth_storage,
            allow_private_http=connection.allow_private_http,
        )
        transport = (
            StreamableHttpTransport(
                url,
                headers=headers,
                auth=auth,
                httpx_client_factory=_mcp_http_client_factory(
                    allow_private_http=connection.allow_private_http
                ),
            )
            if connection.transport is MCPTransport.STREAMABLE_HTTP
            else SSETransport(
                url,
                headers=headers,
                auth=auth,
                httpx_client_factory=_mcp_http_client_factory(
                    allow_private_http=connection.allow_private_http
                ),
            )
        )
        client = Client(
            transport,
            message_handler=_CatalogChangeHandler(
                lambda: self.invalidate(
                    authority.principal_id,
                    connection.connection_id,
                )
            ),
            init_timeout=MCP_INIT_TIMEOUT_SECONDS,
            timeout=MCP_READ_TIMEOUT_SECONDS,
        )
        try:
            async with asyncio.timeout(MCP_CONNECT_TIMEOUT_SECONDS):
                await client.__aenter__()
                tools = await client.list_tools(max_pages=MCP_MAX_TOOL_PAGES)
        except BaseException:
            await _close_client(client, connection=connection)
            raise
        logger.info(
            "MCP connection ready",
            data={
                "event": "mcp_connection_ready",
                "principal_id": authority.principal_id,
                "connection_id": connection.connection_id,
                "url": sanitize_url_for_log(url),
                "transport": connection.transport.value,
                "tool_count": len(tools),
            },
        )
        return _ManagedConnection(
            connection=connection,
            client=client,
            tools=tuple(tools),
            active_leases=0,
            last_used=time.monotonic(),
        )

    async def _connect_advanced_shell_stdio(
        self,
        authority: ExecutionAuthority,
        connection: MCPConnection,
    ) -> _ManagedConnection:
        config = self._advanced_shell_stdio
        launch = connection.stdio
        require_advanced_shell_authority(authority)
        if self._advanced_shell_readiness is not None:
            readiness = await self._advanced_shell_readiness()
            if readiness.state is not AdvancedShellReadiness.READY:
                raise MCPAdvancedShellUnavailableError(readiness.message)
        if config is None or launch is None:
            raise ValueError("Advanced-shell stdio requires advanced execution mode.")
        for path in (config.private_key_path, config.known_hosts_path):
            if not path.is_file():
                raise ValueError("Advanced-shell SSH identity is unavailable.")
        command = encode_structured_launch(
            executable=launch.executable,
            arguments=launch.arguments,
            working_directory=launch.working_directory,
            environment=launch.environment,
        )
        ssh_argv = build_fixed_ssh_command(config, command)
        transport = StdioTransport(
            command=ssh_argv[0],
            args=ssh_argv[1:],
            keep_alive=True,
            log_file=Path(os.devnull),
        )
        client = Client(
            transport,
            roots=[Path(root).as_uri() for root in launch.roots],
            message_handler=_CatalogChangeHandler(
                lambda: self.invalidate(
                    authority.principal_id,
                    connection.connection_id,
                )
            ),
            init_timeout=MCP_INIT_TIMEOUT_SECONDS,
            timeout=MCP_READ_TIMEOUT_SECONDS,
        )
        launch_permit_acquired = False
        try:
            async with asyncio.timeout(MCP_CONNECT_TIMEOUT_SECONDS):
                await self._stdio_launch_semaphore.acquire()
                launch_permit_acquired = True
                await client.__aenter__()
                tools = await client.list_tools(max_pages=MCP_MAX_TOOL_PAGES)
        except TimeoutError:
            if not launch_permit_acquired:
                logger.warning(
                    "MCP stdio launch capacity unavailable",
                    data={
                        "event": "mcp_stdio_capacity_rejected",
                        "principal_id": authority.principal_id,
                        "connection_id": connection.connection_id,
                        "limit": self._max_concurrent_stdio_launches,
                        "reason": "launch_queue_timeout",
                    },
                )
            await _close_client(client, connection=connection)
            raise
        except BaseException:
            await _close_client(client, connection=connection)
            raise
        finally:
            if launch_permit_acquired:
                self._stdio_launch_semaphore.release()
        logger.info(
            "MCP connection ready",
            data={
                "event": "mcp_connection_ready",
                "principal_id": authority.principal_id,
                "connection_id": connection.connection_id,
                "transport": connection.transport.value,
                "tool_count": len(tools),
            },
        )
        return _ManagedConnection(
            connection=connection,
            client=client,
            tools=tuple(tools),
            active_leases=0,
            last_used=time.monotonic(),
        )

    async def _connect_with_retry(
        self,
        authority: ExecutionAuthority,
        connection: MCPConnection,
    ) -> _ManagedConnection:
        for attempt in range(1, MCP_CONNECT_ATTEMPTS + 1):
            try:
                return await self._connect(authority, connection)
            except BaseException as exc:
                if attempt == MCP_CONNECT_ATTEMPTS or not _is_transient(exc):
                    raise
                logger.info(
                    "Retrying transient MCP connection failure",
                    data={
                        "event": "mcp_connection_retrying",
                        "connection_id": connection.connection_id,
                        "attempt": attempt + 1,
                        "error_type": type(exc).__name__,
                    },
                )
                await asyncio.sleep(0)
        raise RuntimeError("MCP connection retry loop exhausted unexpectedly.")

    def _lease(
        self,
        key: _ConnectionKey,
        entry: _ManagedConnection,
    ) -> MCPConnectionLease:
        return MCPConnectionLease(
            connection=entry.connection,
            client=entry.client,
            tools=entry.tools,
            release=lambda: self._release(key, entry),
        )

    async def _release(
        self,
        key: _ConnectionKey,
        entry: _ManagedConnection,
    ) -> None:
        close_entry = False
        async with self._state_lock:
            entry.active_leases = max(0, entry.active_leases - 1)
            entry.last_used = time.monotonic()
            if entry.invalidated and entry.active_leases == 0:
                close_entry = True
                if self._entries.get(key) is entry:
                    self._entries.pop(key, None)
        if close_entry:
            await _close_client(entry.client, connection=entry.connection)

    async def _close_invalidated(self, to_close: list[_ManagedConnection]) -> None:
        await asyncio.gather(
            *(
                _close_client(entry.client, connection=entry.connection)
                for entry in to_close
            )
        )


def _key(
    authority: ExecutionAuthority,
    connection: MCPConnection,
) -> _ConnectionKey:
    return (
        authority.principal_id,
        connection.connection_id,
        connection.config_version,
    )


def _build_auth(
    connection: MCPConnection,
    credential: str | None,
    *,
    oauth_storage: EncryptedMCPOAuthStorage | None = None,
    allow_private_http: bool = False,
) -> tuple[dict[str, str] | None, str | httpx.Auth | None]:
    if connection.auth_mode is MCPAuthMode.BEARER:
        if credential is None:
            raise ValueError("MCP bearer credential is missing.")
        return None, credential
    if connection.auth_mode is MCPAuthMode.HEADER:
        if credential is None or connection.header_name is None:
            raise ValueError("MCP header credential is missing.")
        return {connection.header_name: credential}, None
    if connection.auth_mode is MCPAuthMode.OAUTH:
        if oauth_storage is None:
            raise ValueError("MCP OAuth storage is unavailable.")
        return None, ConnectedMCPOAuth(
            mcp_url=_require_http_url(connection),
            token_storage=oauth_storage,
            allow_private_http=allow_private_http,
        )
    return None, None


def _require_http_url(connection: MCPConnection) -> str:
    if connection.url is None:
        raise ValueError("HTTP MCP connection URL is missing.")
    return connection.url


def _mcp_http_client_factory(
    *, allow_private_http: bool
) -> Callable[..., httpx.AsyncClient]:
    """Create clients that enforce network policy immediately before each request."""

    async def validate_request(request: httpx.Request) -> None:
        await validate_mcp_endpoint(
            str(request.url),
            allow_private_http=allow_private_http,
        )

    def create_client(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **_kwargs: object,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout or httpx.Timeout(30.0, read=MCP_READ_TIMEOUT_SECONDS),
            auth=auth,
            follow_redirects=False,
            trust_env=False,
            event_hooks={"request": [validate_request]},
            transport=MCPAsyncHTTPTransport(),
        )

    return create_client


class _CatalogChangeHandler(MessageHandler):
    def __init__(self, invalidate: Callable[[], None]) -> None:
        self._invalidate = invalidate

    async def on_tool_list_changed(
        self,
        message: ToolListChangedNotification,
    ) -> None:
        del message
        self._invalidate()


async def _close_client(
    client: Client,
    *,
    connection: MCPConnection | None = None,
) -> None:
    try:
        async with asyncio.timeout(MCP_CLOSE_TIMEOUT_SECONDS):
            await client.__aexit__(None, None, None)
    except (Exception, asyncio.CancelledError) as exc:
        context: dict[str, object] = {
            "event": "mcp_client_cleanup_failed",
            "error_type": type(exc).__name__,
        }
        if connection is not None:
            context.update(
                {
                    "connection_id": connection.connection_id,
                    "transport": connection.transport.value,
                }
            )
        logger.warning(
            "MCP client cleanup did not complete cleanly",
            data=context,
        )


def _unavailable(
    connection: MCPConnection,
    error: BaseException,
) -> MCPUnavailableConnection:
    status = "connection_failed"
    message = "The MCP server did not become ready."
    if isinstance(error, TimeoutError | httpx.TimeoutException):
        status = "timeout"
        message = "The MCP server did not become ready before the timeout."
    elif isinstance(error, MCPNetworkPolicyError):
        status = "network_policy_rejected"
        message = str(error)
    elif isinstance(error, MCPAdvancedShellUnavailableError):
        status = "advanced_shell_unavailable"
        message = str(error)
    elif isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {
        401,
        403,
    }:
        status = "authentication_failed"
        message = "The MCP server rejected authentication."
    elif isinstance(error, httpx.RequestError):
        status = "unreachable"
        message = "The MCP server could not be reached."
    logger.warning(
        "MCP connection unavailable",
        data={
            "event": "mcp_connection_unavailable",
            "connection_id": connection.connection_id,
            "url": (
                sanitize_url_for_log(connection.url)
                if connection.url is not None
                else None
            ),
            "transport": connection.transport.value,
            "status": status,
            "error_type": type(error).__name__,
        },
    )
    return MCPUnavailableConnection(
        connection_id=connection.connection_id,
        display_name=connection.display_name,
        status=status,
        message=message,
    )


def _is_transient(error: BaseException) -> bool:
    return isinstance(error, TimeoutError | httpx.TimeoutException | httpx.RequestError)
