from __future__ import annotations

import json
import sqlite3
from functools import partial

import pytest
from pydantic import TypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
)

from validation.scenarios.application import Application
from validation.scenarios.support.fork_lineage_inputs import (
    _assistant,
    _map_context,
    _user,
)

pytestmark = pytest.mark.scenario


async def test_checkpoint_origin_validation(app: Application) -> None:
    vault = app.vault("ForkLineageVault")
    await app.start()
    from core.chat.chat_store import (
        ChatHistoryCorruptionError,
        ContextCheckpointKind,
    )
    from core.identity import LOCAL_USER_PRINCIPAL_ID
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    vault_name = vault.name
    database_path = app.root / "system/chat_sessions.db"
    adapter = TypeAdapter(list[ModelMessage])
    canonical_messages = [
        _user("First origin question"),
        _assistant("First origin answer"),
        _user("Second origin question"),
        _assistant("Second origin answer"),
    ]
    context = _map_context("Synthetic checkpoint context")
    valid_replacement = [context, *canonical_messages[2:]]
    invalid_cases = (
        ("boolean", "[null, true, 3]", valid_replacement),
        ("float", "[null, 2.0, 3]", valid_replacement),
        ("negative", "[null, -1, 3]", valid_replacement),
        ("future", "[null, 2, 4]", valid_replacement),
        ("missing", "[null, 2, 3]", valid_replacement),
        (
            "descending",
            "[null, 3, 2]",
            [context, canonical_messages[3], canonical_messages[2]],
        ),
        (
            "duplicate",
            "[null, 2, 2]",
            [context, canonical_messages[2], canonical_messages[2]],
        ),
        (
            "fabricated",
            "[null, 2, 3]",
            [context, canonical_messages[2], _assistant("Fabricated answer")],
        ),
        ("misaligned", "[null, 2]", valid_replacement),
        ("object", "{}", valid_replacement),
        ("invalid-json", "broken", valid_replacement),
    )
    checkpoint_kinds: tuple[ContextCheckpointKind, ...] = (
        "recovery_card",
        "session_map",
    )
    for checkpoint_kind in checkpoint_kinds:
        for label, encoded_origins, replacement in invalid_cases:
            source_id = f"fork-origin-{checkpoint_kind}-{label}"
            checkpoint_id = f"{source_id}-checkpoint"
            store.ensure_session(
                source_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
            )
            store.add_messages(source_id, vault_name, canonical_messages)
            checkpoint_args = {
                "session_id": source_id,
                "vault_name": vault_name,
                "checkpoint_kind": checkpoint_kind,
                "source": "validation",
                "message_count_before": 4,
                "last_message_sequence_index": 3,
                "summary_message": context,
                "metadata": {"map_observed_through_sequence_index": 3},
            }
            store.add_context_checkpoint(
                **checkpoint_args,
                checkpoint_id=checkpoint_id,
                replacement_history=valid_replacement,
                replacement_source_sequence_indexes=[None, 2, 3],
            )
            projected = store.get_stored_messages(source_id, vault_name)
            assert [item.fork_sequence_index for item in projected] == [
                None,
                2,
                3,
            ], "Synthetic context and an exact canonical tail should retain valid origins"
            valid_child_id = f"{source_id}-valid-child"
            store.fork_session(
                source_session_id=source_id,
                new_session_id=valid_child_id,
                vault_name=vault_name,
                through_sequence_index=3,
                title="Valid origins",
            )
            assert (
                store.get_history(valid_child_id, vault_name) == valid_replacement
            ), "Valid explicit origins should survive checkpoint inheritance"
            if label == "missing":
                with sqlite3.connect(database_path) as conn:
                    conn.execute(
                        "DELETE FROM chat_messages WHERE session_id = ? AND sequence_index = 2",
                        (source_id,),
                    )
            canonical_before = store.get_stored_messages(
                source_id, vault_name, mode="raw"
            )
            checkpoints_before = store.list_context_checkpoints(source_id, vault_name)
            revision_before = store.get_session_history_revision(source_id, vault_name)
            if label != "invalid-json":
                try:
                    store.add_context_checkpoint(
                        **checkpoint_args,
                        checkpoint_id=f"{source_id}-invalid-write",
                        replacement_history=replacement,
                        replacement_source_sequence_indexes=json.loads(encoded_origins),
                        metadata_update={"invalid_origin_write": label},
                    )
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"Invalid writer origins must fail: {label}")
                assert (
                    store.list_context_checkpoints(source_id, vault_name)
                    == checkpoints_before
                ), "Invalid writer origins must not append a checkpoint"
                assert "invalid_origin_write" not in store.get_session_metadata(
                    source_id, vault_name
                ), "Invalid writer origins must roll back metadata updates"
            with sqlite3.connect(database_path) as conn:
                conn.execute(
                    "\n                        UPDATE chat_compaction_checkpoints\n                        SET replacement_history_json = ?, replacement_source_sequence_indexes_json = ?\n                        WHERE checkpoint_id = ?\n                        ",
                    (
                        adapter.dump_json(replacement).decode(),
                        encoded_origins,
                        checkpoint_id,
                    ),
                )
            corrupted_checkpoints = store.list_context_checkpoints(
                source_id, vault_name
            )
            child_id = f"{source_id}-invalid-child"
            for operation in (
                partial(store.get_stored_messages, source_id, vault_name),
                partial(
                    store.fork_session,
                    source_session_id=source_id,
                    new_session_id=child_id,
                    vault_name=vault_name,
                    through_sequence_index=3,
                    title="Reject corrupted origins",
                ),
            ):
                try:
                    operation()
                except ChatHistoryCorruptionError as exc:
                    assert (exc.session_id, exc.vault_name, exc.checkpoint_id) == (
                        source_id,
                        vault_name,
                        checkpoint_id,
                    ), "Persisted origin corruption must identify its checkpoint"
                else:
                    raise AssertionError(
                        f"Persisted origin corruption must fail: {label}"
                    )
            assert (
                store.get_session(child_id, vault_name) is None
            ), "Invalid inherited origins must leave no child session"
            assert (
                store.get_stored_messages(child_id, vault_name, mode="raw") == []
            ), "Invalid inherited origins must leave no child messages"
            assert (
                store.get_tool_events(child_id, vault_name) == []
            ), "Invalid inherited origins must leave no child tool events"
            assert (
                store.list_context_checkpoints(child_id, vault_name) == []
            ), "Invalid inherited origins must leave no child checkpoints"
            assert (
                store.get_stored_messages(source_id, vault_name, mode="raw")
                == canonical_before
            ), "Origin validation must preserve source canonical records"
            assert (
                store.list_context_checkpoints(source_id, vault_name)
                == corrupted_checkpoints
            ), "Rejected forks must preserve source checkpoint records"
            assert (
                store.get_session_history_revision(source_id, vault_name)
                == revision_before
            ), "Invalid origins must not advance the source revision"


async def test_checkpoint_payload_validation(app: Application) -> None:
    vault = app.vault("ForkLineageVault")
    await app.start()
    from core.chat.chat_store import (
        ChatHistoryCorruptionError,
        ContextCheckpointKind,
    )
    from core.identity import LOCAL_USER_PRINCIPAL_ID
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    vault_name = vault.name
    database_path = app.root / "system/chat_sessions.db"
    checkpoint_kinds: tuple[ContextCheckpointKind, ...] = (
        "recovery_card",
        "session_map",
    )
    for checkpoint_kind in checkpoint_kinds:
        source_id = f"fork-invalid-{checkpoint_kind}-payload"
        checkpoint_id = f"{source_id}-older"
        store.ensure_session(
            source_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        canonical_messages = [
            _user("First question"),
            _assistant("First answer"),
            _user("Second question"),
            _assistant("Second answer"),
        ]
        store.add_messages(source_id, vault_name, canonical_messages)
        for label, boundary in (("older", 1), ("latest", 3)):
            context = _map_context(f"{label} context")
            store.add_context_checkpoint(
                session_id=source_id,
                vault_name=vault_name,
                checkpoint_id=f"{source_id}-{label}",
                checkpoint_kind=checkpoint_kind,
                source="validation",
                message_count_before=4,
                last_message_sequence_index=boundary,
                summary_message=context,
                replacement_history=[context],
                replacement_source_sequence_indexes=[None],
                metadata={"map_observed_through_sequence_index": boundary},
            )
        checkpoints_before = store.list_context_checkpoints(source_id, vault_name)
        revision_before = store.get_session_history_revision(source_id, vault_name)
        try:
            for index, payload in enumerate(("broken", '[{"kind":"unknown"}]')):
                child_id = f"{source_id}-child-{index}"
                with sqlite3.connect(database_path) as conn:
                    conn.execute(
                        "\n                            UPDATE chat_compaction_checkpoints\n                            SET replacement_history_json = ? WHERE checkpoint_id = ?\n                            ",
                        (payload, checkpoint_id),
                    )
                try:
                    store.fork_session(
                        source_session_id=source_id,
                        new_session_id=child_id,
                        vault_name=vault_name,
                        through_sequence_index=3,
                        title="Reject damaged inherited checkpoint",
                    )
                except ChatHistoryCorruptionError as exc:
                    assert (exc.session_id, exc.vault_name, exc.checkpoint_id) == (
                        source_id,
                        vault_name,
                        checkpoint_id,
                    ), "Fork validation should identify a damaged older eligible checkpoint"
                else:
                    raise AssertionError(
                        "Forks must validate every eligible replacement payload"
                    )
                assert (
                    store.get_session(child_id, vault_name) is None
                ), "A damaged eligible checkpoint must leave no child session"
                assert (
                    store.get_stored_messages(child_id, vault_name, mode="raw") == []
                ), "A rejected checkpoint payload must leave no child messages"
                assert (
                    store.get_tool_events(child_id, vault_name) == []
                ), "A rejected checkpoint payload must leave no child tool events"
                assert (
                    store.list_context_checkpoints(child_id, vault_name) == []
                ), "A rejected checkpoint payload must leave no child checkpoints"
        finally:
            with sqlite3.connect(database_path) as conn:
                conn.execute(
                    "\n                        UPDATE chat_compaction_checkpoints\n                        SET replacement_history_json = ? WHERE checkpoint_id = ?\n                        ",
                    (checkpoints_before[0].replacement_history_json, checkpoint_id),
                )
        assert (
            store.get_history(source_id, vault_name, mode="raw") == canonical_messages
        ), "Rejected checkpoint forks must preserve source canonical history"
        assert (
            store.list_context_checkpoints(source_id, vault_name) == checkpoints_before
        ), "Rejected checkpoint forks must preserve source checkpoints"
        assert (
            store.get_session_history_revision(source_id, vault_name) == revision_before
        ), "Rejected checkpoint forks must preserve the source revision"
