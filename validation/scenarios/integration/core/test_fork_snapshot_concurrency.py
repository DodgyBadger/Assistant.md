"""Forking and an atomic independent rewrite preserve coherent context snapshots."""

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.fork_snapshot_concurrency import (
    concurrent_fork_and_rewrite,
    map_context,
)
from validation.scenarios.support.session_map_journey_inputs import (
    conversation,
    seed_session,
)

pytestmark = pytest.mark.scenario


async def test_fork_serializes_independent_canonical_and_checkpoint_rewrite(
    app: Application,
):
    vault = app.vault("ConcurrentForkSnapshot")
    await app.start()
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    source, child = "coherent-fork-source", "coherent-first-child"
    original = conversation(
        [("Original canonical question.", "Original canonical answer.")]
    )
    replacement = conversation(
        [("Replacement canonical question.", "Replacement canonical answer.")]
    )
    seed_session(store, source, vault.name, original)
    draft, context = map_context(
        "The original canonical question and answer are current."
    )
    store.add_context_checkpoint(
        session_id=source,
        vault_name=vault.name,
        checkpoint_id="original-snapshot-map",
        checkpoint_kind="session_map",
        source="validation",
        message_count_before=2,
        last_message_sequence_index=1,
        summary_message=context,
        replacement_history=[context],
        replacement_source_sequence_indexes=[None],
        metadata={
            "map": draft.model_dump(mode="json"),
            "map_observed_through_sequence_index": 1,
        },
    )
    copied, waited, rewritten_context = await asyncio.to_thread(
        concurrent_fork_and_rewrite,
        app.root / "system",
        store,
        vault.name,
        source,
        child,
        replacement,
    )
    assert copied == 2
    assert (
        waited > 0.01
    ), "The independent writer must contend with the real fork transaction"
    assert store.get_history(child, vault.name, mode="raw") == original
    assert store.get_history(child, vault.name) == [context]
    assert store.get_history(source, vault.name, mode="raw") == replacement
    assert store.get_history(source, vault.name) == [rewritten_context]
    second = "coherent-second-child"
    store.fork_session(
        source_session_id=source,
        new_session_id=second,
        vault_name=vault.name,
        through_sequence_index=1,
        title="After rewrite commits",
    )
    assert store.get_history(second, vault.name, mode="raw") == replacement
    assert store.get_history(second, vault.name) == [rewritten_context]
    assert store.get_history(child, vault.name, mode="raw") == original
    assert store.get_history(child, vault.name) == [context]
    app.evidence.save_json(
        "fork-snapshot-contention.json",
        {
            "copied_messages": copied,
            "writer_wait_seconds": waited,
            "original_child": child,
            "rewritten_child": second,
        },
    )
