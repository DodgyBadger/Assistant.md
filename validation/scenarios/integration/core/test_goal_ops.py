"""Goal tools preserve source provenance, task activity and cleanup."""

from __future__ import annotations

import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.goal_job_inputs import (
    age_goals,
    goal_rows,
    tool_context,
    tool_result,
)

pytestmark = pytest.mark.scenario


async def test_goal_tool_provenance_activity_and_retention(app: Application) -> None:
    vault = app.vault("GoalOpsVault")
    await app.start()
    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.goals import GoalOpsStore
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.execution_tasks import (
        ExecutionTaskKind,
        ExecutionTaskSource,
        goal_task_metadata,
        workflow_vault_scope,
    )
    from core.runtime.state import get_runtime_context
    from core.system_migrations import get_system_migration_status
    from core.vault_state.file_mutations import mutate_vault_file

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        binding = resolve_tool_binding(["goal_ops"], vault_path=str(vault))
        assert binding.tool_names() == ["goal_ops"]
        tool = binding.tool_specs[0].tool_function.function
        ctx = tool_context(vault.name, session_id="goal_ops_chat_session")
        initial = [
            {"text": "Review source notes", "status": "pending"},
            {"text": "Draft briefing", "status": "pending"},
            {"text": "List open questions", "status": "pending"},
        ]
        checkpoint = app.events.checkpoint()
        goal = tool_result(
            await tool(
                ctx,
                operation="create_goal",
                data={
                    "title": "Prepare renewal briefing",
                    "objective": "Create a concise renewal briefing from project notes.",
                    "workspace_path_hint": "Clients/Acme",
                    "source_type": "workflow",
                    "source_id": "spoofed/source",
                    "success_criteria": ["Draft exists", "Open questions listed"],
                    "plan": initial,
                },
            )
        )
        goal_id = goal["goal_id"]
        assert goal_id.startswith("goal_")
        assert (
            goal["vault_name"],
            goal["workspace_path_hint"],
            goal["plan"],
            goal["source_type"],
            goal["source_id"],
            goal["source_label"],
        ) == (
            vault.name,
            "Clients/Acme",
            initial,
            "chat",
            "goal_ops_chat_session",
            "chat:goal_ops_chat_session",
        )
        updated_plan = [
            {"text": "Review source notes", "status": "completed"},
            {"text": "Draft briefing", "status": "in_progress"},
            {"text": "List open questions", "status": "pending"},
        ]
        updated = tool_result(
            await tool(
                ctx,
                operation="update_goal",
                goal_id=goal_id,
                data={
                    "plan": updated_plan,
                    "metadata": {"draft_path": "Clients/Acme/renewal-briefing.md"},
                    "reason": "Source review finished.",
                },
            )
        )
        assert (updated["plan"], updated["metadata"]) == (
            updated_plan,
            {"draft_path": "Clients/Acme/renewal-briefing.md"},
        )
        saved = tool_result(
            await tool(
                ctx,
                operation="checkpoint",
                goal_id=goal_id,
                data={
                    "summary": "Reviewed sources and started the renewal briefing draft.",
                    "current_state": "Risk section is started.",
                    "next_actions": ["Pull source quotes", "Finish risk table"],
                    "open_questions": [
                        "Conservative or aggressive pricing recommendation?"
                    ],
                    "risks": ["Security review owner remains unclear"],
                },
            )
        )
        assert saved["checkpoint_id"].startswith("checkpoint_") and saved[
            "next_actions"
        ] == ["Pull source quotes", "Finish risk table"]
        for event, operation in (
            ("goal_ops_goal_created", "create_goal"),
            ("goal_ops_goal_updated", "update_goal"),
            ("goal_ops_checkpoint_created", "checkpoint"),
        ):
            app.events.require(
                after=checkpoint,
                name=event,
                fields={
                    "vault_name": vault.name,
                    "operation": operation,
                    "goal_id": goal_id,
                },
            )
        app.events.require(
            after=checkpoint,
            name="goal_ops_goal_created",
            fields={
                "source_type": "chat",
                "source_id": "goal_ops_chat_session",
                "goal_id": goal_id,
            },
        )
        app.events.require(
            after=checkpoint,
            name="goal_ops_checkpoint_created",
            fields={"goal_id": goal_id, "checkpoint_id": saved["checkpoint_id"]},
        )
        latest = tool_result(await tool(ctx, operation="get_goal", goal_id=goal_id))
        assert (
            latest["latest_checkpoint"]["checkpoint_id"] == saved["checkpoint_id"]
            and "steps" not in latest
        )
        listed = tool_result(
            await tool(
                ctx,
                operation="list_goals",
                status="active",
                workspace_path_hint="Clients/Acme",
            )
        )
        assert [item["goal_id"] for item in listed] == [goal_id]
        assert [
            item["goal_id"]
            for item in GoalOpsStore().list_goals(
                vault_name=vault.name,
                source_type="chat",
                source_id="goal_ops_chat_session",
            )
        ] == [goal_id]
        listed = tool_result(
            await tool(
                ctx,
                operation="list_goals",
                data={"source": "current_session", "status": "any"},
            )
        )
        assert [item["goal_id"] for item in listed] == [goal_id]
        runtime = get_runtime_context()
        workflow_id = f"{vault.name}/goal-source-workflow"
        async with runtime.task_coordinator.track_current_task(
            authority=LOCAL_USER_AUTHORITY,
            kind=ExecutionTaskKind.WORKFLOW,
            scope=workflow_vault_scope(vault.name),
            source=ExecutionTaskSource.TOOL,
            label=workflow_id,
            metadata={"vault": vault.name, "workflow_id": workflow_id},
        ) as task:
            workflow = tool_result(
                await tool(
                    tool_context(vault.name, authoring_workflow_id=workflow_id),
                    operation="create_goal",
                    data={
                        "title": "Workflow-created goal",
                        "objective": "Exercise workflow source provenance.",
                    },
                )
            )
            assert (
                workflow["source_type"],
                workflow["source_id"],
                workflow["source_task_id"],
            ) == ("workflow", workflow_id, task.task_id)
        context_id = f"{vault.name}/context/default.md/goal_ops_chat_session"
        context = tool_result(
            await tool(
                tool_context(vault.name, authoring_workflow_id=context_id),
                operation="create_goal",
                data={
                    "title": "Context-created goal",
                    "objective": "Exercise context source provenance.",
                },
            )
        )
        assert (context["source_type"], context["source_id"]) == ("context", context_id)
        checkpoint = app.events.checkpoint()
        async with runtime.task_coordinator.track_current_task(
            authority=LOCAL_USER_AUTHORITY,
            kind=ExecutionTaskKind.WORKFLOW,
            scope=workflow_vault_scope(vault.name),
            source=ExecutionTaskSource.TOOL,
            label=f"{vault.name}/goal-scoped-write",
            metadata={"vault": vault.name, **goal_task_metadata(goal_id=goal_id)},
        ):
            mutate_vault_file(
                vault_path=vault,
                path="notes/goal-output.md",
                operation="write",
                mutator=lambda path: path.write_text("Goal output\n", encoding="utf-8"),
                create_parent=True,
            )
        app.events.require(
            after=checkpoint, name="execution_task_created", fields={"goal_id": goal_id}
        )
        app.events.require(
            after=checkpoint,
            name="vault_mutation_recorded",
            fields={"goal_id": goal_id, "path": "notes/goal-output.md"},
        )
        activity = tool_result(
            await tool(ctx, operation="list_activity", goal_id=goal_id)
        )
        assert (
            len(activity) == 1
            and activity[0]["goal_id"] == goal_id
            and activity[0]["mutations"][0]["path"] == "notes/goal-output.md"
        )
        with sqlite3.connect(app.root / "system/goal_ops.db") as conn:
            assert (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='goal_artifacts'"
                ).fetchone()
                is None
            )
        target = next(
            target
            for target in get_system_migration_status(app.root / "system").targets
            if target.db_name == "goal_ops"
        )
        assert target.pending_versions == ()
        store = GoalOpsStore()
        goals = [
            store.create_goal(
                vault_name=vault.name, title=title, objective="Retention fixture"
            )
            for title in (
                "Old completed",
                "Old cancelled",
                "Old active",
                "Recent completed",
            )
        ]
        old_done, old_cancelled, old_active, recent = [
            item["goal_id"] for item in goals
        ]
        store.update_goal(goal_id=old_done, status="completed", reason="Finished")
        store.checkpoint(goal_id=old_done, summary="Cleanup cascade marker")
        store.update_goal(
            goal_id=old_cancelled, status="cancelled", reason="No longer needed"
        )
        store.update_goal(
            goal_id=recent, status="completed", reason="Finished recently"
        )
        age_goals(app, [old_done, old_cancelled, old_active])
        cleanup = app.request(
            "/api/system/goals/cleanup",
            "POST",
            {
                "vault_name": vault.name,
                "status": "completed_or_cancelled",
                "older_than_days": 30,
            },
        )
        assert cleanup.status_code == 200 and cleanup.json()["deleted"] == 2
        assert (
            goal_rows(app, old_done) == (0, 0, 0)
            and goal_rows(app, old_cancelled)[0] == 0
        )
        assert goal_rows(app, old_active)[0] == 1 and goal_rows(app, recent)[0] == 1
