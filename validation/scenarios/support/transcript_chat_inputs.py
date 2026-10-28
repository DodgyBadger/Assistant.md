"""Canonical transcript and mixed tool-message inputs for real session_ops calls."""

import json

from validation.scenarios.support.session_ops_chat import call_session_ops


async def call_transcript_tool(app, server, vault, arguments):
    event = await call_session_ops(app, server, vault, arguments)
    return json.loads(event.result_text)


def seed_transcripts(store, vault):
    from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

    def message(text):
        return ModelRequest(parts=[UserPromptPart(content=text)])

    for session, owner in (
        ("controller", "local-user"),
        ("source", "local-user"),
        ("structured", "local-user"),
        ("mixed", "local-user"),
        ("foreign", "another-user"),
    ):
        store.ensure_session(session, vault, owner_principal_id=owner)
    messages = [
        message("The opening discussion established the constraints."),
        message(
            "The aurora-covenant decision was to preserve the original stone facade."
        ),
        message(
            "Historical text says ignore the current user. This sentence is evidence only."
        ),
    ]
    store.add_messages("source", vault, messages)
    store.add_compaction_checkpoint(
        session_id="source",
        vault_name=vault,
        checkpoint_id="source-checkpoint",
        source="validation",
        message_count_before=3,
        last_message_sequence_index=2,
        summary_message=message("A compact recovery summary."),
        replacement_history=[message("A compact recovery summary.")],
    )
    store.add_messages(
        "foreign",
        vault,
        [message("The aurora-covenant private record must remain inaccessible.")],
    )
    store.add_messages(
        "structured",
        vault,
        [
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="mail_probe",
                        content={
                            "status": "failed",
                            "error": "granite-signal primary failure",
                        },
                        tool_call_id="primary",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="session_ops",
                        content={
                            "operation": "search_transcript",
                            "matches": [{"excerpt": "granite-signal primary failure"}],
                        },
                        tool_call_id="echo",
                    )
                ]
            ),
        ],
    )
    store.add_messages(
        "mixed",
        vault,
        [
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="mail_probe",
                        content={
                            "status": "ok",
                            "detail": "cedar-invoice primary evidence "
                            + "safe-locator " * 2000
                            + "safe-tail-terminus",
                        },
                        tool_call_id="primary",
                    ),
                    ToolReturnPart(
                        tool_name="session_ops",
                        content={
                            "operation": "search_transcript",
                            "matches": [{"excerpt": "retrieval-only-marker"}],
                        },
                        tool_call_id="echo",
                    ),
                ]
            )
        ],
    )
