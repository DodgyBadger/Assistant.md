"""Recovery policy metadata and production decisions over explicit persisted frontiers."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.recovery_inputs import (
    _assert_snapshot_retention_is_bounded,
    _unresolved_decision,
)
from validation.scenarios.support.recovery_replay import replay_pending_read

pytestmark = pytest.mark.scenario


async def test_tool_recovery_policies_decide_and_replay_real_read(
    app: Application,
) -> None:
    vault = app.vault("ToolRecoveryPolicyVault")
    app.write(vault, "notes/recovery.md", "RECOVERY_READ_CONTENT")
    await app.start()
    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.chat.run_recovery import ChatRecoveryStrategy, ChatRunRecoveryCoordinator
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.tools.base import (
        BaseTool,
        ToolRecoveryPolicy,
        recovery_policy_from_tool_metadata,
        tool_recovery_metadata,
    )
    from core.tools.code_execution import CodeExecution

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        binding = resolve_tool_binding(
            "file_read, file_write",
            vault_path=str(vault),
        )
        specs = {spec.name: spec for spec in binding.tool_specs}
        assert specs["file_read"].recovery_policy is ToolRecoveryPolicy.REPLAY_SAFE
        assert (
            specs["file_write"].recovery_policy
            is ToolRecoveryPolicy.VAULT_TRANSACTIONAL
        )

        coordinator = ChatRunRecoveryCoordinator.from_tools(binding.tool_functions)
        assert coordinator.tool_policy("file_read") is ToolRecoveryPolicy.REPLAY_SAFE
        assert (
            coordinator.tool_policy("file_write")
            is ToolRecoveryPolicy.VAULT_TRANSACTIONAL
        )
        assert coordinator.tool_policy("unregistered") is ToolRecoveryPolicy.UNKNOWN
        assert BaseTool.get_recovery_policy() is ToolRecoveryPolicy.UNKNOWN
        assert CodeExecution.get_recovery_policy() is ToolRecoveryPolicy.UNKNOWN
        assert tool_recovery_metadata(ToolRecoveryPolicy.REPLAY_SAFE) == {
            "recovery_policy": "replay_safe"
        }
        assert (
            recovery_policy_from_tool_metadata(
                {"assistantmd": {"recovery_policy": "replay_safe"}}
            )
            is ToolRecoveryPolicy.REPLAY_SAFE
        )
        assert (
            recovery_policy_from_tool_metadata(
                {"assistantmd": {"recovery_policy": "invalid"}}
            )
            is ToolRecoveryPolicy.UNKNOWN
        )
        await _assert_snapshot_retention_is_bounded()

        assert binding.tool_instructions == ""

        replay = await _unresolved_decision(ToolRecoveryPolicy.REPLAY_SAFE)
        assert replay.strategy is ChatRecoveryStrategy.REPLAY_NO_EFFECT
        assert replay.checkpoint is not None
        assert replay.checkpoint.interrupted is True
        await replay_pending_read(app, vault, replay)

        rollback = await _unresolved_decision(ToolRecoveryPolicy.VAULT_TRANSACTIONAL)
        assert rollback.strategy is ChatRecoveryStrategy.TERMINAL_ROLLBACK_RESTART
        assert rollback.reason == "unresolved_vault_effect"

        manual = await _unresolved_decision(ToolRecoveryPolicy.MANUAL_REQUIRED)
        assert manual.strategy is ChatRecoveryStrategy.MANUAL_REQUIRED
        unknown = await _unresolved_decision(ToolRecoveryPolicy.UNKNOWN)
        assert unknown.strategy is ChatRecoveryStrategy.MANUAL_REQUIRED
        mixed = await _unresolved_decision(
            ToolRecoveryPolicy.VAULT_TRANSACTIONAL,
            additional_policy=ToolRecoveryPolicy.UNKNOWN,
        )
        assert mixed.strategy is ChatRecoveryStrategy.MANUAL_REQUIRED
        assert mixed.reason == "unresolved_external_or_unknown_effect"
        assert mixed.unresolved_tool_count == 2
