"""Manual compaction retains canonical messages, tool boundaries and forks."""

import json

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.compaction_journeys import (
    author_payload,
    compaction_provider,
    configure_compaction,
)

pytestmark = pytest.mark.scenario


async def test_manual_compaction_and_fork_preserve_history(app: Application) -> None:
    vault = app.vault("ChatHistoryCompactionVault")
    app.evidence.register_secret("raw-secret-sentinel")
    with compaction_provider() as server:
        await app.start()
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            TextPart,
            ToolCallPart,
            ToolReturnPart,
            UserPromptPart,
        )

        from core.constants import CHAT_HISTORY_RECOVERY_CARD_PREAMBLE
        from core.runtime.state import get_runtime_context

        configure_compaction(app, server)
        assert (
            app.request(
                "/api/system/settings/general/persist_model_reasoning_parts",
                method="PUT",
                data={"value": "true"},
            ).status_code
            == 200
        )
        session = "chat_history_compaction_session"
        store = get_runtime_context().chat_store
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        messages = [
            ModelRequest(parts=[UserPromptPart(content="First user decision.")]),
            ModelResponse(parts=[TextPart(content="First assistant answer.")]),
            ModelRequest(parts=[UserPromptPart(content="Please use the probe tool.")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="probe",
                        args={},
                        tool_call_id="probe-1",
                        id="provider-probe-item",
                        provider_name="openai",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="probe",
                        content="probe result raw-secret-sentinel",
                        tool_call_id="probe-1",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="Probe result handled.")]),
        ]
        store.add_messages(session, vault.name, messages)
        assert (
            store.get_stored_messages(session, vault.name, mode="raw")[3].message
            == messages[3]
        )
        assert store.get_history(session, vault.name)[3].parts[0].id is None
        for kind in ("call", "result"):
            store.add_tool_event(
                session_id=session,
                vault_name=vault.name,
                tool_call_id="probe-1",
                tool_name="probe",
                event_type=kind,
                args={} if kind == "call" else None,
                result_text=(
                    "probe result raw-secret-sentinel" if kind == "result" else None
                ),
                result_metadata={"status": "completed"} if kind == "result" else None,
            )
        status = app.request(
            f"/api/chat/sessions/{session}/compaction-status",
            params={"vault_name": vault.name},
        )
        assert (
            status.status_code == 200
            and status.json()["messages_before"] == 6
            and status.json()["recommended"] is True
        )
        assert "export_recommended" not in status.json()
        response = await app.request_async(
            f"/api/chat/sessions/{session}/compact",
            method="POST",
            data={
                "vault_name": vault.name,
                "focus": "Keep decisions and tool outcomes.",
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert (
            result["messages_before"],
            result["messages_after"],
            result["kept_recent"],
        ) == (6, 5, 4)
        assert {"export_recommended", "export_created", "export_path"}.isdisjoint(
            result
        )
        payload = author_payload(server, 0)
        assert payload["prompt_contract_version"] == "recovery-card-v5"
        assert "probe result" not in json.dumps(payload["older_history"])
        assert "probe result" in json.dumps(payload["retained_recent_history"])
        assert store.get_message_count(session, vault.name, mode="raw") == 6
        effective = store.get_stored_messages(session, vault.name)
        assert (
            len(effective) == 5
            and [item.message for item in effective[1:]] == messages[2:]
        )
        assert (
            effective[0].role == "system"
            and "AssistantMD compacted chat history" in effective[0].content_text
        )
        assert effective[0].content_text.count(CHAT_HISTORY_RECOVERY_CARD_PREAMBLE) == 1
        assert store.get_history(session, vault.name)[2].parts[0].id is None
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        rows = detail.json()["messages"]
        assert len(rows) == 5 and rows[0]["role"] == "system"
        assert (
            rows[2]["is_tool_message"]
            and rows[2]["tool_call_ids"] == ["probe-1"]
            and rows[2]["content"] == ""
        )
        assert (
            rows[3]["is_tool_message"]
            and rows[3]["tool_return_ids"] == ["probe-1"]
            and rows[3]["content"] == ""
        )
        assert [row["fork_sequence_index"] for row in rows] == [None, 2, None, 4, 5]
        fork = app.request(
            f"/api/chat/sessions/{session}/fork",
            method="POST",
            data={
                "vault_name": vault.name,
                "through_sequence_index": rows[-1]["fork_sequence_index"],
            },
        )
        assert fork.status_code == 200 and fork.json()["copied_message_count"] == 6
        assert fork.json()["session"]["context_strategy"] == "recovery_card"
        child = fork.json()["session"]["session_id"]
        assert (
            store.get_message_count(child, vault.name, mode="raw") == 6
            and len(store.get_tool_events(child, vault.name)) == 2
        )
        checkpoint = store.get_latest_context_checkpoint(child, vault.name)
        assert checkpoint is not None and checkpoint.checkpoint_kind == "recovery_card"
        assert store.get_session_metadata(child, vault.name)["history_revision"] == 1
        assert (
            store.get_session_metadata(child, vault.name)["last_compaction"][
                "compaction_id"
            ]
            == checkpoint.checkpoint_id
        )
        child_detail = app.request(
            f"/api/chat/sessions/{child}", params={"vault_name": vault.name}
        ).json()["messages"]
        assert (
            len(child_detail) == 5
            and child_detail[0]["role"] == "system"
            and CHAT_HISTORY_RECOVERY_CARD_PREAMBLE in child_detail[0]["content"]
        )
        assert child_detail[-1]["content"] == "Probe result handled."
        metadata = store.get_session_metadata(session, vault.name)["last_compaction"]
        assert (
            metadata["prompt_contract_version"] == "recovery-card-v5"
            and metadata["trigger"] == "manual"
            and metadata["reason"] == "api_requested"
            and metadata["compaction_retained_turns"] == 1
        )
        parent_checkpoint = store.get_latest_compaction_checkpoint(session, vault.name)
        assert parent_checkpoint is not None
        assert parent_checkpoint.checkpoint_kind == "recovery_card"
        assert parent_checkpoint.last_message_sequence_index == 5
        checkpoint_metadata = json.loads(parent_checkpoint.metadata_json or "{}")
        assert (
            checkpoint_metadata["prompt_contract_version"],
            checkpoint_metadata["trigger"],
            checkpoint_metadata["reason"],
        ) == ("recovery-card-v5", "manual", "api_requested")
        app.evidence.save_json(
            "manual-compaction-author-requests.json", server.requests
        )
