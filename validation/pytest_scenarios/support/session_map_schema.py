"""Explicit map/evidence fixtures and real checkpoint persistence actions."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from validation.pytest_scenarios.application import Application


def schema_inputs():
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapEntry,
        SessionMapTrajectory,
        SourceRange,
    )

    envelopes = (
        envelope(
            evidence_id="envelope-a",
            start=10,
            end=13,
        ),
        envelope(
            evidence_id="envelope-b",
            start=14,
            end=17,
        ),
    )
    previous = SessionMapDraft(
        schema_version=1,
        entries=(
            SessionMapEntry(
                id="redevelopment_orientation",
                kind="orientation",
                state="active",
                basis="user_established",
                text="The co-op is evaluating neighbouring redevelopment risks.",
                sources=(SourceRange(start=2, end=5),),
            ),
            SessionMapEntry(
                id="wait_for_documents",
                kind="next_action",
                state="active",
                basis="user_established",
                text="Wait for the written materials before deciding next steps.",
                sources=(SourceRange(start=4, end=5),),
            ),
        ),
    )
    authored = SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="The work moved from initial proposal review into legal review after the City land issue emerged.",
            sources=(
                SourceRange(start=2, end=5),
                SourceRange(start=10, end=14),
            ),
        ),
        entries=(
            SessionMapEntry(
                id="redevelopment_orientation",
                kind="orientation",
                state="active",
                basis="user_established",
                text="The co-op has moved from initial contact into legal review.",
                sources=(
                    SourceRange(start=2, end=5),
                    SourceRange(start=10, end=13),
                ),
            ),
            SessionMapEntry(
                id="wait_for_documents",
                kind="next_action",
                state="superseded",
                basis="user_established",
                text="Waiting for developer documents is no longer the next action.",
                sources=(
                    SourceRange(start=4, end=5),
                    SourceRange(start=10, end=11),
                ),
            ),
            SessionMapEntry(
                id="wait_for_counsel",
                kind="next_action",
                state="active",
                basis="user_established",
                text="Wait for counsel's initial review before further engagement.",
                sources=(SourceRange(start=12, end=13),),
            ),
            SessionMapEntry(
                id="city_owned_land",
                kind="constraint",
                state="active",
                basis="user_established",
                text="The City owns the land beneath the co-op.",
                sources=(SourceRange(start=14, end=14),),
            ),
        ),
    )
    return previous, authored, envelopes


def envelope(
    *,
    evidence_id: str,
    start: int,
    end: int,
):
    from core.memory.session_map.evidence import SessionMapEvidence
    from core.memory.session_map.models import SourceRange

    return SessionMapEvidence(
        evidence_id=evidence_id,
        session_id="session-map-schema",
        vault_name="SessionMapSchemaVault",
        history_revision=1,
        source_start_sequence_index=start,
        source_end_sequence_index=end,
        message_count=end - start + 1,
        estimated_tokens=100,
        projected_text=f"Evidence for {start}-{end}",
        source_digest=f"digest-{start}-{end}",
        citable_source_ranges=(SourceRange(start=start, end=end),),
    )


def persist_schema_maps(
    app: Application, previous, authored, envelopes
) -> dict[str, Any]:
    """Commit seeded drafts; this does not claim they were model generated."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.memory.session_map.checkpoints import (
        commit_session_map_context_checkpoint,
    )
    from core.memory.session_map.models import SessionMapDraft
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    session = "session-map-schema"
    vault = "SessionMapSchemaVault"
    store.ensure_session(session, vault, owner_principal_id="local-user")
    store.add_messages(
        session,
        vault,
        [
            ModelRequest(parts=[UserPromptPart(content=f"Canonical record {i}")])
            for i in range(18)
        ],
    )
    revision = store.get_session_history_revision(session, vault)
    first = replace(
        envelope(evidence_id="initial-prefix", start=0, end=9),
        history_revision=revision,
    )
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        commit_session_map_context_checkpoint(
            store=store,
            session_id=session,
            vault_name=vault,
            draft=previous,
            previous_map=SessionMapDraft(),
            new_evidence=(first,),
            expected_history_revision=revision,
            message_count_before=18,
            source="validation",
            checkpoint_id="schema-initial",
        )
        revision = store.get_session_history_revision(session, vault)
        commit_session_map_context_checkpoint(
            store=store,
            session_id=session,
            vault_name=vault,
            draft=authored,
            previous_map=previous,
            new_evidence=tuple(
                replace(e, history_revision=revision) for e in envelopes
            ),
            expected_history_revision=revision,
            message_count_before=18,
            source="validation",
            checkpoint_id="schema-authored",
        )
    response = app.request(
        f"/api/chat/sessions/{session}/map", params={"vault_name": vault}
    )
    assert response.status_code == 200, response.text
    return response.json()
