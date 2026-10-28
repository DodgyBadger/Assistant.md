"""Persisted session ownership and task-local execution authority."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.principal_authority import (
    AUTHORITY_WORKFLOW,
    capture_authority,
    create_legacy_chat_database,
    current_principal_id,
    wait_terminal,
)

pytestmark = pytest.mark.scenario


async def test_legacy_session_ownership_migrates_and_cannot_be_rebound(
    app: Application,
) -> None:
    from core.chat.chat_store import ChatStore
    from core.identity import LOCAL_USER_PRINCIPAL_ID
    from core.system_migrations import run_system_migrations

    system = app.root / "system"
    create_legacy_chat_database(system)
    ChatStore(str(system))
    run_system_migrations(system, backup=False)
    store = ChatStore(str(system))
    legacy = store.get_session_by_id("legacy-session")
    assert legacy is not None
    assert legacy.owner_principal_id == LOCAL_USER_PRINCIPAL_ID
    created = store.ensure_session(
        "owned-session", "PrincipalVault", owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    assert created.owner_principal_id == LOCAL_USER_PRINCIPAL_ID
    with pytest.raises(ValueError):
        store.ensure_session(
            "owned-session", "PrincipalVault", owner_principal_id="different-user"
        )
    unchanged = store.get_session_by_id("owned-session")
    assert unchanged is not None
    assert unchanged.owner_principal_id == LOCAL_USER_PRINCIPAL_ID
    with pytest.raises(ValueError):
        store.replace_session_messages("ownerless-session", "PrincipalVault", [])
    await app.start()
    from core.runtime.state import get_runtime_context

    migrated = get_runtime_context().chat_store.get_session_by_id("legacy-session")
    assert migrated is not None
    assert migrated.owner_principal_id == LOCAL_USER_PRINCIPAL_ID


async def test_runtime_authority_is_captured_for_nested_threaded_and_connected_tasks(
    app: Application,
) -> None:
    vault = app.vault("PrincipalVault")
    app.write(vault, "AssistantMD/Authoring/Authority.md", AUTHORITY_WORKFLOW)
    for secret in (
        "workflow-client-secret",
        "workflow-access-token",
        "workflow-refresh-token",
    ):
        app.evidence.register_secret(secret)
    await app.start()
    from core.connections import GoogleConnectionCreate
    from core.identity import (
        LOCAL_USER_AUTHORITY,
        LOCAL_USER_PRINCIPAL_ID,
        SYSTEM_AUTHORITY,
        ExecutionAuthority,
        get_current_execution_authority,
        require_current_execution_authority,
        use_execution_authority,
    )
    from core.integrations.google import (
        GMAIL_READONLY_SCOPE,
        GOOGLE_IDENTITY_SCOPES,
        GoogleOAuthTokenState,
    )
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionTaskSpec

    runtime = get_runtime_context()
    with pytest.raises(RuntimeError):
        runtime.chat_session_access.list_sessions("PrincipalVault")
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        session = runtime.chat_session_access.ensure_session(
            "runtime-owned-session", "PrincipalVault"
        )
    assert session.owner_principal_id == LOCAL_USER_PRINCIPAL_ID
    with use_execution_authority(ExecutionAuthority("different-user")):
        assert runtime.chat_session_access.get_session_by_id(session.session_id) is None
    with pytest.raises(RuntimeError):
        await runtime.execution_task_access.list_tasks()
    assert app.request("/api/tasks").status_code == 200
    assert get_current_execution_authority() is None

    checkpoint = app.events.checkpoint()
    observed: list[str | None] = []
    task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="principal:interactive",
            source=ExecutionTaskSource.API,
            label="principal-interactive",
            authority=LOCAL_USER_AUTHORITY,
        ),
        lambda _task: capture_authority(observed),
    )
    terminal = await wait_terminal(task.task_id)
    assert terminal.principal_id == LOCAL_USER_PRINCIPAL_ID
    assert observed == [LOCAL_USER_PRINCIPAL_ID]
    with use_execution_authority(ExecutionAuthority("different-user")):
        assert await runtime.execution_task_access.get_task(task.task_id) is None
    events = app.events.select(after=checkpoint, fields={"task_id": task.task_id})
    assert [event["name"] for event in events] == [
        "execution_task_created",
        "execution_task_started",
        "execution_task_completed",
    ]
    assert all(
        event["data"]["principal_id"] == LOCAL_USER_PRINCIPAL_ID for event in events
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
        lambda _task: capture_authority(system_observed),
    )
    assert system_observed == [SYSTEM_AUTHORITY.principal_id]
    assert get_current_execution_authority() is None
    nested_observed: list[str | None] = []

    async def run_nested(_task) -> None:
        inherited = require_current_execution_authority()
        await runtime.task_runner.run_inline(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.WORKFLOW,
                scope="principal:nested",
                source=ExecutionTaskSource.TOOL,
                label="principal-nested",
                authority=inherited,
            ),
            lambda _nested: capture_authority(nested_observed),
        )

    await runtime.task_runner.run_inline(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="principal:parent",
            source=ExecutionTaskSource.API,
            label="principal-parent",
            authority=LOCAL_USER_AUTHORITY,
        ),
        run_nested,
    )
    assert nested_observed == [LOCAL_USER_PRINCIPAL_ID]

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        runtime.built_in_connections.create_google_connection(
            GoogleConnectionCreate(
                display_name="Google", client_id="workflow.apps.googleusercontent.com"
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

    async def gmail_status(_task) -> dict[str, object]:
        assert runtime.gmail is not None
        return runtime.gmail.status(require_current_execution_authority())

    gmail = await runtime.task_runner.run_inline(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.WORKFLOW,
            scope="principal:gmail-workflow",
            source=ExecutionTaskSource.SCHEDULER,
            label="principal-gmail-workflow",
            authority=LOCAL_USER_AUTHORITY,
        ),
        gmail_status,
    )
    assert (gmail.get("available"), gmail.get("account_email")) == (
        True,
        "workflow@example.com",
    )
    thread_observed = await runtime.task_runner.run_inline(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.INGESTION,
            scope="principal:thread",
            source=ExecutionTaskSource.SCHEDULER,
            label="principal-thread",
            authority=SYSTEM_AUTHORITY,
        ),
        lambda _task: asyncio.to_thread(current_principal_id),
    )
    assert thread_observed == SYSTEM_AUTHORITY.principal_id
    assert get_current_execution_authority() is None

    # Exercise the governor entrypoint rather than its private authority resolver.
    for source in (ExecutionTaskSource.API, ExecutionTaskSource.SCHEDULER):
        with pytest.raises(RuntimeError):
            await runtime.workflow_governor.execute_workflow(
                global_id="PrincipalVault/Authority", source=source
            )
    checkpoint = app.events.checkpoint()
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        result = await runtime.workflow_governor.execute_workflow(
            global_id="PrincipalVault/Authority", source=ExecutionTaskSource.TOOL
        )
    assert result.to_dict()["status"] == "completed"
    app.events.require(
        after=checkpoint,
        name="execution_task_created",
        fields={
            "kind": "workflow",
            "source": "tool",
            "principal_id": LOCAL_USER_PRINCIPAL_ID,
            "label": "PrincipalVault/Authority",
        },
    )
    assert get_current_execution_authority() is None
