"""Stress the actual experimental shell tool against the persistent advanced shell."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pydantic_ai.messages import ToolReturn

from core.tools.advanced_shell import AdvancedShell

LOG_PATH = Path("scripts/advanced_shell_tool_probe.latest.log")
SHELL_TOOL = AdvancedShell.get_tool()


async def _call(command: str, **kwargs: Any) -> ToolReturn:
    result = await SHELL_TOOL.function(command=command, **kwargs)
    assert isinstance(result, ToolReturn)
    return result


def _assert_status(result: ToolReturn, status: str) -> None:
    assert result.metadata is not None
    assert result.metadata["status"] == status, result


async def run_probe() -> dict[str, Any]:
    """Exercise behavior and return a compact machine-readable report."""
    checks: list[str] = []

    result = await _call("printf stdout-value; printf stderr-value >&2; exit 7")
    _assert_status(result, "completed")
    assert result.metadata["exit_code"] == 7
    assert "stdout-value" in str(result.return_value)
    assert "stderr-value" in str(result.return_value)
    checks.append("streams_and_exit")

    result = await _call(
        "IFS= read -r value; printf 'input=%s' \"$value\"", stdin="hello\n"
    )
    _assert_status(result, "completed")
    assert "input=hello" in str(result.return_value)
    checks.append("stdin")

    result = await _call(
        "touch /workspace/oversized-stdin-must-not-run",
        stdin="x" * (1024 * 1024 + 1),
    )
    _assert_status(result, "error")
    assert result.metadata["error_type"] == "ShellInputLimitExceeded"
    result = await _call("test ! -e /workspace/oversized-stdin-must-not-run")
    _assert_status(result, "completed")
    checks.append("stdin_limit_before_execution")

    marker = f"persistent-{time.time_ns()}"
    await _call(f"printf %s {marker!r} > /workspace/persistence-probe")
    result = await _call("cat /workspace/persistence-probe")
    assert marker in str(result.return_value)
    await _call(f"printf %s {marker!r} > /home/advanced-shell/persistence-probe")
    result = await _call("cat /home/advanced-shell/persistence-probe")
    assert marker in str(result.return_value)
    checks.append("persistent_workspace_and_home")

    result = await _call(
        'python -c \'import os; print(os.getenv("ASSISTANTMD_PROBE_SENTINEL", "absent"))\'; '
        "test ! -e /app/system; test ! -e /run/secrets; "
        'test ! -S /var/run/docker.sock; test "$(id -u)" = 1000; '
        "test ! -r /run/advanced-shell/ssh_host_ed25519_key; "
        "test ! -e /run/advanced-shell/advanced_shell_client"
    )
    _assert_status(result, "completed")
    assert "absent" in str(result.return_value)
    checks.append("environment_and_filesystem_isolation")

    result = await _call(
        "python -c 'import os,signal,time; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        'open("/workspace/timeout.pid","w").write(str(os.getpid())); '
        "time.sleep(300)'",
        timeout_seconds=0.5,
    )
    _assert_status(result, "timed_out")
    await asyncio.sleep(3)
    result = await _call(
        'pid=$(cat /workspace/timeout.pid); ! kill -0 "$pid" 2>/dev/null'
    )
    _assert_status(result, "completed")
    checks.append("timeout")

    result = await _call(
        "echo $$ > /workspace/output-limit.pid; printf stdout-prefix; "
        "printf stderr-prefix >&2; "
        "while true; do head -c 32768 /dev/zero | tr '\\0' x; "
        "head -c 32768 /dev/zero | tr '\\0' y >&2; done",
        timeout_seconds=10,
    )
    _assert_status(result, "output_limit_exceeded")
    assert int(result.metadata["output_bytes"]) > 2 * 1024 * 1024
    assert "stdout-prefix" in str(result.return_value)
    assert "stderr-prefix" in str(result.return_value)
    await asyncio.sleep(3)
    result = await _call(
        'pid=$(cat /workspace/output-limit.pid); ! kill -0 "$pid" 2>/dev/null'
    )
    _assert_status(result, "completed")
    checks.append("output_limit")

    cancellation = asyncio.create_task(
        _call(
            'python -c \'import os,time; open("/workspace/cancel.pid","w").write(str(os.getpid())); time.sleep(300)\''
        )
    )
    await asyncio.sleep(0.5)
    cancellation.cancel()
    try:
        await cancellation
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(3)
    result = await _call(
        'pid=$(cat /workspace/cancel.pid); ! kill -0 "$pid" 2>/dev/null'
    )
    _assert_status(result, "completed")
    checks.append("cancellation_descendant_cleanup")

    concurrent = await asyncio.gather(
        *[_call(f"sleep 0.1; printf concurrent-{index}") for index in range(20)]
    )
    assert all(item.metadata["status"] == "completed" for item in concurrent)
    assert all(
        f"concurrent-{index}" in str(item.return_value)
        for index, item in enumerate(concurrent)
    )
    checks.append("concurrency_20_governed")

    result = await _call("exit 255")
    _assert_status(result, "indeterminate_255")
    assert result.metadata["exit_code"] == 255
    checks.append("ssh_exit_255_is_explicitly_ambiguous")

    return {"status": "passed", "checks": checks, "check_count": len(checks)}


def main() -> None:
    os.environ["ASSISTANTMD_PROBE_SENTINEL"] = "must-not-cross-ssh"
    report = asyncio.run(run_probe())
    rendered = json.dumps(report, indent=2, sort_keys=True)
    LOG_PATH.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    print(f"wrote {LOG_PATH}")


if __name__ == "__main__":
    main()
