"""Validate Gmail draft policy, capability, and tool boundaries."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic_ai.messages import ToolReturn

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-gmail-draft-")
    direct_root = Path(_direct_run_root.name)
    (direct_root / "data").mkdir()
    (direct_root / "system").mkdir()
    set_bootstrap_roots(
        data_root=direct_root / "data", system_root=direct_root / "system"
    )

from core.identity import ExecutionAuthority, use_execution_authority  # noqa: E402
from core.integrations.google.connection import (  # noqa: E402
    GoogleConnectionConfigurationError,
)
from core.integrations.google.gmail import GmailDraft, GmailError  # noqa: E402
from core.integrations.google.gmail_service import (  # noqa: E402
    GmailConfigurationError,
    GmailResourceService,
)
from core.integrations.google.oauth import GoogleOAuthError  # noqa: E402
from core.tools.gmail import Gmail  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class GmailDraftToolScenario(BaseScenario):
    """Prove opt-in, scope, bounds, and sanitized draft results."""

    async def test_scenario(self) -> None:
        authority = ExecutionAuthority("gmail-draft-owner")
        selected = SimpleNamespace(
            connection_id="work-id",
            gmail=SimpleNamespace(
                draft_creation_enabled=True,
                draft_max_characters=20,
            ),
        )
        client = _DraftClient()
        google = _GoogleAvailability(available=True)
        service = GmailResourceService(
            connections=object(),  # type: ignore[arg-type]
            google=google,  # type: ignore[arg-type]
            oauth=object(),  # type: ignore[arg-type]
        )
        with (
            patch.object(service, "_resolve_connection", return_value=selected),
            patch.object(service, "_client", return_value=client),
        ):
            result = await service.create_draft(
                authority,
                subject="Hello",
                body="Draft body",
                connection="work",
            )
        self.soft_assert_equal(
            (result.draft_id, client.calls),
            (
                "draft-1",
                [("Hello", "Draft body")],
            ),
            "The service should create one draft after policy and scope checks",
        )

        await self._assert_gate(authority, selected, enabled=False, scope=True)
        await self._assert_gate(authority, selected, enabled=True, scope=False)
        await self._assert_bounds(authority, service, selected)
        await self._assert_tool(authority)
        await self._assert_missing_message_failure(authority)
        await self._assert_failure_classification(authority)
        self.assert_no_failures()
        self.teardown_scenario()

    async def _assert_gate(
        self,
        authority: ExecutionAuthority,
        selected: SimpleNamespace,
        *,
        enabled: bool,
        scope: bool,
    ) -> None:
        selected.gmail.draft_creation_enabled = enabled
        client = _DraftClient()
        service = GmailResourceService(
            connections=object(),  # type: ignore[arg-type]
            google=_GoogleAvailability(available=scope),  # type: ignore[arg-type]
            oauth=object(),  # type: ignore[arg-type]
        )
        with (
            patch.object(service, "_resolve_connection", return_value=selected),
            patch.object(service, "_client", return_value=client),
        ):
            try:
                await service.create_draft(
                    authority,
                    subject="Hello",
                    body="Body",
                )
            except GmailConfigurationError:
                pass
            else:
                self.soft_assert(False, "Draft creation should enforce its gate")
        self.soft_assert_equal(client.calls, [], "Rejected drafts must not reach Gmail")

    async def _assert_bounds(
        self,
        authority: ExecutionAuthority,
        service: GmailResourceService,
        selected: SimpleNamespace,
    ) -> None:
        selected.gmail.draft_creation_enabled = True
        for body in ("", "x" * 21):
            client = _DraftClient()
            with (
                patch.object(service, "_resolve_connection", return_value=selected),
                patch.object(service, "_client", return_value=client),
            ):
                try:
                    await service.create_draft(authority, subject="Hello", body=body)
                except ValueError:
                    pass
                else:
                    self.soft_assert(False, "Invalid draft bounds should be rejected")
            self.soft_assert_equal(
                client.calls, [], "Bound failures must precede Gmail"
            )

    async def _assert_tool(self, authority: ExecutionAuthority) -> None:
        service = _DraftService()
        tool = Gmail.get_tool()
        with (
            use_execution_authority(authority),
            patch(
                "core.tools.gmail.get_runtime_context",
                return_value=SimpleNamespace(gmail=service),
            ),
        ):
            raw = await tool.function(
                operation="create_draft",
                connection="work",
                subject="Hello",
                body="Draft body",
            )
        payload = json.loads(raw)
        self.soft_assert_equal(
            (payload["draft_id"], payload["message_id"], payload["thread_id"]),
            ("draft-1", "message-1", "thread-1"),
            "The tool should expose draft creation confirmation identifiers",
        )
        self.soft_assert(
            "Draft body" not in raw,
            "Draft content must not be echoed in tool results",
        )
        self.soft_assert(
            "Draft mutation is create-only" in str(tool.description)
            and "search again" in str(tool.description),
            "The tool should forbid draft edits and require current search handles",
        )

    async def _assert_missing_message_failure(
        self, authority: ExecutionAuthority
    ) -> None:
        tool = Gmail.get_tool()
        with (
            use_execution_authority(authority),
            patch(
                "core.tools.gmail.get_runtime_context",
                return_value=SimpleNamespace(gmail=_MissingMessageService()),
            ),
        ):
            result = await tool.function(
                operation="get_message",
                message_id="stale-draft-message",
            )
        self.soft_assert(
            isinstance(result, ToolReturn),
            "A missing Gmail resource should be a settled tool result",
        )
        if not isinstance(result, ToolReturn):
            return
        self.soft_assert_equal(
            result.metadata,
            {
                "status": "failed",
                "error_type": "GmailError",
                "failure_kind": "not_found",
                "retryable": False,
                "phase": "gmail",
                "suggested_action": (
                    "Do not retry the same resource ID. Search Gmail again for a "
                    "current message or thread ID. IDs returned by create_draft are "
                    "not durable follow-up handles. Existing drafts cannot be edited, "
                    "sent, or deleted; create another draft only when the user "
                    "explicitly requests it."
                ),
                "tool_name": "gmail",
                "operation": "get_message",
                "gmail_category": "not_found",
            },
            "A missing Gmail resource should carry actionable failure metadata",
        )
        self.soft_assert(
            "stale-draft-message" not in str(result.return_value),
            "A Gmail failure result should not echo the resource ID",
        )
        self.soft_assert(
            "Do not retry the same resource ID" in str(result.return_value)
            and "Search Gmail again" in str(result.return_value),
            "Gmail recovery guidance should be visible to the model",
        )

    async def _assert_failure_classification(
        self, authority: ExecutionAuthority
    ) -> None:
        cases = (
            (
                GoogleOAuthError("Google authorization must be reconnected."),
                "configuration",
                False,
            ),
            (
                GoogleConnectionConfigurationError(
                    "Stored Google OAuth token state is invalid."
                ),
                "configuration",
                False,
            ),
            (
                GmailError(
                    "Gmail could not be reached after retrying.",
                    category="network",
                    retryable=True,
                ),
                "transient_network",
                True,
            ),
        )
        for error, failure_kind, retryable in cases:
            tool = Gmail.get_tool()
            with (
                use_execution_authority(authority),
                patch(
                    "core.tools.gmail.get_runtime_context",
                    return_value=SimpleNamespace(gmail=_FailingMessageService(error)),
                ),
            ):
                result = await tool.function(
                    operation="get_message",
                    message_id="message-1",
                )
            self.soft_assert(
                isinstance(result, ToolReturn),
                f"{failure_kind} should return a settled tool failure",
            )
            if not isinstance(result, ToolReturn):
                continue
            self.soft_assert_equal(
                (
                    result.metadata.get("failure_kind"),
                    result.metadata.get("retryable"),
                ),
                (failure_kind, retryable),
                f"Gmail should preserve the {failure_kind} taxonomy",
            )

        tool = Gmail.get_tool()
        with (
            use_execution_authority(authority),
            patch(
                "core.tools.gmail.get_runtime_context",
                return_value=SimpleNamespace(
                    gmail=_FailingMessageService(ValueError("unexpected bug"))
                ),
            ),
        ):
            try:
                await tool.function(
                    operation="get_message",
                    message_id="message-1",
                )
            except ValueError as exc:
                self.soft_assert_equal(
                    str(exc),
                    "unexpected bug",
                    "Unexpected ValueError failures should retain their details",
                )
            else:
                self.soft_assert(
                    False,
                    "Unexpected ValueError failures should preserve fail-fast behavior",
                )

        tool = Gmail.get_tool()
        with (
            use_execution_authority(authority),
            patch(
                "core.tools.gmail.get_runtime_context",
                return_value=SimpleNamespace(gmail=None),
            ),
        ):
            locked_result = await tool.function(operation="status")
        self.soft_assert(
            isinstance(locked_result, ToolReturn),
            "Locked encrypted connections should return a settled tool failure",
        )
        if isinstance(locked_result, ToolReturn):
            self.soft_assert_equal(
                locked_result.metadata.get("failure_kind"),
                "configuration",
                "Locked encrypted connections should be a configuration failure",
            )
            self.soft_assert(
                "Unlock encrypted connections" in str(locked_result.return_value),
                "The model should receive the action needed for locked connections",
            )


class _GoogleAvailability:
    def __init__(self, *, available: bool) -> None:
        self.available = available

    def capability_availability(self, *_args: object) -> SimpleNamespace:
        return SimpleNamespace(available=self.available, missing_scopes=())


class _DraftClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def create_draft(
        self,
        *,
        subject: str,
        body: str,
    ) -> GmailDraft:
        self.calls.append((subject, body))
        return GmailDraft("draft-1", "message-1", "thread-1")


class _DraftService:
    async def create_draft(self, *_args: object, **_kwargs: object) -> GmailDraft:
        return GmailDraft("draft-1", "message-1", "thread-1")


class _MissingMessageService:
    async def get_message(self, *_args: object, **_kwargs: object) -> None:
        raise GmailError("Gmail resource was not found.", category="not_found")


class _FailingMessageService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def get_message(self, *_args: object, **_kwargs: object) -> None:
        raise self.error


if __name__ == "__main__":
    asyncio.run(GmailDraftToolScenario().test_scenario())
