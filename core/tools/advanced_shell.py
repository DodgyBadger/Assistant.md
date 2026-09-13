"""Experimental fixed-destination tool for the advanced-shell container."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import Tool

from core.advanced_shell.config import AdvancedShellConfig
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import get_current_execution_task

from .base import (
    ASSISTANTMD_TOOL_METADATA_KEY,
    BaseTool,
    ToolRecoveryPolicy,
    tool_recovery_metadata,
)

logger = UnifiedLogger(tag="advanced-shell-tool")


class ShellTransportError(RuntimeError):
    """Raised when the fixed advanced-shell transport cannot be used safely."""


class ShellOutputLimitExceeded(ShellTransportError):
    """Raised after a command exceeds the configured combined output limit."""


class ShellInputLimitExceeded(ShellTransportError):
    """Raised before execution when stdin exceeds its deployment limit."""


@dataclass
class _OutputCollector:
    """Retain one shared bounded prefix while counting complete stream bytes."""

    limit: int
    total: int = 0
    retained: int = 0
    stdout_chunks: list[bytes] | None = None
    stderr_chunks: list[bytes] | None = None

    def __post_init__(self) -> None:
        self.stdout_chunks = []
        self.stderr_chunks = []

    def add(self, stream_name: str, chunk: bytes) -> None:
        """Account for a chunk and retain only shared remaining capacity."""
        self.total += len(chunk)
        remaining = max(0, self.limit - self.retained)
        retained_chunk = chunk[:remaining]
        if retained_chunk:
            chunks = (
                self.stdout_chunks if stream_name == "stdout" else self.stderr_chunks
            )
            assert chunks is not None
            chunks.append(retained_chunk)
            self.retained += len(retained_chunk)
        if self.total > self.limit:
            raise ShellOutputLimitExceeded("combined shell output limit exceeded")

    def text(self, stream_name: str) -> str:
        """Decode the retained prefix for one stream."""
        chunks = self.stdout_chunks if stream_name == "stdout" else self.stderr_chunks
        assert chunks is not None
        return b"".join(chunks).decode(errors="replace")


@dataclass(frozen=True)
class ShellTransportConfig:
    """Deployment-owned SSH configuration that is never model-controlled."""

    host: str
    private_key_path: Path
    known_hosts_path: Path
    port: int = 2222
    user: str = "advanced-shell"
    host_key_alias: str | None = None
    connect_timeout_seconds: int = 5
    default_timeout_seconds: float = 120.0
    max_timeout_seconds: float = 900.0
    max_output_bytes: int = 2 * 1024 * 1024
    max_input_bytes: int = 1024 * 1024
    max_concurrent_commands: int = 8

    @classmethod
    def from_infrastructure(
        cls,
        config: AdvancedShellConfig,
        system_root: Path,
        *,
        key_root: Path | None = None,
    ) -> ShellTransportConfig:
        """Build product transport settings from validated infrastructure state."""
        state_paths = config.state_paths(system_root)
        private_key_path = (
            key_root / "client_identity"
            if key_root is not None
            else state_paths.client_identity
        )
        known_hosts_path = (
            key_root / "known_hosts"
            if key_root is not None
            else state_paths.known_hosts
        )
        return cls(
            host=config.host,
            port=config.port,
            user=config.user,
            host_key_alias=config.host_key_alias,
            private_key_path=private_key_path,
            known_hosts_path=known_hosts_path,
        )

    @classmethod
    def from_environment(cls) -> ShellTransportConfig:
        """Load experimental deployment coordinates from the environment."""
        key_root = Path(
            os.environ.get("ASSISTANTMD_SHELL_KEY_ROOT", "/run/advanced-shell")
        )
        product_identity = key_root / "client_identity"
        private_key_path = (
            product_identity
            if product_identity.is_file()
            else key_root / "advanced_shell_client"
        )
        return cls(
            host=os.environ.get("ASSISTANTMD_SHELL_HOST", "advanced-shell"),
            port=int(os.environ.get("ASSISTANTMD_SHELL_PORT", "2222")),
            host_key_alias=(
                os.environ.get("ASSISTANTMD_SHELL_HOST_KEY_ALIAS", "").strip() or None
            ),
            private_key_path=private_key_path,
            known_hosts_path=key_root / "known_hosts",
        )


@dataclass(frozen=True)
class ShellExecutionResult:
    """One completed remote command with streams kept distinct."""

    stdout: str
    stderr: str
    exit_code: int | None
    status: str
    output_bytes: int


class ShellExecutor(Protocol):
    """Execution boundary used by the model-facing shell tool."""

    async def execute(
        self,
        command: str,
        *,
        stdin: str = "",
        timeout_seconds: float | None = None,
    ) -> ShellExecutionResult:
        """Execute one command through deployment-owned transport."""
        ...


def build_fixed_ssh_command(config: ShellTransportConfig, command: str) -> list[str]:
    """Build argv for the one deployment-owned SSH destination."""
    arguments = [
        "ssh",
        "-F",
        "/dev/null",
        "-T",
        "-i",
        str(config.private_key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={config.known_hosts_path}",
        "-o",
        f"ConnectTimeout={config.connect_timeout_seconds}",
        "-o",
        "ClearAllForwardings=yes",
    ]
    if config.host_key_alias:
        arguments.extend(("-o", f"HostKeyAlias={config.host_key_alias}"))
    arguments.extend(
        (
            "-p",
            str(config.port),
            f"{config.user}@{config.host}",
            command,
        )
    )
    return arguments


class FixedSshShellExecutor:
    """Execute commands through one pinned SSH identity and destination."""

    def __init__(self, config: ShellTransportConfig):
        self.config = config
        self._concurrency = asyncio.Semaphore(config.max_concurrent_commands)

    async def execute(
        self,
        command: str,
        *,
        stdin: str = "",
        timeout_seconds: float | None = None,
    ) -> ShellExecutionResult:
        """Execute one command under one queue-to-cleanup deadline."""
        normalized_command = command.strip()
        if not normalized_command:
            raise ValueError("command is required")
        self._validate_deployment_files()
        encoded_stdin = stdin.encode()
        if len(encoded_stdin) > self.config.max_input_bytes:
            raise ShellInputLimitExceeded(
                "shell stdin exceeds the deployment limit of "
                f"{self.config.max_input_bytes} bytes"
            )
        timeout = self._bounded_timeout(timeout_seconds)
        deadline = time.monotonic() + timeout
        try:
            await asyncio.wait_for(self._concurrency.acquire(), timeout=timeout)
        except TimeoutError:
            return ShellExecutionResult(
                stdout="",
                stderr="",
                exit_code=None,
                status="timed_out",
                output_bytes=0,
            )
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ShellExecutionResult(
                    stdout="",
                    stderr="",
                    exit_code=None,
                    status="timed_out",
                    output_bytes=0,
                )
            return await self._execute(
                normalized_command,
                stdin=encoded_stdin,
                timeout_seconds=remaining,
            )
        finally:
            self._concurrency.release()

    async def _execute(
        self,
        command: str,
        *,
        stdin: bytes,
        timeout_seconds: float,
    ) -> ShellExecutionResult:
        """Execute after admission through the local concurrency boundary."""
        collector = _OutputCollector(self.config.max_output_bytes)
        process: asyncio.subprocess.Process | None = None
        tasks: list[asyncio.Task[object]] = []
        try:
            async with asyncio.timeout(timeout_seconds):
                process = await asyncio.create_subprocess_exec(
                    *build_fixed_ssh_command(self.config, command),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                assert process.stdin is not None
                assert process.stdout is not None
                assert process.stderr is not None
                tasks = [
                    asyncio.create_task(process.wait(), name="shell-process"),
                    asyncio.create_task(
                        self._read_stream(process.stdout, "stdout", collector),
                        name="shell-stdout",
                    ),
                    asyncio.create_task(
                        self._read_stream(process.stderr, "stderr", collector),
                        name="shell-stderr",
                    ),
                    asyncio.create_task(
                        self._write_stdin(process.stdin, stdin), name="shell-stdin"
                    ),
                ]
                await asyncio.gather(*tasks)
            return ShellExecutionResult(
                stdout=collector.text("stdout"),
                stderr=collector.text("stderr"),
                exit_code=process.returncode,
                status=(
                    "indeterminate_255" if process.returncode == 255 else "completed"
                ),
                output_bytes=collector.total,
            )
        except ShellOutputLimitExceeded:
            if process is not None:
                await self._stop_process(process)
            return ShellExecutionResult(
                stdout=collector.text("stdout"),
                stderr=collector.text("stderr"),
                exit_code=process.returncode if process is not None else None,
                status="output_limit_exceeded",
                output_bytes=collector.total,
            )
        except TimeoutError:
            if process is not None:
                await self._stop_process(process)
            return ShellExecutionResult(
                stdout=collector.text("stdout"),
                stderr=collector.text("stderr"),
                exit_code=process.returncode if process is not None else None,
                status="timed_out",
                output_bytes=collector.total,
            )
        except asyncio.CancelledError:
            if process is not None:
                await self._stop_process(process)
            raise
        finally:
            if process is not None and process.returncode is None:
                try:
                    await self._stop_process(process)
                except OSError as exc:
                    logger.warning(
                        "Advanced shell process cleanup failed",
                        data={
                            "event": "advanced_shell_process_cleanup_failed",
                            "error_type": type(exc).__name__,
                        },
                    )
            if process is not None and process.stdin is not None:
                if not process.stdin.is_closing():
                    process.stdin.close()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _ssh_command(self, command: str) -> list[str]:
        """Compatibility shim for experiments; use the public builder."""
        return build_fixed_ssh_command(self.config, command)

    async def _read_stream(
        self,
        stream: asyncio.StreamReader,
        stream_name: str,
        collector: _OutputCollector,
    ) -> None:
        while chunk := await stream.read(65536):
            collector.add(stream_name, chunk)

    @staticmethod
    async def _write_stdin(writer: asyncio.StreamWriter, content: bytes) -> None:
        """Write bounded stdin without closing the SSH channel lifetime signal."""
        if not content:
            return
        try:
            writer.write(content)
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            return

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    def _validate_deployment_files(self) -> None:
        for label, path in (
            ("private key", self.config.private_key_path),
            ("known-hosts file", self.config.known_hosts_path),
        ):
            if not path.is_file():
                raise ShellTransportError(
                    f"Advanced-shell SSH {label} is unavailable: {path}"
                )

    def _bounded_timeout(self, requested: float | None) -> float:
        timeout = (
            self.config.default_timeout_seconds
            if requested is None
            else float(requested)
        )
        if timeout <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        return min(timeout, self.config.max_timeout_seconds)


class AdvancedShell(BaseTool):
    """Experimental Pydantic AI tool backed by the advanced-shell container."""

    @classmethod
    def get_recovery_policy(cls) -> ToolRecoveryPolicy:
        return ToolRecoveryPolicy.MANUAL_REQUIRED

    @classmethod
    def get_tool(cls, vault_path: str | None = None) -> Tool:
        del vault_path
        executor = FixedSshShellExecutor(ShellTransportConfig.from_environment())

        return cls.for_executor(executor)

    @classmethod
    def for_executor(cls, executor: ShellExecutor) -> Tool:
        """Build the product tool around deployment-owned transport."""

        async def shell(
            *, command: str, stdin: str = "", timeout_seconds: float = 120.0
        ) -> ToolReturn:
            """Run a command in the constrained advanced-shell Linux environment.

            :param command: Shell command to execute in the advanced-shell workspace.
            :param stdin: Optional text made available on standard input.
            :param timeout_seconds: Runtime limit, capped by the deployment maximum.
            """
            started_at = time.monotonic()
            task = get_current_execution_task()
            task_data = {
                "task_id": task.task_id if task is not None else None,
                "session_id": (
                    task.metadata.get("session_id") if task is not None else None
                ),
                "principal_id": task.principal_id if task is not None else None,
            }
            logger.info(
                "Advanced shell command started",
                data={
                    "event": "advanced_shell_command_started",
                    "status": "running",
                    **task_data,
                    "command_chars": len(command),
                    "stdin_bytes": len(stdin.encode()),
                    "timeout_seconds": timeout_seconds,
                },
            )
            try:
                result = await executor.execute(
                    command, stdin=stdin, timeout_seconds=timeout_seconds
                )
            except asyncio.CancelledError:
                logger.warning(
                    "Advanced shell command cancelled",
                    data={
                        "event": "advanced_shell_command_cancelled",
                        "status": "cancelled",
                        **task_data,
                        "duration_seconds": round(time.monotonic() - started_at, 3),
                    },
                )
                raise
            except (ShellTransportError, ValueError, OSError) as exc:
                logger.warning(
                    "Advanced shell command failed",
                    data={
                        "event": "advanced_shell_command_failed",
                        "status": "failed",
                        **task_data,
                        "error_type": type(exc).__name__,
                        "duration_seconds": round(time.monotonic() - started_at, 3),
                    },
                )
                return ToolReturn(
                    return_value=f"shell failed: {exc}",
                    metadata={
                        "tool_name": "shell",
                        "status": "error",
                        "error_type": type(exc).__name__,
                    },
                )
            logger.info(
                "Advanced shell command completed",
                data={
                    "event": "advanced_shell_command_completed",
                    "status": result.status,
                    **task_data,
                    "exit_code": result.exit_code,
                    "output_bytes": result.output_bytes,
                    "duration_seconds": round(time.monotonic() - started_at, 3),
                },
            )
            rendered = _render_result(result)
            return ToolReturn(
                return_value=rendered,
                metadata={
                    "tool_name": "shell",
                    "status": result.status,
                    "exit_code": result.exit_code,
                    "output_bytes": result.output_bytes,
                },
            )

        return Tool(
            shell,
            name="shell",
            description=(
                "Execute a noninteractive shell command in AssistantMD's persistent "
                "advanced-shell container. The destination and SSH transport are fixed."
            ),
            metadata={
                ASSISTANTMD_TOOL_METADATA_KEY: tool_recovery_metadata(
                    cls.get_recovery_policy()
                )
            },
        )


def _render_result(result: ShellExecutionResult) -> str:
    parts = [f"status: {result.status}", f"exit_code: {result.exit_code}"]
    if result.stdout:
        parts.extend(("stdout:", result.stdout))
    if result.stderr:
        parts.extend(("stderr:", result.stderr))
    return "\n".join(parts)
