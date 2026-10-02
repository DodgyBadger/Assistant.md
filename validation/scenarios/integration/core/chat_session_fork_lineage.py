"""Validate canonical session forks with checkpoint lineage."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from core.chat.chat_store import ChatHistoryCorruptionError  # noqa: E402
from core.identity import LOCAL_USER_PRINCIPAL_ID  # noqa: E402
from core.memory.session_map.checkpoints import (
    load_session_map_observed_through,  # noqa: E402
)
from core.memory.session_map.models import SessionMapDraft  # noqa: E402
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class ChatSessionForkLineageScenario(BaseScenario):
    """Fork canonical history and only checkpoints safe at the branch point."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("ChatSessionForkLineageVault")
        await self.start_system()
        store = get_runtime_context().chat_store
        session_id = "fork-lineage-source"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        raw_messages = [
            _user("Begin the review."),
            _assistant("I will establish the scope."),
            _user("Compare the two options."),
            _assistant("The comparison is still exploratory."),
            _user("Focus next on cost."),
            _assistant("I will investigate cost without choosing yet."),
            _user("Now include timing."),
            _assistant("Cost and timing are both open."),
        ]
        store.add_messages(session_id, vault.name, raw_messages)
        first_context = _map_context("Initial scope only")
        store.add_context_checkpoint(
            session_id=session_id,
            vault_name=vault.name,
            checkpoint_id="source-map-1",
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=8,
            last_message_sequence_index=1,
            summary_message=first_context,
            replacement_history=[first_context],
            replacement_source_sequence_indexes=[None],
            metadata={
                "map_observed_through_sequence_index": 1,
                "map": SessionMapDraft().model_dump(mode="json"),
            },
        )
        second_context = _map_context("Cost and timing synthesis")
        store.add_context_checkpoint(
            session_id=session_id,
            vault_name=vault.name,
            checkpoint_id="source-map-2",
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=7,
            last_message_sequence_index=3,
            summary_message=second_context,
            replacement_history=[second_context],
            replacement_source_sequence_indexes=[None],
            metadata={
                "map_observed_through_sequence_index": 7,
                "map": SessionMapDraft().model_dump(mode="json"),
            },
        )

        source_before = store.get_stored_messages(session_id, vault.name, mode="raw")
        response = self.call_api(
            f"/api/chat/sessions/{session_id}/fork",
            method="POST",
            data={"vault_name": vault.name, "through_sequence_index": 5},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        child_id = payload["session"]["session_id"]
        self.soft_assert_equal(
            payload["copied_message_count"],
            6,
            "Fork should report canonical raw messages copied",
        )
        self.soft_assert_equal(
            payload["session"]["context_strategy"],
            "session_map",
            "A safe inherited map checkpoint should pin the child to V2",
        )
        child_raw = store.get_stored_messages(child_id, vault.name, mode="raw")
        self.soft_assert_equal(
            [message.sequence_index for message in child_raw],
            list(range(6)),
            "Child should preserve canonical parent sequence coordinates",
        )
        self.soft_assert_equal(
            [message.message for message in child_raw],
            raw_messages[:6],
            "Child raw history should be the exact canonical source prefix",
        )
        child_checkpoints = store.list_context_checkpoints(child_id, vault.name)
        self.soft_assert_equal(
            len(child_checkpoints),
            1,
            "A future-informed map checkpoint should not cross an earlier fork",
        )
        child_checkpoint = child_checkpoints[0]
        self.soft_assert(
            child_checkpoint.checkpoint_id != "source-map-1",
            "Inherited checkpoints should receive child-owned identifiers",
        )
        child_checkpoint_metadata = json.loads(child_checkpoint.metadata_json or "{}")
        self.soft_assert_equal(
            child_checkpoint_metadata["fork_origin"],
            {
                "source_session_id": session_id,
                "source_checkpoint_id": "source-map-1",
            },
            "Inherited checkpoint metadata should retain explicit origin",
        )
        lineage = store.get_session_metadata(child_id, vault.name)["fork"]
        self.soft_assert_equal(
            lineage["root_session_id"],
            session_id,
            "First-generation fork should record the source as lineage root",
        )
        self.soft_assert_equal(
            lineage["child_owned_from_sequence_index"],
            6,
            "Lineage should identify where independently owned history begins",
        )
        self.soft_assert_equal(
            store.get_stored_messages(session_id, vault.name, mode="raw"),
            source_before,
            "Forking must not mutate canonical source history",
        )
        self.soft_assert_equal(
            len(store.list_context_checkpoints(session_id, vault.name)),
            2,
            "Forking must not mutate source checkpoints",
        )

        nested_response = self.call_api(
            f"/api/chat/sessions/{child_id}/fork",
            method="POST",
            data={"vault_name": vault.name, "through_sequence_index": 5},
        )
        assert nested_response.status_code == 200, nested_response.text
        nested_id = nested_response.json()["session"]["session_id"]
        nested_lineage = store.get_session_metadata(nested_id, vault.name)["fork"]
        self.soft_assert_equal(
            nested_lineage["source_session_id"],
            child_id,
            "Nested lineage should name its immediate parent",
        )
        self.soft_assert_equal(
            nested_lineage["root_session_id"],
            session_id,
            "Nested lineage should retain the original family root",
        )

        latest_response = self.call_api(
            f"/api/chat/sessions/{session_id}/fork",
            method="POST",
            data={"vault_name": vault.name, "through_sequence_index": 7},
        )
        assert latest_response.status_code == 200, latest_response.text
        latest_id = latest_response.json()["session"]["session_id"]
        self.soft_assert_equal(
            len(store.list_context_checkpoints(latest_id, vault.name)),
            2,
            "Forking at the latest observation boundary should inherit all revisions",
        )
        map_response = self.call_api(
            f"/api/chat/sessions/{latest_id}/map?vault_name={vault.name}"
        )
        assert map_response.status_code == 200, map_response.text
        self.soft_assert_equal(
            len(map_response.json()["revisions"]),
            2,
            "Inherited V2 revisions should remain inspectable through the map modal API",
        )
        evicted_fork_response = self.call_api(
            f"/api/chat/sessions/{session_id}/fork",
            method="POST",
            data={"vault_name": vault.name, "through_sequence_index": 3},
        )
        assert evicted_fork_response.status_code == 200, evicted_fork_response.text
        evicted_child_id = evicted_fork_response.json()["session"]["session_id"]
        self.soft_assert_equal(
            len(store.list_context_checkpoints(evicted_child_id, vault.name)),
            1,
            "A fork from evicted canonical history should exclude future-informed maps",
        )
        self.soft_assert_equal(
            store.get_message_count(evicted_child_id, vault.name, mode="raw"),
            4,
            "A fork from evicted history should end at the selected canonical message",
        )

        with patch(
            "core.chat.chat_store.ChatStore.fork_session",
            side_effect=sqlite3.IntegrityError("private fork failure marker"),
        ):
            injected_failure = self.call_api(
                f"/api/chat/sessions/{session_id}/fork",
                method="POST",
                data={"vault_name": vault.name, "through_sequence_index": 5},
            )
        self.soft_assert_equal(
            injected_failure.status_code,
            500,
            "An injected persistence failure should fail the fork request",
        )

        atomic_child_id = "atomic-failure-child"
        database_path = get_runtime_context().config.system_root / "chat_sessions.db"
        with sqlite3.connect(database_path) as conn:
            conn.execute(
                """
                CREATE TRIGGER reject_atomic_fork_checkpoint
                BEFORE INSERT ON chat_compaction_checkpoints
                WHEN NEW.session_id = 'atomic-failure-child'
                BEGIN
                    SELECT RAISE(ABORT, 'injected checkpoint copy failure');
                END
                """
            )
        try:
            try:
                store.fork_session(
                    source_session_id=session_id,
                    new_session_id=atomic_child_id,
                    vault_name=vault.name,
                    through_sequence_index=7,
                    title="Injected atomic failure",
                )
            except sqlite3.IntegrityError as exc:
                self.soft_assert(
                    "injected checkpoint copy failure" in str(exc),
                    "Injected failure should occur during checkpoint cloning",
                )
            else:
                self.soft_assert(False, "Injected checkpoint failure should abort fork")
        finally:
            with sqlite3.connect(database_path) as conn:
                conn.execute("DROP TRIGGER reject_atomic_fork_checkpoint")
        self.soft_assert_equal(
            store.get_session(atomic_child_id, vault.name),
            None,
            "A failed fork transaction should leave no child session",
        )
        self.soft_assert_equal(
            store.get_stored_messages(atomic_child_id, vault.name, mode="raw"),
            [],
            "A failed fork transaction should leave no child messages",
        )
        self.soft_assert_equal(
            store.list_context_checkpoints(atomic_child_id, vault.name),
            [],
            "A failed fork transaction should leave no child checkpoints",
        )

        legacy_session_id = "ambiguous-legacy-checkpoint"
        store.ensure_session(
            legacy_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        duplicate_response = _assistant("The same legacy answer.")
        legacy_raw = [
            _user("First occurrence."),
            duplicate_response,
            _user("Second occurrence."),
            duplicate_response,
        ]
        store.add_messages(legacy_session_id, vault.name, legacy_raw)
        legacy_summary = _map_context("Legacy recovery context")
        store.add_compaction_checkpoint(
            session_id=legacy_session_id,
            vault_name=vault.name,
            checkpoint_id="legacy-ambiguous-checkpoint",
            source="validation",
            message_count_before=4,
            last_message_sequence_index=3,
            summary_message=legacy_summary,
            replacement_history=[legacy_summary, duplicate_response],
        )
        projected_legacy = store.get_stored_messages(legacy_session_id, vault.name)
        self.soft_assert_equal(
            projected_legacy[-1].fork_sequence_index,
            None,
            "Ambiguous legacy retained messages should not receive a guessed origin",
        )
        canonical_legacy_response = self.call_api(
            f"/api/chat/sessions/{legacy_session_id}/fork",
            method="POST",
            data={"vault_name": vault.name, "through_sequence_index": 3},
        )
        self.soft_assert_equal(
            canonical_legacy_response.status_code,
            200,
            "An explicit canonical transcript index should remain forkable",
        )
        canonical_legacy_child = canonical_legacy_response.json()["session"][
            "session_id"
        ]
        self.soft_assert_equal(
            store.get_message_count(canonical_legacy_child, vault.name, mode="raw"),
            4,
            "Canonical transcript forks should not depend on replacement-message inference",
        )

        protocol_session_id = "fork-protocol-source"
        store.ensure_session(
            protocol_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        protocol_messages = [
            _user("Start a tool-assisted review."),
            _assistant("I will review the sources."),
            _user("Check the source."),
            ModelResponse(
                parts=[ToolCallPart(tool_name="probe", args={}, tool_call_id="closed")]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="probe", content="Checked", tool_call_id="closed"
                    )
                ]
            ),
            _assistant("The source is checked."),
            _user("Check another source."),
            ModelResponse(
                parts=[ToolCallPart(tool_name="probe", args={}, tool_call_id="pending")]
            ),
            _assistant("The second tool cycle is still incomplete."),
        ]
        store.add_messages(protocol_session_id, vault.name, protocol_messages)
        for boundary in (0, 2, 3, 4, 6, 7, 8):
            rejected_child_id = f"rejected-protocol-child-{boundary}"
            try:
                store.fork_session(
                    source_session_id=protocol_session_id,
                    new_session_id=rejected_child_id,
                    vault_name=vault.name,
                    through_sequence_index=boundary,
                    title="Invalid protocol boundary",
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"Core fork must reject unsafe canonical boundary {boundary}"
                )
            assert store.get_session(rejected_child_id, vault.name) is None
            assert not store.get_stored_messages(
                rejected_child_id, vault.name, mode="raw"
            )
            rejected_response = self.call_api(
                f"/api/chat/sessions/{protocol_session_id}/fork",
                method="POST",
                data={"vault_name": vault.name, "through_sequence_index": boundary},
            )
            assert rejected_response.status_code == 400, rejected_response.text

        valid_response = self.call_api(
            f"/api/chat/sessions/{protocol_session_id}/fork",
            method="POST",
            data={"vault_name": vault.name, "through_sequence_index": 5},
        )
        assert valid_response.status_code == 200, valid_response.text
        assert valid_response.json()["copied_message_count"] == 6
        protocol_context = _map_context("Prior tool review context")
        store.add_compaction_checkpoint(
            session_id=protocol_session_id,
            vault_name=vault.name,
            checkpoint_id="protocol-effective-checkpoint",
            source="validation",
            message_count_before=9,
            last_message_sequence_index=8,
            summary_message=protocol_context,
            replacement_history=[protocol_context, *protocol_messages[5:]],
            replacement_source_sequence_indexes=[None, 5, 6, 7, 8],
        )
        protocol_detail = self.call_api(
            f"/api/chat/sessions/{protocol_session_id}?vault_name={vault.name}"
        )
        assert protocol_detail.status_code == 200, protocol_detail.text
        projected_protocol = protocol_detail.json()["messages"]
        assert (
            projected_protocol[1]["fork_sequence_index"] == 5
        ), "Effective projection must preserve safe canonical assistant origins"
        assert projected_protocol[3]["fork_sequence_index"] is None
        assert (
            projected_protocol[4]["fork_sequence_index"] is None
        ), "Effective projection must withhold unresolved tool-cycle fork actions"
        legacy_detail = self.call_api(
            f"/api/chat/sessions/{legacy_session_id}?vault_name={vault.name}"
        )
        assert legacy_detail.status_code == 200, legacy_detail.text
        assert (
            legacy_detail.json()["messages"][-1]["fork_sequence_index"] is None
        ), "API projection must not invent ambiguous legacy canonical origins"

        malformed_session_id = "fork-orphan-tool-result"
        store.ensure_session(
            malformed_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            malformed_session_id,
            vault.name,
            [
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="probe", content="Orphan", tool_call_id="missing"
                        )
                    ]
                ),
                _assistant("An unmatched tool result precedes this response."),
            ],
        )
        try:
            store.fork_session(
                source_session_id=malformed_session_id,
                new_session_id="rejected-orphan-result-child",
                vault_name=vault.name,
                through_sequence_index=1,
                title="Malformed tool protocol",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Core fork must reject an orphan tool-result prefix")
        assert store.get_session("rejected-orphan-result-child", vault.name) is None

        corrupt_child_id = "corrupt-source-child"
        revision_before = store.get_session_history_revision(session_id, vault.name)
        private_marker = "private-canonical-contents"
        with sqlite3.connect(database_path) as conn:
            conn.execute(
                """
                UPDATE chat_messages SET message_json = ?
                WHERE session_id = ? AND vault_name = ? AND sequence_index = 1
                """,
                (json.dumps({"private": private_marker}), session_id, vault.name),
            )
            damaged_source_rows = conn.execute(
                """
                SELECT sequence_index, message_json FROM chat_messages
                WHERE session_id = ? AND vault_name = ? ORDER BY sequence_index
                """,
                (session_id, vault.name),
            ).fetchall()
        try:
            for operation in (
                lambda: store.get_history(session_id, vault.name, mode="raw"),
                lambda: store.fork_session(
                    source_session_id=session_id,
                    new_session_id=corrupt_child_id,
                    vault_name=vault.name,
                    through_sequence_index=7,
                    title="Damaged canonical prefix",
                ),
            ):
                try:
                    operation()
                except ChatHistoryCorruptionError as exc:
                    self.soft_assert_equal(
                        (
                            exc.session_id,
                            exc.vault_name,
                            exc.sequence_index,
                        ),
                        (session_id, vault.name, 1),
                        "A damaged canonical row should identify its session and sequence",
                    )
                else:
                    self.soft_assert(
                        False,
                        "Canonical reads and forks must reject a damaged interior message",
                    )
            self.soft_assert_equal(
                store.get_session(corrupt_child_id, vault.name),
                None,
                "A damaged source prefix must not create a partial child session",
            )
            self.soft_assert_equal(
                store.get_stored_messages(corrupt_child_id, vault.name, mode="raw"),
                [],
                "A rejected corrupt fork must leave no child messages",
            )
            self.soft_assert_equal(
                store.list_context_checkpoints(corrupt_child_id, vault.name),
                [],
                "A rejected corrupt fork must leave no inherited checkpoints",
            )
            self.soft_assert_equal(
                store.get_session_history_revision(session_id, vault.name),
                revision_before,
                "Rejecting corrupt history must not advance the source revision",
            )
            with sqlite3.connect(database_path) as conn:
                self.soft_assert_equal(
                    conn.execute(
                        """
                        SELECT sequence_index, message_json FROM chat_messages
                        WHERE session_id = ? AND vault_name = ? ORDER BY sequence_index
                        """,
                        (session_id, vault.name),
                    ).fetchall(),
                    damaged_source_rows,
                    "Rejecting corrupt history must preserve the original persisted rows",
                )
        finally:
            with sqlite3.connect(database_path) as conn:
                conn.execute(
                    """
                    UPDATE chat_messages SET message_json = ?
                    WHERE session_id = ? AND vault_name = ? AND sequence_index = 1
                    """,
                    (source_before[1].message_json, session_id, vault.name),
                )
        activity_response = self.call_api("/api/system/activity-log?limit=200")
        assert activity_response.status_code == 200
        entries = activity_response.json()["entries"]
        completed_forks = [
            entry
            for entry in entries
            if entry.get("data", {}).get("event") == "chat_session_fork_completed"
            and entry.get("data", {}).get("source_session_id") == session_id
            and entry.get("data", {}).get("new_session_id") == child_id
            and entry.get("data", {}).get("canonical_through_sequence_index") == 5
        ]
        self.soft_assert(
            any(
                entry.get("data", {}).get("status") == "completed"
                and entry.get("data", {}).get("operation_id")
                for entry in completed_forks
            ),
            "Successful fork Activity should include status, source/child identity, boundary, and correlation ID",
        )
        fork_failures = [
            entry
            for entry in entries
            if entry.get("data", {}).get("event") == "chat_session_fork_failed"
            and entry.get("data", {}).get("source_session_id") == session_id
            and entry.get("data", {}).get("canonical_through_sequence_index") == 5
        ]
        self.soft_assert(
            any(
                entry.get("data", {}).get("status") == "failed"
                and entry.get("data", {}).get("vault_name") == vault.name
                and entry.get("data", {}).get("error_type") == "IntegrityError"
                and entry.get("data", {}).get("issue")
                and entry.get("data", {}).get("operation_id")
                for entry in fork_failures
            ),
            "Failed fork Activity should retain a distinct lifecycle and failure identity",
        )
        completed_operation_ids = {
            entry.get("data", {}).get("operation_id") for entry in completed_forks
        }
        self.soft_assert(
            any(
                entry.get("data", {}).get("event") == "chat_session_fork_started"
                and entry.get("data", {}).get("status") == "started"
                and entry.get("data", {}).get("source_session_id") == session_id
                and entry.get("data", {}).get("canonical_through_sequence_index") == 5
                and entry.get("data", {}).get("operation_id") in completed_operation_ids
                for entry in entries
            ),
            "Fork start and completion should share a searchable operation correlation ID",
        )
        failed_operation_ids = {
            entry.get("data", {}).get("operation_id") for entry in fork_failures
        }
        self.soft_assert(
            any(
                entry.get("data", {}).get("event") == "chat_session_fork_started"
                and entry.get("data", {}).get("status") == "started"
                and entry.get("data", {}).get("operation_id") in failed_operation_ids
                for entry in entries
            ),
            "The failed fork should retain a correlated start record",
        )
        self.soft_assert(
            "private fork failure marker" not in json.dumps(fork_failures),
            "Fork failure Activity must not log raw persistence exception text",
        )
        self.soft_assert(
            any(
                entry.get("data", {}).get("event")
                == "chat_history_deserialization_failed"
                and entry["data"].get("status") == "failed"
                and entry["data"].get("session_id") == session_id
                and entry["data"].get("vault_name") == vault.name
                and entry["data"].get("sequence_index") == 1
                and entry["data"].get("error_type") == "ChatHistoryCorruptionError"
                for entry in entries
            ),
            "System Activity should identify the canonical row that prevents reading or forking",
        )
        self.soft_assert(
            private_marker not in json.dumps(entries),
            "Canonical failure diagnostics must not include persisted transcript contents",
        )

        checkpoint = store.get_latest_context_checkpoint(session_id, vault.name)
        assert checkpoint is not None
        malformed_metadata = [
            json.dumps({"map_observed_through_sequence_index": value})
            for value in (-1, 2, None, True, "7")
        ] + ["{", "[]"]
        for metadata_json in malformed_metadata:
            damaged = replace(checkpoint, metadata_json=metadata_json)
            try:
                load_session_map_observed_through(damaged)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "Explicit invalid checkpoint cutoffs must fail closed"
                )
        for metadata_json in (None, "{}"):
            legacy = replace(checkpoint, metadata_json=metadata_json)
            self.soft_assert_equal(
                load_session_map_observed_through(legacy),
                checkpoint.last_message_sequence_index,
                "Only absent legacy observation metadata may use the consumed boundary",
            )
        unsafe_child_id = "invalid-cutoff-child"
        with sqlite3.connect(database_path) as conn:
            conn.execute(
                """
                UPDATE chat_compaction_checkpoints SET metadata_json = ?
                WHERE checkpoint_id = ?
                """,
                ("{", checkpoint.checkpoint_id),
            )
        try:
            try:
                store.fork_session(
                    source_session_id=session_id,
                    new_session_id=unsafe_child_id,
                    vault_name=vault.name,
                    through_sequence_index=7,
                    title="Invalid checkpoint observation cutoff",
                )
            except ValueError:
                pass
            else:
                raise AssertionError("A fork must reject malformed checkpoint metadata")
            self.soft_assert_equal(
                store.get_session(unsafe_child_id, vault.name),
                None,
                "A rejected observation cutoff must leave no forked child",
            )
        finally:
            with sqlite3.connect(database_path) as conn:
                conn.execute(
                    """
                    UPDATE chat_compaction_checkpoints SET metadata_json = ?
                    WHERE checkpoint_id = ?
                    """,
                    (checkpoint.metadata_json, checkpoint.checkpoint_id),
                )


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _map_context(text: str) -> ModelRequest:
    return ModelRequest(parts=[SystemPromptPart(content=text)])
