"""Manual compaction refuses incomplete or mismatched tool histories."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_manual_compaction_rejects_invalid_tool_replies_without_changing_history(
    app: Application,
) -> None:
    vault = app.vault("ManualCompactionToolIntegrityVault")
    await app.start()
    from core.chat.compaction import compact_chat_context, get_compaction_status
    from core.identity import (
        LOCAL_USER_AUTHORITY,
        LOCAL_USER_PRINCIPAL_ID,
        use_execution_authority,
    )
    from core.runtime.state import get_runtime_context
    from validation.support.tool_history_fixtures import tool_reply_cases

    for key, value in (
        ("compaction_strategy", "session_map"),
        ("compaction_author_model", "test"),
        ("compaction_author_thinking", "low"),
        ("compaction_low_watermark_tokens", "50"),
        ("compaction_retained_turns", "1"),
        ("compaction_high_watermark_tokens", "100000"),
        ("compaction_type", "suggested"),
    ):
        assert (
            app.request(
                f"/api/system/settings/general/{key}", "PUT", {"value": value}
            ).status_code
            == 200
        )
    store = get_runtime_context().chat_store
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        for case in tool_reply_cases():
            session = f"manual-tool-reply-{case.name}"
            store.ensure_session(
                session, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
            )
            store.add_messages(session, vault.name, case.messages)
            status = await get_compaction_status(
                session_id=session, vault_name=vault.name, store=store
            )
            assert status.manual_compaction_available is (
                not case.issue_codes
            ), case.name
            before = store.get_history(session, vault.name, mode="raw")
            if case.issue_codes:
                with pytest.raises(ValueError, match="invalid_tool_history"):
                    await compact_chat_context(
                        session_id=session,
                        vault_name=vault.name,
                        authority=LOCAL_USER_AUTHORITY,
                        store=store,
                    )
                assert store.get_history(session, vault.name, mode="raw") == before
