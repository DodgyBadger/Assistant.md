"""Validate the Gmail attachment service and vault-writing tool boundary."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-gmail-download-")
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    system_root = direct_root / "system"
    data_root.mkdir()
    system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=system_root)

from core.authoring.shared.tool_binding import _wrap_tool_function  # noqa: E402
from core.connections import (  # noqa: E402
    BuiltInConnectionService,
    GmailPreferences,
    GoogleConnectionCreate,
)
from core.identity import ExecutionAuthority, use_execution_authority  # noqa: E402
from core.integrations.google.gmail import GmailAttachment, GmailError  # noqa: E402
from core.integrations.google.gmail_service import (  # noqa: E402
    GmailAttachmentDownload,
    GmailResourceService,
)
from core.tools.base import (  # noqa: E402
    ToolRecoveryPolicy,
    recovery_policy_from_tool_metadata,
)
from core.tools.gmail import Gmail  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402

_PDF = b"%PDF-1.7\nuntrusted attachment"
_ATTACHMENT = GmailAttachment(
    attachment_id="attachment-1",
    filename="provider-controlled.pdf",
    media_type="application/pdf",
    declared_size=len(_PDF),
    message_id="message-1",
)


class GmailAttachmentToolScenario(BaseScenario):
    """Prove authorization selection, format gates, and safe vault creation."""

    async def test_scenario(self) -> None:
        authority = ExecutionAuthority("gmail-download-owner")
        client = _AttachmentClient()
        service = GmailResourceService(
            connections=object(),  # type: ignore[arg-type]
            google=object(),  # type: ignore[arg-type]
            oauth=object(),  # type: ignore[arg-type]
        )
        selected = SimpleNamespace(connection_id="work-connection")
        with (
            patch.object(
                service, "_preferences", return_value=(selected, _preferences())
            ),
            patch.object(service, "_client", return_value=client) as client_factory,
        ):
            downloaded = await service.download_attachment(
                authority,
                "message-1",
                "attachment-1",
                connection="work",
            )
        self.soft_assert_equal(
            downloaded.content,
            _PDF,
            "The service should return bounded bytes with their selected-message descriptor",
        )
        client_factory.assert_called_once_with(authority, "work-connection")

        await self._assert_service_rejections(service, authority)
        await self._assert_connection_policy_selection(authority)
        await self._assert_tool_contract(authority)
        self.soft_assert_equal(
            Gmail.get_recovery_policy(),
            ToolRecoveryPolicy.MANUAL_REQUIRED,
            "The Gmail tool should require inspection after unresolved remote mutations",
        )
        bound = _wrap_tool_function(
            Gmail.get_tool(vault_path="unused"),
            tool_name="gmail",
            recovery_policy=Gmail.get_recovery_policy(),
            requires_approval=None,
        )
        self.soft_assert_equal(
            recovery_policy_from_tool_metadata(bound.metadata),
            ToolRecoveryPolicy.MANUAL_REQUIRED,
            "The bound Gmail tool should carry manual recovery metadata",
        )

        self.assert_no_failures()
        self.teardown_scenario()

    async def _assert_connection_policy_selection(
        self, authority: ExecutionAuthority
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="gmail-policy-") as root:
            connections = BuiltInConnectionService(system_root=root)
            enabled = connections.create_google_connection_for_authority(
                authority,
                GoogleConnectionCreate(
                    display_name="Enabled",
                    client_id="enabled-client",
                    is_default=True,
                    gmail=GmailPreferences(
                        attachment_download_enabled=True, attachment_max_mb=7
                    ),
                ),
            )
            disabled = connections.create_google_connection_for_authority(
                authority,
                GoogleConnectionCreate(
                    display_name="Disabled",
                    client_id="disabled-client",
                ),
            )
            service = GmailResourceService(
                connections=connections,
                google=_AvailableGoogle(),  # type: ignore[arg-type]
                oauth=object(),  # type: ignore[arg-type]
            )
            client = _AttachmentClient()
            with patch.object(service, "_client", return_value=client) as factory:
                await service.download_attachment(
                    authority, "message-1", "attachment-1"
                )
                factory.assert_called_once_with(authority, enabled.connection_id)
                factory.reset_mock()
                try:
                    await service.download_attachment(
                        authority,
                        "message-1",
                        "attachment-1",
                        connection=disabled.slug,
                    )
                except ValueError as exc:
                    self.soft_assert(
                        "disabled" in str(exc).lower(),
                        "Disabled selected connection should reject download",
                    )
                else:
                    self.soft_assert(
                        False, "Disabled selected connection should reject"
                    )
                factory.assert_not_called()
            status = service.status(authority)
            self.soft_assert_equal(
                (status["attachment_download_enabled"], status["attachment_max_mb"]),
                (True, 7),
                "Gmail status should disclose the selected connection attachment policy",
            )

    async def _assert_service_rejections(
        self, service: GmailResourceService, authority: ExecutionAuthority
    ) -> None:
        selected = SimpleNamespace(connection_id="work-connection")
        for enabled, expected in ((False, "disabled"),):
            client = _AttachmentClient()
            with (
                patch.object(
                    service,
                    "_preferences",
                    return_value=(
                        selected,
                        _preferences(enabled=enabled),
                    ),
                ),
                patch.object(service, "_client", return_value=client),
            ):
                try:
                    await service.download_attachment(
                        authority, "message-1", "attachment-1", connection="work"
                    )
                except (ValueError, GmailError) as exc:
                    self.soft_assert(
                        expected.lower() in str(exc).lower(),
                        f"Service rejection should explain {expected}",
                    )
                else:
                    self.soft_assert(False, f"Service should reject {expected}")

        invalid_pdf_client = _AttachmentClient(content=b"not a pdf")
        with (
            patch.object(
                service, "_preferences", return_value=(selected, _preferences())
            ),
            patch.object(service, "_client", return_value=invalid_pdf_client),
        ):
            try:
                await service.download_attachment(
                    authority, "message-1", "attachment-1", connection="work"
                )
            except GmailError as exc:
                self.soft_assert(
                    "valid PDF" in str(exc),
                    "Service should reject content without a PDF signature",
                )
            else:
                self.soft_assert(False, "Service should reject a false PDF payload")

    async def _assert_tool_contract(self, authority: ExecutionAuthority) -> None:
        fake_service = _AttachmentService()
        runtime = SimpleNamespace(gmail=fake_service)
        with tempfile.TemporaryDirectory(prefix="gmail-tool-vault-") as root:
            tool = Gmail.get_tool(vault_path=root)
            with (
                use_execution_authority(authority),
                patch("core.tools.gmail.get_runtime_context", return_value=runtime),
            ):
                first, second = await asyncio.gather(
                    tool.function(
                        operation="download_attachment",
                        message_id="message-1",
                        attachment_id="attachment-1",
                        connection="work",
                        destination_path="Inbox/report.pdf",
                    ),
                    tool.function(
                        operation="download_attachment",
                        message_id="message-1",
                        attachment_id="attachment-1",
                        connection="work",
                        destination_path="Inbox/report.pdf",
                    ),
                )
                payloads = [json.loads(first), json.loads(second)]
                self.soft_assert_equal(
                    sorted(item["path"] for item in payloads),
                    ["Inbox/report (1).pdf", "Inbox/report.pdf"],
                    "Concurrent requests should create distinct numbered paths",
                )
                self.soft_assert(
                    all(
                        "content" not in item and str(_PDF) not in json.dumps(item)
                        for item in payloads
                    ),
                    "Tool results must not expose attachment bytes",
                )
                self.soft_assert_equal(
                    fake_service.calls,
                    [("message-1", "attachment-1", "work")] * 2,
                    "The tool should preserve the selected account and Gmail handles",
                )
                for destination in (
                    "../escape.pdf",
                    "/absolute.pdf",
                    "Inbox/file.docx",
                ):
                    result = await tool.function(
                        operation="download_attachment",
                        message_id="message-1",
                        attachment_id="attachment-1",
                        destination_path=destination,
                    )
                    self.soft_assert_equal(
                        result.metadata.get("status"),
                        "failed",
                        f"Tool should reject {destination} as a settled result",
                    )
                self.soft_assert_equal(
                    len(fake_service.calls),
                    2,
                    "Invalid vault paths should be rejected before Gmail download",
                )

            vaultless = Gmail.get_tool(vault_path=None)
            with (
                use_execution_authority(authority),
                patch("core.tools.gmail.get_runtime_context", return_value=runtime),
            ):
                result = await vaultless.function(
                    operation="download_attachment",
                    message_id="message-1",
                    attachment_id="attachment-1",
                    destination_path="report.pdf",
                )
                self.soft_assert(
                    "vault is required" in str(result.return_value).lower(),
                    "Vaultless downloads should fail clearly",
                )


class _AttachmentClient:
    def __init__(
        self,
        *,
        attachment: GmailAttachment | None = _ATTACHMENT,
        content: bytes = _PDF,
    ) -> None:
        self.attachment = attachment
        self.content = content

    async def find_attachment(
        self, _message_id: str, _attachment_id: str
    ) -> GmailAttachment | None:
        return self.attachment

    async def download_attachment(
        self, _message_id: str, _attachment_id: str, *, max_bytes: int
    ) -> bytes:
        if len(self.content) > max_bytes:
            raise GmailError("too large", category="attachment_too_large")
        return self.content


def _preferences(*, enabled: bool = True, max_bytes: int = 1024) -> SimpleNamespace:
    return SimpleNamespace(
        attachment_download_enabled=enabled,
        attachment_max_mb=max(1, (max_bytes + 1024 * 1024 - 1) // (1024 * 1024)),
    )


class _AttachmentService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def download_attachment(
        self,
        _authority: ExecutionAuthority,
        message_id: str,
        attachment_id: str,
        *,
        connection: str | None = None,
    ) -> GmailAttachmentDownload:
        self.calls.append((message_id, attachment_id, connection))
        return GmailAttachmentDownload(attachment=_ATTACHMENT, content=_PDF)


class _AvailableGoogle:
    def capability_availability(self, *_args: object) -> SimpleNamespace:
        return SimpleNamespace(
            available=True, connection_state="ready", missing_scopes=()
        )

    def status(self, *_args: object) -> SimpleNamespace:
        return SimpleNamespace(account_email="owner@example.com")


if __name__ == "__main__":
    asyncio.run(GmailAttachmentToolScenario().test_scenario())
