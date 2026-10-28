"""Explicit discovery input records, not evidence of map generation."""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart


def seed_discovery_sessions(store, vault_name):
    """Seed canonical transcripts, map revisions and a foreign-owned session."""
    store.ensure_session("short", vault_name, owner_principal_id="local-user")
    store.set_session_title("short", vault_name, "Albatross research")
    store.add_messages("short", vault_name, [_user("Looking for otter habitats.")])
    store.set_session_workspace(
        session_id="short",
        vault_name=vault_name,
        workspace_path="Projects/Birds",
    )
    store.ensure_session("long", vault_name, owner_principal_id="local-user")
    store.set_session_workspace(
        session_id="long", vault_name=vault_name, workspace_path="Projects/City"
    )
    store.add_messages(
        "long",
        vault_name,
        [
            _user("Explore redevelopment alternatives."),
            ModelResponse(
                parts=[TextPart("We are considering several possibilities.")]
            ),
            _user("Compare the current proposal."),
            ModelResponse(parts=[TextPart("We have not decided yet.")]),
            _user("New tail topic: kestrel habitat."),
        ],
    )
    add_discovery_checkpoint(
        store, vault_name, "old-map", 1, "Quokka feasibility review"
    )
    add_discovery_checkpoint(
        store, vault_name, "new-map", 3, "Current proposal remains exploratory"
    )
    store.ensure_session("hidden", vault_name, owner_principal_id="another-user")
    store.set_session_title("hidden", vault_name, "Albatross private")
    store.add_messages("hidden", vault_name, [_user("Private otter study.")])


def add_discovery_checkpoint(store, vault_name, checkpoint_id, through, text):
    from core.memory.session_map.checkpoints import build_session_map_context_message
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapEntry,
        SessionMapTrajectory,
        SourceRange,
    )

    draft = SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text=text, sources=(SourceRange(start=0, end=through),)
        ),
        entries=(
            SessionMapEntry(
                id="proposal_exploration",
                kind="option",
                state="active",
                basis="user_established",
                text=text,
                sources=(SourceRange(start=0, end=through),),
            ),
        ),
    )
    message = build_session_map_context_message(draft)
    store.add_context_checkpoint(
        session_id="long",
        vault_name=vault_name,
        checkpoint_id=checkpoint_id,
        checkpoint_kind="session_map",
        source="validation",
        message_count_before=5,
        last_message_sequence_index=through,
        summary_message=message,
        replacement_history=[message],
        metadata={
            "map": draft.model_dump(mode="json"),
            "map_observed_through_sequence_index": through,
        },
    )


def _user(text):
    return ModelRequest(parts=[UserPromptPart(content=text)])
