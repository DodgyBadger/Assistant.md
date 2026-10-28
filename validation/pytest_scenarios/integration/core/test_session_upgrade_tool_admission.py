"""Legacy context upgrades reject malformed canonical tool histories."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.session_map_journey_inputs import (
    add_recovery_checkpoint,
    conversation,
    seed_session,
)

pytestmark = pytest.mark.scenario


async def test_upgrade_readiness_and_admission_follow_tool_history_integrity(
    app: Application,
) -> None:
    vault = app.vault("SessionUpgradeAdmissionVault")
    await app.start()
    from core.chat.context_strategy_upgrade import get_session_context_strategy_status
    from core.runtime.execution_tasks import ExecutionTaskKind, chat_session_scope
    from core.runtime.state import get_runtime_context
    from validation.support.tool_history_fixtures import tool_reply_cases

    for key, value in (
        ("compaction_strategy", "session_map"),
        ("compaction_author_model", "test"),
        ("compaction_author_thinking", "low"),
        ("compaction_low_watermark_tokens", "250"),
        ("compaction_retained_turns", "2"),
        ("compaction_high_watermark_tokens", "500"),
        ("compaction_type", "auto"),
    ):
        assert (
            app.request(
                f"/api/system/settings/general/{key}", "PUT", {"value": value}
            ).status_code
            == 200
        )
    runtime = get_runtime_context()
    store = runtime.chat_store
    for case in tool_reply_cases():
        session = f"upgrade-reply-{case.name}"
        messages = conversation(
            [
                (
                    f"Question {i} " + "substantive planning evidence " * 12,
                    f"Answer {i} " + "substantive planning evidence " * 12,
                )
                for i in range(6)
            ]
        )
        seed_session(store, session, vault.name, messages)
        add_recovery_checkpoint(store, session, vault.name, f"{session}-recovery", 5)
        store.add_messages(session, vault.name, case.messages)
        status = get_session_context_strategy_status(
            store=store, session_id=session, vault_name=vault.name
        )
        assert status.can_upgrade_to_v2 is (not case.issue_codes), case.name
        if case.issue_codes:
            rejected = await app.request_async(
                f"/api/chat/sessions/{session}/upgrade-context-strategy",
                "POST",
                {"vault_name": vault.name},
            )
            assert (
                rejected.status_code == 409
                and rejected.json()["details"]["reason"]
                == "upgrade_plan_invalid_tool_history"
            ), case.name
            assert not await runtime.task_coordinator.list_tasks(
                kind=ExecutionTaskKind.CONTEXT_STRATEGY_UPGRADE,
                scope=chat_session_scope(session),
            )
