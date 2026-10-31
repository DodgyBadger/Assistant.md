"""Native canonical transcripts and legacy recovery checkpoints for map journeys."""

from __future__ import annotations


def conversation(pairs: list[tuple[str, str]]):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    return [
        message
        for user, assistant in pairs
        for message in (
            ModelRequest(parts=[UserPromptPart(user)]),
            ModelResponse(parts=[TextPart(assistant)]),
        )
    ]


def seed_session(store, session: str, vault_name: str, messages) -> None:
    from core.identity import LOCAL_USER_PRINCIPAL_ID

    store.ensure_session(
        session, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    store.add_messages(session, vault_name, messages)


def add_recovery_checkpoint(
    store, session: str, vault_name: str, checkpoint: str, boundary: int
) -> None:
    from core.chat.compaction import build_compaction_summary_message

    summary = build_compaction_summary_message(
        f"Existing recovery state through message {boundary}."
    )
    store.add_compaction_checkpoint(
        session_id=session,
        vault_name=vault_name,
        checkpoint_id=checkpoint,
        source="validation",
        message_count_before=boundary + 1,
        last_message_sequence_index=boundary,
        summary_message=summary,
        replacement_history=[summary],
    )


def range_evidence(store, session: str, vault_name: str, start: int, end: int):
    from core.chat.compaction import build_canonical_evidence_range

    revision = store.get_session_history_revision(session, vault_name)
    result = build_canonical_evidence_range(
        store=store,
        session_id=session,
        vault_name=vault_name,
        source_start_sequence_index=start,
        source_end_sequence_index=end,
        history_revision=revision,
    )
    assert result.status == "resolved"
    return revision, result.envelopes
