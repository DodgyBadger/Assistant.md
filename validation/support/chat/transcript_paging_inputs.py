"""Canonical long-message and retrieval-echo inputs for transcript paging."""


def seed_paging(store, vault):
    from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

    for session in ("paging", "echo-starvation"):
        store.ensure_session(session, vault, owner_principal_id="local-user")
    long_text = "oversized-evidence " + "granite detail " * 1500
    store.add_messages(
        "paging",
        vault,
        [
            ModelRequest(
                parts=[UserPromptPart(content="The solstice-archive code is SA-2049.")]
            ),
            ModelRequest(parts=[UserPromptPart(content=long_text)]),
        ],
    )
    echoes = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="session_ops",
                    content={
                        "operation": "search_transcript",
                        "matches": [{"excerpt": "ember-beacon prior retrieval echo"}],
                    },
                    tool_call_id=f"echo-{index}",
                )
            ]
        )
        for index in range(101)
    ]
    store.add_messages(
        "echo-starvation",
        vault,
        [
            *echoes,
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content="The ember-beacon primary evidence remains searchable."
                    )
                ]
            ),
        ],
    )
    return long_text
