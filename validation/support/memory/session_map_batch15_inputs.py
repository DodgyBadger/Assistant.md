"""Explicit source-linked authoring and mixed retrieval message inputs."""


def author_request(vault_name):
    from core.memory.session_map.evidence import (
        SessionMapEvidence,
        SessionMapMessageEvidence,
    )
    from core.memory.session_map.models import SessionMapDraft, SourceRange
    from core.memory.session_map.service import SessionMapAuthoringRequest

    session = "session-map-task"
    envelope = SessionMapEvidence(
        evidence_id="session-map-task-envelope",
        session_id=session,
        vault_name=vault_name,
        history_revision=2,
        source_start_sequence_index=10,
        source_end_sequence_index=11,
        message_count=2,
        estimated_tokens=100,
        projected_text="[source:10] USER:\nPlease review this with counsel.",
        source_digest="session-map-task-digest",
        citable_source_ranges=(SourceRange(start=10, end=11),),
    )
    return SessionMapAuthoringRequest(
        session_id=session,
        vault_name=vault_name,
        model_alias="session-map-author",
        previous_map=SessionMapDraft(),
        new_evidence=(envelope,),
        recent_evidence=(
            SessionMapMessageEvidence(
                sequence_index=12,
                role="user",
                content_text="The legal review is complete.",
            ),
        ),
    )


def seed_mixed_evidence(store, vault_name, session):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        NativeToolReturnPart,
        TextPart,
        ToolReturnPart,
        UserPromptPart,
    )

    retrieval = "An unrelated retrieved recommendation must not become state."

    def returned(call_id):
        return ToolReturnPart(
            tool_name="session_ops", tool_call_id=call_id, content=retrieval
        )

    store.ensure_session(session, vault_name, owner_principal_id="local-user")
    store.add_messages(
        session,
        vault_name,
        [
            ModelRequest(parts=[UserPromptPart(content="Eligible user fact.")]),
            ModelRequest(parts=[returned("pure")]),
            ModelRequest(
                parts=[
                    UserPromptPart(content="Eligible mixed user fact."),
                    returned("mixed-user"),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="vault_ops",
                        tool_call_id="ordinary",
                        content="Eligible observed file.",
                    ),
                    returned("mixed-tool"),
                ]
            ),
            ModelResponse(
                parts=[NativeToolReturnPart(tool_name="session_ops", content=retrieval)]
            ),
            ModelResponse(
                parts=[
                    TextPart(content="Eligible mixed assistant finding."),
                    NativeToolReturnPart(tool_name="session_ops", content=retrieval),
                ]
            ),
        ],
    )
    return retrieval
