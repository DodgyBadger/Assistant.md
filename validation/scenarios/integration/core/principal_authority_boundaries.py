"""Adversarial checks for principal-owned runtime resource boundaries."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(
        prefix="assistantmd-principal-authority-"
    )
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    system_root = direct_root / "system"
    data_root.mkdir()
    system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=system_root)

from core.identity import (  # noqa: E402
    ExecutionAuthority,
    get_current_execution_authority,
)
from core.identity.context import use_execution_authority  # noqa: E402
from core.runtime.execution_tasks import (  # noqa: E402
    ExecutionTaskKind,
    ExecutionTaskSource,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from core.runtime.task_runner import ExecutionTaskSpec  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class PrincipalAuthorityBoundariesScenario(BaseScenario):
    """Prove untrusted identifiers and metadata cannot cross owner boundaries."""

    async def test_scenario(self) -> None:
        await self.start_system()
        runtime = get_runtime_context()
        owner = ExecutionAuthority("security-owner")
        attacker = ExecutionAuthority("security-attacker")

        with use_execution_authority(owner):
            owned_session = runtime.chat_session_access.ensure_session(
                "security-owned-session",
                "SecurityVault",
            )

        with use_execution_authority(attacker):
            self.soft_assert_equal(
                runtime.chat_session_access.get_session_by_id(owned_session.session_id),
                None,
                "Cross-principal session reads should be concealed",
            )
            self.soft_assert_equal(
                runtime.chat_session_access.list_sessions("SecurityVault"),
                [],
                "Cross-principal session lists should omit foreign sessions",
            )
            concealed_touch = False
            try:
                runtime.chat_session_access.ensure_session(
                    owned_session.session_id,
                    owned_session.vault_name,
                )
            except LookupError:
                concealed_touch = True
            self.soft_assert(
                concealed_touch,
                "A caller-selected foreign session ID should fail as not found",
            )

        unchanged = runtime.chat_store.get_session_by_id(owned_session.session_id)
        self.soft_assert_equal(
            unchanged.owner_principal_id if unchanged else None,
            owner.principal_id,
            "A denied touch should not rebind session ownership",
        )

        release_task = asyncio.Event()
        started_task = asyncio.Event()

        async def _wait_for_release(_task) -> None:
            started_task.set()
            await release_task.wait()

        task = await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="security:owned-task",
                source=ExecutionTaskSource.API,
                label="security-owned-task",
                authority=owner,
                metadata={"principal_id": attacker.principal_id},
            ),
            _wait_for_release,
        )
        await asyncio.wait_for(started_task.wait(), timeout=2)

        with use_execution_authority(attacker):
            self.soft_assert_equal(
                await runtime.execution_task_access.get_task(task.task_id),
                None,
                "Task metadata should not grant access to another principal",
            )
            self.soft_assert_equal(
                await runtime.execution_task_access.cancel_task(task.task_id),
                None,
                "A foreign principal should not be able to cancel an owned task",
            )

        owner_snapshot = await runtime.task_coordinator.get_task(task.task_id)
        self.soft_assert_equal(
            owner_snapshot.cancel_requested if owner_snapshot else None,
            False,
            "A denied cancellation should not mutate task state",
        )
        self.soft_assert_equal(
            owner_snapshot.principal_id if owner_snapshot else None,
            owner.principal_id,
            "Untrusted metadata should not replace captured task authority",
        )

        release_task.set()
        await self._wait_for_task_terminal(task.task_id)
        self.soft_assert_equal(
            get_current_execution_authority(),
            None,
            "Adversarial checks should not leak authority into the caller context",
        )
        self.assert_no_failures()
        self.teardown_scenario()

    @staticmethod
    async def _wait_for_task_terminal(task_id: str) -> None:
        runtime = get_runtime_context()
        for _ in range(100):
            task = await runtime.task_coordinator.get_task(task_id)
            if task is not None and task.is_terminal:
                return
            await asyncio.sleep(0.02)
        raise TimeoutError(f"Execution task did not finish: {task_id}")
