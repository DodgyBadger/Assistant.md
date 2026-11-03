"""Canonical history and caller inputs for real session-ops boundary journeys."""

from __future__ import annotations

from types import SimpleNamespace


def seed_boundaries(store, vault):
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    for session, owner, workspace in (
        ("controller", "local-user", "Projects/WorkspaceA"),
        ("source", "local-user", "Projects/WorkspaceA"),
        ("sibling", "local-user", "Projects/WorkspaceB"),
        ("foreign", "another-user", "Projects/WorkspaceA"),
        ("long", "local-user", "Projects/WorkspaceA"),
    ):
        store.ensure_session(session, vault, owner_principal_id=owner)
        store.set_session_workspace(
            session_id=session, vault_name=vault, workspace_path=workspace
        )
    for session in ("source", "sibling", "foreign"):
        store.add_messages(
            session,
            vault,
            [
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content="Wetland evidence for the selected workspace."
                        )
                    ]
                )
            ],
        )
    store.set_session_title("source", vault, "oversized title " * 100)
    text = "Evidence start " + "granite detail " * 420 + "evidence end"
    store.add_messages(
        "long", vault, [ModelRequest(parts=[UserPromptPart(content=text)])]
    )
    return text


def tool_context(vault):
    return SimpleNamespace(
        deps=SimpleNamespace(vault_name=vault, session_id="controller"),
        run_id="boundary-run",
        tool_call_id="boundary-call",
    )
