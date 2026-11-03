"""Source-linked drafts and recursive transcript-window inputs."""


def draft(*sources):
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapEntry,
        SessionMapTrajectory,
    )

    return SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="A supported historical finding guides current work.", sources=sources
        ),
        entries=(
            SessionMapEntry(
                id="supported_finding",
                kind="finding",
                state="active",
                basis="tool_observed",
                text="Supported historical evidence.",
                sources=sources,
            ),
        ),
    )


def append_recursive_windows(store, vault, session, raw):
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        NativeToolReturnPart,
        ToolReturnPart,
    )

    def window(index, row):
        return {
            "operation": "get_transcript_window",
            "status": "ok",
            "session_id": session,
            "vault_name": vault,
            "messages": [
                {"sequence_index": index, "role": row.role, "content": row.content_text}
            ],
        }

    store.add_messages(
        session,
        vault,
        [
            ModelResponse(
                parts=[
                    NativeToolReturnPart(
                        tool_name="session_ops", content=window(0, raw[0])
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="session_ops",
                        tool_call_id="recursive",
                        content=window(4, raw[4]),
                    )
                ]
            ),
        ],
    )
