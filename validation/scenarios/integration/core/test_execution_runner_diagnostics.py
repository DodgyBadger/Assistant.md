"""Real workload failures and timeouts retain bounded, content-safe task diagnostics."""

from __future__ import annotations

import asyncio
import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.task_runner_workloads import (
    ContentFailureWorkload,
    _slow_task,
    bounded_failure_workload,
    oversized_result,
    wait_terminal,
)

pytestmark = pytest.mark.scenario


async def test_runner_timeouts_remain_distinct_and_failure_diagnostics_are_bounded(
    app: Application,
) -> None:
    app.vault("ExecutionRunnerDiagnosticsVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionTaskSpec

    runtime = get_runtime_context()
    checkpoint = app.events.checkpoint()
    for suffix in ("one", "two"):
        task = await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.DELEGATE,
                scope=f"runner:timeout-warning:{suffix}",
                source=ExecutionTaskSource.SYSTEM,
                label=f"runner-timeout-warning-{suffix}",
                authority=SYSTEM_AUTHORITY,
                timeout_seconds=0.01,
                timeout_reason="validation_timeout",
            ),
            _slow_task,
        )
        terminal = await wait_terminal(runtime, task.task_id)
        assert terminal.status == "timed_out"
    assert len(app.events.select("execution_task_timed_out", after=checkpoint)) == 2
    app.evidence.register_secret("sensitive-sentinel-")
    checkpoint = app.events.checkpoint()
    failed = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.DELEGATE,
            scope="runner:bounded-failure",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-bounded-failure",
            authority=SYSTEM_AUTHORITY,
        ),
        bounded_failure_workload,
    )
    assert (await wait_terminal(runtime, failed.task_id)).status == "failed"
    data = app.events.require(
        "execution_task_failed", after=checkpoint, task_id=failed.task_id
    )["data"]
    assert data["error_type"] == "ValidationFailure"
    assert len(str(data.get("error") or "")) <= 503
    response = app.request(
        "/api/system/activity-log",
        params={"limit": 100, "tag": "execution-tasks", "search": failed.task_id},
    )
    assert response.status_code == 200
    failures = [
        entry["data"]
        for entry in response.json()["entries"]
        if entry.get("data", {}).get("event") == "execution_task_failed"
    ]
    assert failures and failures[-1]["error_type"] == "ValidationFailure"
    assert len(str(failures[-1].get("error") or "")) <= 503


@pytest.mark.parametrize(
    "kind", ["history_compaction", "session_map_authoring", "context_strategy_upgrade"]
)
async def test_runner_inference_failures_preserve_original_exception_and_safe_records(
    app: Application, kind: str
) -> None:
    app.vault("ExecutionRunnerContentSafetyVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionTaskHooks, ExecutionTaskSpec

    runtime = get_runtime_context()
    marker = f"PRIVATE-TASK-CONTENT-{kind}"
    app.evidence.register_secret(marker)
    workload = ContentFailureWorkload(marker)
    spec = ExecutionTaskSpec(
        kind=kind,
        scope=f"runner:content-safe:{kind}",
        source=ExecutionTaskSource.SYSTEM,
        label=f"content-safe:{kind}",
        authority=SYSTEM_AUTHORITY,
    )
    with pytest.raises(RuntimeError) as raised:
        await runtime.task_runner.run_inline(spec, workload.fail)
    assert raised.value is workload.failure
    inline = await runtime.task_coordinator.get_task(workload.task_ids[-1])
    assert inline is not None and inline.status == "failed"
    assert inline.terminal_error_type == "RuntimeError"
    assert marker not in str(inline.terminal_reason)
    assert kind in str(inline.terminal_reason)
    task = await runtime.task_runner.start_background(
        spec, workload.fail, hooks=ExecutionTaskHooks(on_failed=workload.on_failed)
    )
    terminal = await wait_terminal(runtime, task.task_id)
    assert terminal.status == "failed"
    assert terminal.terminal_error_type == "RuntimeError"
    assert marker not in str(terminal.terminal_reason)
    assert kind in str(terminal.terminal_reason)
    await asyncio.wait_for(workload.hook_finished.wait(), timeout=2)
    assert workload.hook_errors == [workload.failure]
    response = app.request(
        "/api/system/activity-log", params={"limit": 100, "tag": "execution-tasks"}
    )
    assert response.status_code == 200
    assert marker not in json.dumps(response.json())


async def test_runner_bounds_actual_workload_result_and_preserves_valid_artifact_references(
    app: Application,
) -> None:
    app.vault("ExecutionRunnerResultsVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import (
        EXECUTION_TASK_RESULT_MAX_CHARS,
        ExecutionTaskKind,
        ExecutionTaskSource,
    )
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionTaskSpec

    runtime = get_runtime_context()
    task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:terminal-result",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-terminal-result",
            authority=SYSTEM_AUTHORITY,
        ),
        oversized_result,
    )
    await wait_terminal(runtime, task.task_id)
    waited = await runtime.task_coordinator.wait_for_tasks(
        [task.task_id],
        after_revisions={task.task_id: 0},
        timeout_seconds=1,
        terminal_or_attention_only=True,
    )
    assert len(waited.snapshots) == 1
    snapshot = waited.snapshots[0]
    assert snapshot.status == "completed"
    assert snapshot.result_truncated is True
    assert snapshot.result["artifact_references"] == ["vault://report.md"]
    assert (
        len(json.dumps(snapshot.result, ensure_ascii=False, sort_keys=True))
        <= EXECUTION_TASK_RESULT_MAX_CHARS
    )
