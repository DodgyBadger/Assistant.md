"""Delegate's failure guard classifies actual file results and admits parallel work."""

from __future__ import annotations

import asyncio
import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.delegate_guard_inputs import GuardInputs

pytestmark = pytest.mark.scenario


async def test_delegate_failure_guard_uses_actual_file_results(app: Application):
    vault = app.vault("DelegateGuardInputs")
    app.write(vault, "notes/exists.md", "actual successful read")
    await app.start()
    from core.llm.capabilities.delegate_repeated_failure_guard import (
        DelegateRepeatedFailureGuard,
    )

    workload = GuardInputs(vault)
    guard = DelegateRepeatedFailureGuard(limit=2, session_id="guard-contract")
    await guard.execute(
        tool_name="file_read", args={"a": 1, "b": 2}, handler=workload.fail
    )
    await guard.execute(
        tool_name="file_read", args={"b": 2, "a": 1}, handler=workload.fail
    )
    blocked = await guard.execute(
        tool_name="file_read", args={"a": 1, "b": 2}, handler=workload.fail
    )
    assert len(workload.executed) == 2
    assert blocked.metadata["failure_kind"] == "repeated_tool_failure"
    await guard.execute(tool_name="other", args={}, handler=workload.succeed)
    await guard.execute(
        tool_name="file_read", args={"a": 1, "b": 2}, handler=workload.fail
    )
    assert len(workload.executed) == 4
    disabled = DelegateRepeatedFailureGuard(limit=0, session_id="guard-disabled")
    for _ in range(3):
        await disabled.execute(tool_name="file_read", args={}, handler=workload.fail)
    assert len(workload.executed) == 7
    successes = DelegateRepeatedFailureGuard(limit=1, session_id="guard-success")
    for _ in range(3):
        await successes.execute(
            tool_name="file_read", args={"same": True}, handler=workload.succeed
        )
    assert len(workload.executed) == 10
    exception_guard = DelegateRepeatedFailureGuard(
        limit=1, session_id="guard-exception-reset"
    )
    await exception_guard.execute(
        tool_name="file_read", args={"same": True}, handler=workload.fail
    )
    with pytest.raises(json.JSONDecodeError):
        await exception_guard.execute(
            tool_name="other", args={}, handler=workload.parse_invalid
        )
    before = len(workload.executed)
    await exception_guard.execute(
        tool_name="file_read", args={"same": True}, handler=workload.fail
    )
    assert len(workload.executed) == before + 1
    parallel = DelegateRepeatedFailureGuard(limit=1, session_id="guard-parallel")
    await asyncio.wait_for(
        asyncio.gather(
            *(
                parallel.execute(
                    tool_name="file_read",
                    args={"same": True},
                    handler=workload.parallel_failure,
                )
                for _ in range(2)
            )
        ),
        timeout=5,
    )
    assert workload.parallel_started == 2
