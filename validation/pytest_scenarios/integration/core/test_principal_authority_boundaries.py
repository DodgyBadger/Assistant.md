"""Caller-controlled identifiers and metadata cannot cross principal boundaries."""

from __future__ import annotations

import asyncio

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.principal_authority import wait_terminal

pytestmark = pytest.mark.scenario


async def test_foreign_session_and_task_identifiers_cannot_rebind_or_cancel(
    app: Application,
) -> None:
    await app.start()
    from core.identity import (
        ExecutionAuthority,
        get_current_execution_authority,
        use_execution_authority,
    )
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionTaskSpec

    runtime = get_runtime_context()
    owner = ExecutionAuthority("security-owner")
    attacker = ExecutionAuthority("security-attacker")
    with use_execution_authority(owner):
        owned = runtime.chat_session_access.ensure_session(
            "security-owned-session", "SecurityVault"
        )
    with use_execution_authority(attacker):
        assert runtime.chat_session_access.get_session_by_id(owned.session_id) is None
        assert runtime.chat_session_access.list_sessions("SecurityVault") == []
        with pytest.raises(LookupError):
            runtime.chat_session_access.ensure_session(
                owned.session_id, owned.vault_name
            )
    unchanged = runtime.chat_store.get_session_by_id(owned.session_id)
    assert unchanged is not None
    assert unchanged.owner_principal_id == owner.principal_id

    released = asyncio.Event()
    started = asyncio.Event()

    async def wait_for_release(_task) -> None:
        started.set()
        await released.wait()

    task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="security:owned-task",
            source=ExecutionTaskSource.API,
            label="security-owned-task",
            authority=owner,
            metadata={"principal_id": attacker.principal_id},
        ),
        wait_for_release,
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        with use_execution_authority(attacker):
            assert await runtime.execution_task_access.get_task(task.task_id) is None
            assert await runtime.execution_task_access.cancel_task(task.task_id) is None
        snapshot = await runtime.task_coordinator.get_task(task.task_id)
        assert snapshot is not None
        assert snapshot.cancel_requested is False
        assert snapshot.principal_id == owner.principal_id
    finally:
        released.set()
        await wait_terminal(task.task_id)
    assert get_current_execution_authority() is None
