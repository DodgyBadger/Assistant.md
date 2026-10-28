"""Legacy pending evidence remains canonical and eligible for reconciliation."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_map_author_http import (
    author_provider,
    configure_author,
    run_reduction,
)

pytestmark = pytest.mark.scenario


async def test_legacy_pending_evidence_is_canonical_and_ready(app: Application) -> None:
    vault = app.vault("SessionMapPendingEvidenceVault")
    await app.start()
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    from core.chat.compaction import build_canonical_evidence_range
    from core.constants import SESSION_MAP_CONTEXT_PROMPT_VERSION
    from core.identity import (
        LOCAL_USER_AUTHORITY,
        LOCAL_USER_PRINCIPAL_ID,
        use_execution_authority,
    )
    from core.memory.session_map.checkpoints import (
        SessionMapPendingEvidence,
        build_session_map_context_message,
        commit_session_map_context_checkpoint,
        load_session_map_pending_evidence,
    )
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapEntry,
        SourceRange,
    )
    from core.memory.session_map.readiness import (
        evaluate_session_map_compaction_readiness,
    )
    from core.runtime.state import get_runtime_context

    for key, value in (
        ("compaction_strategy", "session_map"),
        ("compaction_author_model", "test"),
        ("compaction_author_thinking", "low"),
        ("compaction_low_watermark_tokens", "1"),
        ("compaction_retained_turns", "1"),
        ("compaction_high_watermark_tokens", "2"),
        ("compaction_type", "auto"),
    ):
        result = app.request(
            f"/api/system/settings/general/{key}", "PUT", {"value": value}
        )
        assert result.status_code == 200, result.text
    store = get_runtime_context().chat_store
    session = "session-map-pending-evidence"
    store.ensure_session(
        session, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    raw = [
        (
            ModelRequest(parts=[UserPromptPart(content=question)])
            if index % 2 == 0
            else ModelResponse(parts=[TextPart(content=question)])
        )
        for index, question in enumerate(
            (
                "Develop the consulting offer.",
                "We will keep the offer practical.",
                "Preserve the legacy pending evidence.",
                "That pending evidence remains relevant.",
                "Create a durable getting-started note.",
                "The getting-started note now exists.",
                "Keep the current launch plan.",
                "The launch plan remains current.",
            )
        )
    ]
    store.add_messages(session, vault.name, raw)
    revision = store.get_session_history_revision(session, vault.name)
    first = build_canonical_evidence_range(
        store=store,
        session_id=session,
        vault_name=vault.name,
        source_start_sequence_index=0,
        source_end_sequence_index=1,
        history_revision=revision,
    )
    assert first.status == "resolved"
    draft = SessionMapDraft(
        schema_version=1,
        entries=(
            SessionMapEntry(
                id="consulting_offer",
                kind="goal",
                state="active",
                basis="mixed",
                text="Develop a practical consulting offer.",
                sources=(SourceRange(start=0, end=1),),
            ),
        ),
    )
    commit_session_map_context_checkpoint(
        store=store,
        session_id=session,
        vault_name=vault.name,
        draft=draft,
        previous_map=SessionMapDraft(),
        new_evidence=first.envelopes,
        expected_history_revision=revision,
        message_count_before=len(raw),
        source="validation",
        authoring_task_id="initial-author-task",
        checkpoint_id="initial-map",
    )
    revision = store.get_session_history_revision(session, vault.name)
    deferred = build_canonical_evidence_range(
        store=store,
        session_id=session,
        vault_name=vault.name,
        source_start_sequence_index=2,
        source_end_sequence_index=3,
        history_revision=revision,
    )
    assert deferred.status == "resolved"
    envelope = deferred.envelopes[0]
    pending = SessionMapPendingEvidence(
        start_sequence_index=envelope.source_start_sequence_index,
        end_sequence_index=envelope.source_end_sequence_index,
        source_digest=envelope.source_digest,
        estimated_tokens=envelope.estimated_tokens,
    )
    context = build_session_map_context_message(draft)
    store.add_context_checkpoint(
        session_id=session,
        vault_name=vault.name,
        checkpoint_id="legacy-deferred-map",
        checkpoint_kind="session_map",
        source="validation",
        message_count_before=7,
        last_message_sequence_index=3,
        summary_message=context,
        replacement_history=[context],
        metadata={
            "checkpoint_kind": "session_map",
            "prompt_contract_version": SESSION_MAP_CONTEXT_PROMPT_VERSION,
            "source_history_revision": revision,
            "consumed_through_sequence_index": 3,
            "map_observed_through_sequence_index": 1,
            "evidence_envelope_ids": [envelope.envelope_id],
            "map": draft.model_dump(mode="json"),
            "pending_evidence": {
                "start_sequence_index": pending.start_sequence_index,
                "end_sequence_index": pending.end_sequence_index,
                "source_digest": pending.source_digest,
                "estimated_tokens": pending.estimated_tokens,
            },
            "classification": {
                "task_id": "legacy-classification",
                "model_alias": "jev",
                "score": 0.22,
                "threshold": 0.5,
                "prompt_contract_version": "session-map-gate-v1",
                "action": "deferred",
            },
        },
        expected_history_revision=revision,
    )
    checkpoint = store.get_latest_context_checkpoint(session, vault.name)
    assert checkpoint is not None
    assert load_session_map_pending_evidence(checkpoint) == pending
    assert "Preserve the legacy pending evidence" not in (
        checkpoint.metadata_json or ""
    )
    assert evaluate_session_map_compaction_readiness(
        store=store, session_id=session, vault_name=vault.name
    ).enabled
    assert store.get_history(session, vault.name, mode="raw") == raw

    from core.runtime.execution_tasks import ExecutionTaskKind

    with author_provider() as server:
        configure_author(app, server)
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            readiness = evaluate_session_map_compaction_readiness(
                store=store, session_id=session, vault_name=vault.name
            )
            assert readiness.enabled
            result = await run_reduction(store, session, vault.name, readiness)
            assert result is not None
        assert len(server.requests) == 1
        assert [
            (item["source_range"]["start"], item["source_range"]["end"])
            for item in server.payloads[0]["new_evidence_envelopes"]
        ] == [(2, 3), (4, 5), (6, 7)]
        latest = store.get_latest_context_checkpoint(session, vault.name)
        assert latest is not None and load_session_map_pending_evidence(latest) is None
        metadata = json.loads(latest.metadata_json or "{}")
        assert "classification" not in metadata and "pending_evidence" not in metadata
        assert store.get_history(session, vault.name, mode="raw") == raw
        assert (store.get_history(session, vault.name) or [])[1:] == []
        tasks = await get_runtime_context().task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        assert [task.status for task in tasks] == ["completed"]
        tasks = await get_runtime_context().task_coordinator.list_tasks()
        assert all(task.kind != "session_map_classification" for task in tasks)
