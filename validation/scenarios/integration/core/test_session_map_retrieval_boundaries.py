"""Verified transcript windows respect fork ancestry and bounded fragments."""

import json
import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_map_boundary_inputs import (
    add_turn,
    add_window,
)
from validation.scenarios.support.session_map_retrieval_inputs import (
    seed_retrieval,
)

pytestmark = pytest.mark.scenario


async def test_nested_fork_retrieval_rejects_parent_future(app: Application) -> None:
    vault = app.vault("RetrievalLineageVault")
    await app.start()
    from core.memory.session_map.retained_evidence import (
        project_retrieved_session_map_evidence,
    )
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    source = "source"
    seed_retrieval(store, vault.name, source)
    child = "child"
    store.fork_session(
        source_session_id=source,
        new_session_id=child,
        vault_name=vault.name,
        through_sequence_index=5,
        title="Child",
    )
    text = "A matching message beyond the inherited prefix."
    add_turn(store, vault.name, source, text, "Parent-only turn.")
    add_turn(store, vault.name, child, text, "Independent child turn.")
    add_window(
        store,
        vault.name,
        child,
        source,
        [{"sequence_index": 6, "role": "user", "content": text}],
        "parent-future",
    )
    child_rows = store.get_stored_messages(child, vault.name, mode="raw")
    retrieved = project_retrieved_session_map_evidence(
        store=store, session_id=child, vault_name=vault.name, retained=child_rows[2:]
    )
    assert [item.sequence_index for item in retrieved.messages] == [0]
    nested = "nested"
    store.fork_session(
        source_session_id=child,
        new_session_id=nested,
        vault_name=vault.name,
        through_sequence_index=11,
        title="Nested",
    )
    nested_rows = store.get_stored_messages(nested, vault.name, mode="raw")
    nested_result = project_retrieved_session_map_evidence(
        store=store, session_id=nested, vault_name=vault.name, retained=nested_rows[2:]
    )
    assert [item.sequence_index for item in nested_result.messages] == [0]


async def test_retrieval_fragment_dedup_budget_and_unrelated_corruption(
    app: Application,
) -> None:
    vault = app.vault("RetrievalFragmentsVault")
    await app.start()
    from core.memory.session_map.authoring import build_session_map_authoring_prompt
    from core.memory.session_map.evidence import build_session_map_evidence
    from core.memory.session_map.models import SessionMapDraft
    from core.memory.session_map.retained_evidence import (
        project_retrieved_session_map_evidence,
    )
    from core.runtime.state import get_runtime_context
    from core.utils.tokens import estimate_token_count

    store = get_runtime_context().chat_store
    session = "fragments"
    store.ensure_session(session, vault.name, owner_principal_id="local-user")
    add_turn(
        store, vault.name, session, "Unrelated archival message.", "Archived answer."
    )
    prefix = "unselected prefix detail. " * 10000
    selected = "The selected canonical fragment."
    source = prefix + selected + " unselected suffix detail." * 10000
    add_turn(store, vault.name, session, source, "Long source recorded.")
    start, end = len(prefix), len(prefix) + len(selected)
    item = {
        "sequence_index": 2,
        "role": "user",
        "content": selected,
        "content_start": start,
        "content_end": end,
        "content_complete": False,
    }
    add_window(store, vault.name, session, session, [item], "fragment-1")
    retained = store.get_stored_messages(session, vault.name, mode="raw")[4:]
    verified = project_retrieved_session_map_evidence(
        store=store, session_id=session, vault_name=vault.name, retained=retained
    )
    assert len(verified.messages) == 1
    fragment = verified.messages[0]
    assert fragment.content_text == selected and (
        fragment.content_start,
        fragment.content_end,
        fragment.content_complete,
    ) == (start, end, False)
    with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
        conn.execute(
            "UPDATE chat_messages SET message_json=? WHERE session_id=? AND vault_name=? AND sequence_index=0",
            ("{", session, vault.name),
        )
    assert (
        project_retrieved_session_map_evidence(
            store=store, session_id=session, vault_name=vault.name, retained=retained
        )
        == verified
    )
    oversized = {
        "sequence_index": 2,
        "role": "user",
        "content": source[:200000],
        "content_start": 0,
        "content_end": 200000,
        "content_complete": False,
    }
    forged = {**item, "content": "X" * len(selected)}
    add_window(
        store, vault.name, session, session, [item, forged, oversized], "fragment-2"
    )
    later = store.get_stored_messages_range(
        session,
        vault.name,
        after_sequence_index=3,
        through_sequence_index=store.get_highest_message_sequence_index(
            session, vault.name
        ),
    )
    bounded = project_retrieved_session_map_evidence(
        store=store, session_id=session, vault_name=vault.name, retained=later
    )
    assert (
        len(bounded.messages) == 2
        and bounded.messages[0] == fragment
        and bounded.truncated is True
    )
    assert (
        bounded.messages[1].content_start == 0
        and bounded.messages[1].content_end < 200000
        and not bounded.messages[1].content_complete
    )
    canonical = store.get_stored_messages_range(
        session, vault.name, after_sequence_index=1, through_sequence_index=2
    )
    envelope = build_session_map_evidence(
        session_id=session,
        vault_name=vault.name,
        history_revision=store.get_session_history_revision(session, vault.name),
        stored_messages=canonical,
        model_messages=[row.message for row in canonical],
    )
    payload = json.loads(
        build_session_map_authoring_prompt(
            previous_map=SessionMapDraft(),
            new_evidence=(envelope,),
            retrieved_evidence=bounded.messages,
            retrieved_evidence_truncated=bounded.truncated,
        )
    )
    assert payload["retrieved_evidence_truncated"] is True
    assert (
        payload["retrieved_canonical_evidence"][0]["content_start"],
        payload["retrieved_canonical_evidence"][0]["content_end"],
        payload["retrieved_canonical_evidence"][0]["content_complete"],
    ) == (start, end, False)
    assert (
        estimate_token_count(
            json.dumps(
                {
                    "retrieved_canonical_evidence": payload[
                        "retrieved_canonical_evidence"
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        <= 8000
    )
