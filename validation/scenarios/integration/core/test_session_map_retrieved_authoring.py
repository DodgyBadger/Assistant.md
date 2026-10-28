"""Verified retrieved fragments reach a real governed author and remain citable."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.retrieved_map_author import retrieved_author
from validation.scenarios.support.session_map_author_http import configure_author
from validation.scenarios.support.session_map_boundary_inputs import (
    add_turn,
    add_window,
)

pytestmark = pytest.mark.scenario


async def test_verified_fragment_in_governed_author_request(app: Application) -> None:
    vault = app.vault("MapRetrievedAuthorVault")
    with retrieved_author() as server:
        await app.start()
        from core.identity import LOCAL_USER_AUTHORITY
        from core.memory.session_map.evidence import (
            SessionMapMessageEvidence,
            build_session_map_evidence,
        )
        from core.memory.session_map.models import SessionMapDraft
        from core.memory.session_map.retained_evidence import (
            project_retrieved_session_map_evidence,
        )
        from core.memory.session_map.service import (
            SessionMapAuthoringRequest,
            run_session_map_authoring,
        )
        from core.runtime.state import get_runtime_context

        configure_author(app, server)
        store = get_runtime_context().chat_store
        session = "verified-fragment-author"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        original = "Historical exact evidence remains available."
        fragment = original[11:25]
        add_turn(store, vault.name, session, original, "Historical turn complete.")
        add_window(
            store,
            vault.name,
            session,
            session,
            [
                {
                    "sequence_index": 0,
                    "role": "user",
                    "content": fragment,
                    "content_start": 11,
                    "content_end": 25,
                    "content_complete": False,
                }
            ],
            "window",
        )
        add_turn(
            store,
            vault.name,
            session,
            "Continue using historical evidence.",
            "Current turn complete.",
        )
        raw = store.get_stored_messages(session, vault.name, mode="raw")
        retrieved = project_retrieved_session_map_evidence(
            store=store, session_id=session, vault_name=vault.name, retained=raw[2:6]
        )
        assert (
            len(retrieved.messages) == 1
            and retrieved.messages[0].content_text == fragment
        )
        envelope = build_session_map_evidence(
            session_id=session,
            vault_name=vault.name,
            history_revision=store.get_session_history_revision(session, vault.name),
            stored_messages=raw[2:6],
            model_messages=[row.message for row in raw[2:6]],
        )
        request = SessionMapAuthoringRequest(
            session_id=session,
            vault_name=vault.name,
            model_alias="session-map-author",
            previous_map=SessionMapDraft(),
            new_evidence=(envelope,),
            recent_evidence=(
                SessionMapMessageEvidence(
                    sequence_index=6,
                    role="user",
                    content_text="Continue using historical evidence.",
                ),
                SessionMapMessageEvidence(
                    sequence_index=7,
                    role="assistant",
                    content_text="Current turn complete.",
                ),
            ),
            retrieved_evidence=retrieved.messages,
        )
        result = await run_session_map_authoring(
            request, authority=LOCAL_USER_AUTHORITY
        )
        assert len(server.payloads) == 1
        payload = server.payloads[0]
        assert payload["new_evidence_envelopes"][0]["source_range"] == {
            "start": 2,
            "end": 5,
        }
        row = payload["retrieved_canonical_evidence"][0]
        assert (
            row["sequence_index"],
            row["content"],
            row["content_start"],
            row["content_end"],
            row["content_complete"],
        ) == (0, fragment, 11, 25, False)
        assert result.draft.trajectory is not None
        assert [
            (source.start, source.end) for source in result.draft.trajectory.sources
        ] == [(0, 0), (6, 6)]
        app.evidence.save_json(
            "retrieved-fragment-author-request.json", server.requests
        )
