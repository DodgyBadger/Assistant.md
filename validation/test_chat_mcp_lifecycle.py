"""Focused lease-ownership tests for chat preparation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic_ai import DeferredToolResults

from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = Path("/tmp/assistantmd-chat-mcp-lifecycle-tests")
(_TEST_ROOT / "data").mkdir(parents=True, exist_ok=True)
(_TEST_ROOT / "system").mkdir(parents=True, exist_ok=True)
set_bootstrap_roots(_TEST_ROOT / "data", _TEST_ROOT / "system")

import core.chat.executor as executor  # noqa: E402
from core.llm.capabilities.mcp_tools import MCPChatCapabilities  # noqa: E402
from core.mcp import MCPReadinessSnapshot  # noqa: E402


class _Snapshot:
    def __init__(self) -> None:
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


class _ChatStore:
    def get_session_workspace_path(self, session_id: str, vault_name: str) -> Path:
        del session_id, vault_name
        return Path("/tmp/assistantmd-chat-mcp-lifecycle")


def _patch_preparation_until_shell(
    monkeypatch: pytest.MonkeyPatch, snapshot: _Snapshot
) -> None:
    async def acquire_mcp() -> MCPChatCapabilities:
        return MCPChatCapabilities(
            capabilities=(),
            snapshot=cast(MCPReadinessSnapshot, snapshot),
            unavailable=(),
            model_tool_names=(),
        )

    async def fail_shell() -> Any:
        raise RuntimeError("shell preflight failed")

    monkeypatch.setattr(executor, "_CHAT_STORE", _ChatStore())
    monkeypatch.setattr(
        executor,
        "_prepare_agent_config",
        lambda *_args, **_kwargs: ("base", "tools", object(), []),
    )
    monkeypatch.setattr(
        executor,
        "_resolve_image_prompt",
        lambda **_kwargs: ("prompt", "prompt", 0),
    )
    monkeypatch.setattr(executor, "_acquire_chat_mcp_capabilities", acquire_mcp)
    monkeypatch.setattr(
        executor, "_acquire_primary_chat_advanced_shell_tool", fail_shell
    )


@pytest.mark.asyncio
async def test_primary_preparation_releases_mcp_snapshot_on_shell_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _Snapshot()
    _patch_preparation_until_shell(monkeypatch, snapshot)

    with pytest.raises(RuntimeError, match="shell preflight failed"):
        await executor._prepare_chat_execution(
            "vault",
            "/tmp/vault",
            "prompt",
            None,
            None,
            "session",
            [],
            "test-model",
        )

    assert snapshot.close_count == 1


@pytest.mark.asyncio
async def test_deferred_preparation_releases_mcp_snapshot_on_shell_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _Snapshot()
    _patch_preparation_until_shell(monkeypatch, snapshot)

    with pytest.raises(RuntimeError, match="shell preflight failed"):
        await executor._prepare_deferred_review_resume_execution(
            vault_name="vault",
            vault_path="/tmp/vault",
            session_id="session",
            tools=[],
            model="test-model",
            message_history=[],
            deferred_tool_results=cast(DeferredToolResults, object()),
        )

    assert snapshot.close_count == 1
