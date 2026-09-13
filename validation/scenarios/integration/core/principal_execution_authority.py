"""Validate single-principal ownership and execution-authority propagation."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(
        prefix="assistantmd-principal-authority-"
    )
    direct_root = Path(_direct_run_root.name)
    direct_data_root = direct_root / "data"
    direct_system_root = direct_root / "system"
    direct_data_root.mkdir()
    direct_system_root.mkdir()
    set_bootstrap_roots(direct_data_root, direct_system_root)

from core.chat.chat_store import ChatStore  # noqa: E402
from core.connections import GoogleConnectionCreate  # noqa: E402
from core.identity import (  # noqa: E402
    LOCAL_USER_AUTHORITY,
    LOCAL_USER_PRINCIPAL_ID,
    SYSTEM_AUTHORITY,
    ExecutionAuthority,
    get_current_execution_authority,
    require_current_execution_authority,
    use_execution_authority,
)
from core.integrations.google import (  # noqa: E402
    GMAIL_READONLY_SCOPE,
    GOOGLE_IDENTITY_SCOPES,
    GoogleOAuthTokenState,
)
from core.runtime.execution_tasks import (  # noqa: E402
    ExecutionTaskKind,
    ExecutionTaskSource,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from core.runtime.task_runner import ExecutionTaskSpec  # noqa: E402
from core.runtime.workflow_governor import (  # noqa: E402
    _authority_for_workflow_source,
)
from core.system_migrations import run_system_migrations  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class PrincipalExecutionAuthorityScenario(BaseScenario):
    """Validate legacy ownership migration and task-local authority."""

    async def test_scenario(self) -> None:
        system_root = self.artifacts_dir / "system"
        system_root.mkdir(parents=True, exist_ok=True)
        chat_db = system_root / "chat_sessions.db"
        self._create_legacy_chat_database(chat_db)

        ChatStore(str(system_root))
        run_system_migrations(system_root, backup=False)
        store = ChatStore(str(system_root))
        legacy = store.get_session_by_id("legacy-session")
        self.soft_assert_equal(
            legacy.owner_principal_id if legacy else None,
            LOCAL_USER_PRINCIPAL_ID,
            "Legacy sessions should migrate to the local user",
        )

        created = store.ensure_session(
            "owned-session",
            "PrincipalVault",
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        self.soft_assert_equal(
            created.owner_principal_id,
            LOCAL_USER_PRINCIPAL_ID,
            "New sessions should persist their owner",
        )
        mismatch_rejected = False
        try:
            store.ensure_session(
                "owned-session",
                "PrincipalVault",
                owner_principal_id="different-user",
            )
        except ValueError:
            mismatch_rejected = True
        self.soft_assert(
            mismatch_rejected,
            "Existing session ownership should be immutable",
        )
        unchanged = store.get_session_by_id("owned-session")
        self.soft_assert_equal(
            unchanged.owner_principal_id if unchanged else None,
            LOCAL_USER_PRINCIPAL_ID,
            "A rejected owner rebind should not modify the stored owner",
        )
        ownerless_creation_rejected = False
        try:
            store.replace_session_messages(
                "ownerless-session",
                "PrincipalVault",
                [],
            )
        except ValueError:
            ownerless_creation_rejected = True
        self.soft_assert(
            ownerless_creation_rejected,
            "Raw store paths should reject ownerless session creation",
        )

        await self.start_system()
        runtime = get_runtime_context()
        missing_session_authority_rejected = False
        try:
            runtime.chat_session_access.list_sessions("PrincipalVault")
        except RuntimeError:
            missing_session_authority_rejected = True
        self.soft_assert(
            missing_session_authority_rejected,
            "Runtime session access should fail without request or task authority",
        )
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            runtime_session = runtime.chat_session_access.ensure_session(
                "runtime-owned-session",
                "PrincipalVault",
            )
        self.soft_assert_equal(
            runtime_session.owner_principal_id,
            LOCAL_USER_PRINCIPAL_ID,
            "The session gateway should assign the active authority as owner",
        )
        with use_execution_authority(ExecutionAuthority("different-user")):
            concealed_session = runtime.chat_session_access.get_session_by_id(
                runtime_session.session_id
            )
        self.soft_assert_equal(
            concealed_session,
            None,
            "Session access should conceal records owned by another principal",
        )

        missing_access_authority_rejected = False
        try:
            await runtime.execution_task_access.list_tasks()
        except RuntimeError:
            missing_access_authority_rejected = True
        self.soft_assert(
            missing_access_authority_rejected,
            "Runtime task access should fail without request or task authority",
        )

        task_list_response = self.call_api("/api/tasks")
        self.soft_assert_equal(
            task_list_response.status_code,
            200,
            "The API router should install interactive authority for task access",
        )
        self.soft_assert_equal(
            get_current_execution_authority(),
            None,
            "Request authority should reset after the API response",
        )

        lifecycle_checkpoint = self.event_checkpoint()
        observed: list[str | None] = []
        task = await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="principal:interactive",
                source=ExecutionTaskSource.API,
                label="principal-interactive",
                authority=LOCAL_USER_AUTHORITY,
            ),
            lambda _task: _capture_authority(observed),
        )
        terminal = await self._wait_for_task_terminal(task.task_id)
        self.soft_assert_equal(
            terminal.principal_id if terminal else None,
            LOCAL_USER_PRINCIPAL_ID,
            "Task snapshots should retain the captured principal",
        )
        self.soft_assert_equal(
            observed,
            [LOCAL_USER_PRINCIPAL_ID],
            "Detached workers should observe the captured authority",
        )
        with use_execution_authority(ExecutionAuthority("different-user")):
            concealed_task = await runtime.execution_task_access.get_task(task.task_id)
        self.soft_assert_equal(
            concealed_task,
            None,
            "Task access should conceal work owned by another principal",
        )
        lifecycle_events = [
            event
            for event in self.events_since(lifecycle_checkpoint)
            if event.get("data", {}).get("task_id") == task.task_id
        ]
        self.soft_assert_equal(
            [event.get("name") for event in lifecycle_events],
            [
                "execution_task_created",
                "execution_task_started",
                "execution_task_completed",
            ],
            "Task lifecycle should emit created, started, and terminal events",
        )
        self.soft_assert(
            all(
                event.get("data", {}).get("principal_id") == LOCAL_USER_PRINCIPAL_ID
                for event in lifecycle_events
            ),
            "Task lifecycle events should retain the captured principal",
        )

        system_observed: list[str | None] = []
        await runtime.task_runner.run_inline(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.HISTORY_COMPACTION,
                scope="principal:system",
                source=ExecutionTaskSource.SYSTEM,
                label="principal-system",
                authority=SYSTEM_AUTHORITY,
            ),
            lambda _task: _capture_authority(system_observed),
        )
        self.soft_assert_equal(
            system_observed,
            [SYSTEM_AUTHORITY.principal_id],
            "System work should observe system authority",
        )
        self.soft_assert_equal(
            get_current_execution_authority(),
            None,
            "Execution authority should reset after task completion",
        )

        nested_observed: list[str | None] = []

        async def _run_nested(_task) -> None:
            inherited = require_current_execution_authority()
            await runtime.task_runner.run_inline(
                ExecutionTaskSpec(
                    kind=ExecutionTaskKind.WORKFLOW,
                    scope="principal:nested",
                    source=ExecutionTaskSource.TOOL,
                    label="principal-nested",
                    authority=inherited,
                ),
                lambda _nested_task: _capture_authority(nested_observed),
            )

        await runtime.task_runner.run_inline(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="principal:parent",
                source=ExecutionTaskSource.API,
                label="principal-parent",
                authority=LOCAL_USER_AUTHORITY,
            ),
            _run_nested,
        )
        self.soft_assert_equal(
            nested_observed,
            [LOCAL_USER_PRINCIPAL_ID],
            "Nested work should inherit the active authority explicitly",
        )

        with use_execution_authority(LOCAL_USER_AUTHORITY):
            runtime.built_in_connections.create_google_connection(
                GoogleConnectionCreate(
                    display_name="Google",
                    client_id="workflow.apps.googleusercontent.com",
                )
            )
            assert runtime.google_connection is not None
            runtime.google_connection.set_client_secret(
                LOCAL_USER_AUTHORITY, "workflow-client-secret"
            )
            runtime.google_connection.save_token_state(
                LOCAL_USER_AUTHORITY,
                GoogleOAuthTokenState(
                    access_token="workflow-access-token",
                    refresh_token="workflow-refresh-token",
                    expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    scopes=(*GOOGLE_IDENTITY_SCOPES, GMAIL_READONLY_SCOPE),
                    account_id="workflow-account",
                    account_email="workflow@example.com",
                ),
            )

        async def _gmail_workflow_status(_task) -> dict[str, object]:
            assert runtime.gmail is not None
            return runtime.gmail.status(require_current_execution_authority())

        gmail_workflow_status = await runtime.task_runner.run_inline(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.WORKFLOW,
                scope="principal:gmail-workflow",
                source=ExecutionTaskSource.SCHEDULER,
                label="principal-gmail-workflow",
                authority=LOCAL_USER_AUTHORITY,
            ),
            _gmail_workflow_status,
        )
        self.soft_assert_equal(
            (
                gmail_workflow_status.get("available"),
                gmail_workflow_status.get("account_email"),
            ),
            (True, "workflow@example.com"),
            "A scheduled workflow should resolve its local-user Gmail connection",
        )

        thread_observed = await runtime.task_runner.run_inline(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.INGESTION,
                scope="principal:thread",
                source=ExecutionTaskSource.SCHEDULER,
                label="principal-thread",
                authority=SYSTEM_AUTHORITY,
            ),
            lambda _task: asyncio.to_thread(_current_principal_id),
        )
        self.soft_assert_equal(
            thread_observed,
            SYSTEM_AUTHORITY.principal_id,
            "Threaded workers should retain captured system authority",
        )
        self.soft_assert_equal(
            get_current_execution_authority(),
            None,
            "Nested and threaded execution should reset authority context",
        )
        missing_workflow_authority_rejected = False
        try:
            _authority_for_workflow_source(ExecutionTaskSource.API)
        except RuntimeError:
            missing_workflow_authority_rejected = True
        self.soft_assert(
            missing_workflow_authority_rejected,
            "API workflows should fail without request or task authority",
        )
        missing_scheduler_authority_rejected = False
        try:
            _authority_for_workflow_source(ExecutionTaskSource.SCHEDULER)
        except RuntimeError:
            missing_scheduler_authority_rejected = True
        self.soft_assert(
            missing_scheduler_authority_rejected,
            "Scheduled workflows should require their captured owner authority",
        )
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            inherited_workflow = _authority_for_workflow_source(
                ExecutionTaskSource.TOOL
            )
        self.soft_assert_equal(
            inherited_workflow.principal_id,
            LOCAL_USER_PRINCIPAL_ID,
            "Tool workflows should inherit current authority",
        )
        self.assert_no_failures()
        self.teardown_scenario()

    @staticmethod
    async def _wait_for_task_terminal(task_id: str):
        runtime = get_runtime_context()
        for _ in range(100):
            task = await runtime.task_coordinator.get_task(task_id)
            if task is not None and task.is_terminal:
                return task
            await asyncio.sleep(0.02)
        return None

    @staticmethod
    def _create_legacy_chat_database(path: Path) -> None:
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE chat_sessions (
                    session_id TEXT NOT NULL,
                    vault_name TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_activity_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    title TEXT,
                    metadata_json TEXT,
                    PRIMARY KEY (session_id, vault_name)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO chat_sessions (session_id, vault_name)
                VALUES ('legacy-session', 'PrincipalVault')
                """
            )


async def _capture_authority(observed: list[str | None]) -> None:
    authority = get_current_execution_authority()
    observed.append(authority.principal_id if authority else None)


def _current_principal_id() -> str | None:
    authority = get_current_execution_authority()
    return authority.principal_id if authority else None
