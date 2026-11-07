"""Real file/review/attachment boundaries exposed by branch hardening review."""

from __future__ import annotations

import asyncio
import json

import pytest

from validation.support.application import Application
from validation.support.chat.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.support.chat.inline_edit_reviews import (
    request_review,
    session_tool_results,
    submit_review,
)
from validation.support.runtime.task_runner_workloads import wait_metadata

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize(
    "change", ["source", "destination", "destination_created", "destination_retargeted"]
)
async def test_approved_copy_rechecks_states_after_waiting_for_session_gate(
    app: Application, change: str
) -> None:
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import chat_session_scope
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionGatePolicy, ExecutionTaskSpec

    vault = app.vault("QueuedCopyReviewVault")
    app.write(vault, "source.data", "reviewed source")
    if change != "destination_created":
        app.write(vault, "destination.data", "reviewed destination")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        session = "queued-copy"
        review = await request_review(
            app,
            server,
            vault.name,
            session,
            {
                "operation": "copy",
                "path": "source.data",
                "destination": "destination.data",
                "overwrite": True,
            },
        )
        runtime = get_runtime_context()
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold_gate():
            entered.set()
            await release.wait()

        holder = await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind="chat",
                scope=chat_session_scope(session),
                source="system",
                label="Hold review execution gate",
                authority=SYSTEM_AUTHORITY,
            ),
            lambda task: runtime.task_runner.run_with_gate(
                task,
                ExecutionGatePolicy(
                    key=chat_session_scope(session), queued_status="queued"
                ),
                hold_gate,
            ),
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        try:
            accepted = await submit_review(app, review, {})
            assert accepted.status_code == 200, accepted.text
            resumed_id = accepted.json()["task"]["task_id"]
            await wait_metadata(
                runtime, resumed_id, "waiting_for_task_id", holder.task_id
            )
            target = vault / (
                "source.data" if change == "source" else "destination.data"
            )
            if change == "destination_retargeted":
                app.write(vault, "other.data", "reviewed destination")
                target.unlink()
                target.symlink_to(vault / "other.data")
            else:
                target.write_text("changed after approval")
        finally:
            release.set()
            await app.wait_task(holder.task_id)
        await app.wait_task(resumed_id)
        expected_destination = (
            "reviewed destination"
            if change in {"source", "destination_retargeted"}
            else "changed after approval"
        )
        assert (vault / "destination.data").read_text() == expected_destination
        results = session_tool_results(vault.name, session)
        assert (
            json.loads(results[-1].result_metadata_json)["error_type"]
            == "review_target_changed"
        )


async def test_copy_destination_override_ignores_unused_old_destination(
    app: Application,
) -> None:
    vault = app.vault("CopyOverrideConflictVault")
    app.write(vault, "source.data", "source")
    app.write(vault, "old.data", "old destination")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        review = await request_review(
            app,
            server,
            vault.name,
            "copy-override",
            {
                "operation": "copy",
                "path": "source.data",
                "destination": "old.data",
                "overwrite": True,
            },
        )
        (vault / "old.data").write_text("unrelated update")
        accepted = await submit_review(app, review, {"destination": "new.data"})
        assert accepted.status_code == 200, accepted.text
        await app.wait_task(accepted.json()["task"]["task_id"])
        assert (vault / "new.data").read_text() == "source"
        assert (vault / "old.data").read_text() == "unrelated update"
