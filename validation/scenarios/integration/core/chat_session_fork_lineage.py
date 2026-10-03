"""Validate canonical session forks with checkpoint lineage."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import replace
from functools import partial
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic import TypeAdapter  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from core.chat.chat_store import (  # noqa: E402
    ChatHistoryCorruptionError,
    ChatStore,
    ContextCheckpointKind,
    StoredChatMessage,
    canonical_assistant_fork_points,
    tool_call_events_are_unambiguous,
)
from core.chat.tool_history import analyze_tool_history  # noqa: E402
from core.identity import LOCAL_USER_PRINCIPAL_ID  # noqa: E402
from core.memory.session_map.checkpoints import (
    load_session_map_observed_through,  # noqa: E402
)
from core.memory.session_map.models import SessionMapDraft  # noqa: E402
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402
from validation.core.tool_history_fixtures import tool_reply_cases  # noqa: E402


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
        rejected_forks = [
            entry
            for entry in entries
            if entry.get("data", {}).get("event") == "chat_session_fork_failed"
            and entry.get("data", {}).get("source_session_id") == protocol_session_id
        ]
        self.soft_assert(
            any(
                entry.get("data", {}).get("status") == "failed"
                and entry.get("data", {}).get("vault_name") == vault.name
                and entry.get("data", {}).get("error_type")
                == "ChatSessionForkPointInvalid"
                and entry.get("data", {}).get("reason") == "request_rejected"
                and entry.get("data", {}).get("operation_id")
                and "protocol-complete" not in json.dumps(entry)
                for entry in rejected_forks
            ),
            "Rejected fork Activity should preserve its semantic error type without exception detail",
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

        self._test_concurrent_fork_snapshot(store, vault.name, database_path)
        self._test_reused_tool_event_lineage(store, vault.name)
        self._test_tool_event_group_lineage(store, vault.name)
        self._test_checkpoint_payload_validation(store, vault.name, database_path)
        self._test_checkpoint_origin_validation(store, vault.name, database_path)
        self._test_legacy_checkpoint_origin_projection(store, vault.name)
        self._test_retry_fork_points(store, vault.name)
        self._test_fork_protocol_parity(store, vault.name)

    def _test_legacy_checkpoint_origin_projection(
        self, store: ChatStore, vault_name: str
    ) -> None:
        session_id = "legacy-checkpoint-origin-projection"
        canonical = [
            _user("First question"),
            _assistant("First answer"),
            _user("Retained question"),
            _assistant("Retained answer"),
        ]
        store.ensure_session(
            session_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        store.add_messages(session_id, vault_name, canonical)
        context = _map_context("Legacy recovery context")
        store.add_context_checkpoint(
            session_id=session_id,
            vault_name=vault_name,
            checkpoint_id="legacy-origin-checkpoint",
            checkpoint_kind="recovery_card",
            source="validation",
            message_count_before=len(canonical),
            last_message_sequence_index=3,
            summary_message=context,
            replacement_history=[context, canonical[-1]],
            replacement_source_sequence_indexes=None,
        )

        raw_fetches: list[dict[str, object]] = []
        original_fetch = store._fetch_raw_messages_from_conn

        def record_fetch(*args: object, **kwargs: object):
            raw_fetches.append(dict(kwargs))
            return original_fetch(*args, **kwargs)

        with patch.object(store, "_fetch_raw_messages_from_conn", record_fetch):
            effective = store.get_stored_messages(session_id, vault_name)

        self.soft_assert_equal(
            [message.fork_sequence_index for message in effective],
            [None, 3],
            "Legacy replacement messages should retain uniquely matched canonical origins",
        )
        self.soft_assert(
            all(fetch.get("through_sequence_index") is None for fetch in raw_fetches),
            "Legacy origin matching must not hydrate the evicted canonical prefix",
        )

    def _test_retry_fork_points(self, store: ChatStore, vault_name: str) -> None:
        call = ModelResponse(parts=[ToolCallPart("probe", {}, "first")])
        retry = ModelRequest(
            parts=[RetryPromptPart("Retry", tool_name="probe", tool_call_id="first")]
        )
        wrong_retry = ModelRequest(
            parts=[RetryPromptPart("Retry", tool_name="other", tool_call_id="first")]
        )
        cases: tuple[tuple[str, list[ModelMessage], set[int]], ...] = (
            (
                "named-retry",
                [_user("Check"), call, retry, _assistant("Done")],
                {3},
            ),
            (
                "named-retry-new-call",
                [
                    _user("Check"),
                    call,
                    retry,
                    ModelResponse(parts=[ToolCallPart("probe", {}, "corrected")]),
                    ModelRequest(parts=[ToolReturnPart("probe", "Done", "corrected")]),
                    _assistant("Done"),
                ],
                {5},
            ),
            (
                "output-retry-pending-call",
                [
                    _user("Check"),
                    call,
                    ModelRequest(parts=[RetryPromptPart("Fix output")]),
                    _assistant("Done"),
                ],
                set(),
            ),
            (
                "wrong-tool-retry",
                [_user("Check"), call, wrong_retry, _assistant("Done")],
                set(),
            ),
            (
                "earlier-complete-prefix",
                [
                    _user("Check"),
                    _assistant("Earlier answer"),
                    call,
                    wrong_retry,
                    _assistant("Done"),
                ],
                {1},
            ),
        )
        for label, messages, expected_points in cases:
            source_id = f"fork-retry-{label}"
            store.ensure_session(
                source_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
            )
            store.add_messages(source_id, vault_name, messages)
            raw = store.get_stored_messages(source_id, vault_name, mode="raw")
            assert canonical_assistant_fork_points(raw) == expected_points, label
            candidates = [
                index
                for index, message in enumerate(messages)
                if isinstance(message, ModelResponse)
            ]
            assert (
                store.get_canonical_fork_points_for_sequences(
                    source_id, vault_name, candidates
                )
                == expected_points
            ), label
            for boundary in expected_points:
                response = self.call_api(
                    f"/api/chat/sessions/{source_id}/fork",
                    method="POST",
                    data={"vault_name": vault_name, "through_sequence_index": boundary},
                )
                assert response.status_code == 200, (label, response.text)
                child_id = response.json()["session"]["session_id"]
                assert [
                    item.message
                    for item in store.get_stored_messages(
                        child_id, vault_name, mode="raw"
                    )
                ] == messages[: boundary + 1], label
            final_boundary = len(messages) - 1
            if final_boundary in expected_points:
                continue
            rejected_child_id = f"{source_id}-rejected-child"
            try:
                store.fork_session(
                    source_session_id=source_id,
                    new_session_id=rejected_child_id,
                    vault_name=vault_name,
                    through_sequence_index=final_boundary,
                    title="Unsafe retry fork",
                )
            except ValueError:
                pass
            else:
                raise AssertionError(f"Unsafe retry prefix must not fork: {label}")
            assert store.get_session(rejected_child_id, vault_name) is None, label
            response = self.call_api(
                f"/api/chat/sessions/{source_id}/fork",
                method="POST",
                data={
                    "vault_name": vault_name,
                    "through_sequence_index": final_boundary,
                },
            )
            assert response.status_code == 400, (label, response.text)

    def _test_fork_protocol_parity(self, store: ChatStore, vault_name: str) -> None:
        cases = [(case.name, case.messages) for case in tool_reply_cases()]
        call = ModelResponse(parts=[ToolCallPart("probe", {}, "first")])
        reply = ModelRequest(parts=[ToolReturnPart("probe", "Done", "first")])
        native_call = NativeToolCallPart("web_search", {}, "first")
        native_return = NativeToolReturnPart("web_search", "Done", "first")
        cases.extend(
            [
                (
                    "wrong-return-name",
                    [
                        call,
                        ModelRequest(parts=[ToolReturnPart("other", "Done", "first")]),
                    ],
                ),
                ("non-adjacent-return", [call, _user("Intervening message"), reply]),
                (
                    "non-adjacent-retry",
                    [
                        call,
                        _user("Intervening message"),
                        ModelRequest(
                            parts=[
                                RetryPromptPart(
                                    "Retry", tool_name="probe", tool_call_id="first"
                                )
                            ]
                        ),
                    ],
                ),
                (
                    "duplicate-call-batch",
                    [ModelResponse(parts=[*call.parts, *call.parts]), reply],
                ),
                ("duplicate-pending-call", [call, call, reply]),
                ("future-pending-call", [call]),
                ("completed-reused-id", [call, reply, call, reply]),
                (
                    "native-tool-pair",
                    [ModelResponse(parts=[native_call, native_return])],
                ),
                (
                    "native-return-does-not-close-custom-call",
                    [call, ModelResponse(parts=[native_return])],
                ),
                (
                    "native-call-does-not-open-custom-invocation",
                    [ModelResponse(parts=[native_call]), reply],
                ),
            ]
        )
        for label, tool_call_id in (
            ("blank-call-id", ""),
            ("whitespace-call-id", " \t "),
        ):
            cases.append(
                (
                    label,
                    [
                        ModelResponse(parts=[ToolCallPart("probe", {}, tool_call_id)]),
                        ModelRequest(
                            parts=[ToolReturnPart("probe", "Done", tool_call_id)]
                        ),
                    ],
                )
            )
        for label, protocol_messages in cases:
            source_id = f"fork-parity-{label}"
            messages = [
                _user("Before tool activity"),
                _assistant("Earlier complete answer"),
                *protocol_messages,
                _assistant("Final answer"),
            ]
            store.ensure_session(
                source_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
            )
            store.add_messages(source_id, vault_name, messages)
            candidates = [
                index
                for index, message in enumerate(messages)
                if isinstance(message, ModelResponse)
            ]
            expected = {
                index
                for index in candidates
                if analyze_tool_history(messages[: index + 1]).ok
            }
            raw = store.get_stored_messages(source_id, vault_name, mode="raw")
            assert canonical_assistant_fork_points(raw) == expected, label
            with patch.object(
                store,
                "_stored_messages_from_rows",
                side_effect=AssertionError(
                    "Fork metadata scans must not hydrate history"
                ),
            ):
                assert (
                    store.get_canonical_fork_points_for_sequences(
                        source_id, vault_name, candidates
                    )
                    == expected
                ), label
                for candidate in candidates:
                    assert store.get_canonical_fork_points_for_sequences(
                        source_id, vault_name, [candidate]
                    ) == expected.intersection({candidate}), (label, candidate)

    def _test_checkpoint_origin_validation(
        self, store: ChatStore, vault_name: str, database_path: Path
    ) -> None:
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
                self.soft_assert_equal(
                    [item.fork_sequence_index for item in projected],
                    [None, 2, 3],
                    "Synthetic context and an exact canonical tail should retain valid origins",
                )
                valid_child_id = f"{source_id}-valid-child"
                store.fork_session(
                    source_session_id=source_id,
                    new_session_id=valid_child_id,
                    vault_name=vault_name,
                    through_sequence_index=3,
                    title="Valid origins",
                )
                self.soft_assert_equal(
                    store.get_history(valid_child_id, vault_name),
                    valid_replacement,
                    "Valid explicit origins should survive checkpoint inheritance",
                )
                if label == "missing":
                    with sqlite3.connect(database_path) as conn:
                        conn.execute(
                            "DELETE FROM chat_messages WHERE session_id = ? AND sequence_index = 2",
                            (source_id,),
                        )
                canonical_before = store.get_stored_messages(
                    source_id, vault_name, mode="raw"
                )
                checkpoints_before = store.list_context_checkpoints(
                    source_id, vault_name
                )
                revision_before = store.get_session_history_revision(
                    source_id, vault_name
                )
                if label != "invalid-json":
                    try:
                        store.add_context_checkpoint(
                            **checkpoint_args,
                            checkpoint_id=f"{source_id}-invalid-write",
                            replacement_history=replacement,
                            replacement_source_sequence_indexes=json.loads(
                                encoded_origins
                            ),
                            metadata_update={"invalid_origin_write": label},
                        )
                    except ValueError:
                        pass
                    else:
                        raise AssertionError(
                            f"Invalid writer origins must fail: {label}"
                        )
                    self.soft_assert_equal(
                        store.list_context_checkpoints(source_id, vault_name),
                        checkpoints_before,
                        "Invalid writer origins must not append a checkpoint",
                    )
                    self.soft_assert(
                        "invalid_origin_write"
                        not in store.get_session_metadata(source_id, vault_name),
                        "Invalid writer origins must roll back metadata updates",
                    )
                with sqlite3.connect(database_path) as conn:
                    conn.execute(
                        """
                        UPDATE chat_compaction_checkpoints
                        SET replacement_history_json = ?, replacement_source_sequence_indexes_json = ?
                        WHERE checkpoint_id = ?
                        """,
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
                        self.soft_assert_equal(
                            (exc.session_id, exc.vault_name, exc.checkpoint_id),
                            (source_id, vault_name, checkpoint_id),
                            "Persisted origin corruption must identify its checkpoint",
                        )
                    else:
                        raise AssertionError(
                            f"Persisted origin corruption must fail: {label}"
                        )
                self.soft_assert_equal(
                    store.get_session(child_id, vault_name),
                    None,
                    "Invalid inherited origins must leave no child session",
                )
                self.soft_assert_equal(
                    store.get_stored_messages(child_id, vault_name, mode="raw"),
                    [],
                    "Invalid inherited origins must leave no child messages",
                )
                self.soft_assert_equal(
                    store.get_tool_events(child_id, vault_name),
                    [],
                    "Invalid inherited origins must leave no child tool events",
                )
                self.soft_assert_equal(
                    store.list_context_checkpoints(child_id, vault_name),
                    [],
                    "Invalid inherited origins must leave no child checkpoints",
                )
                self.soft_assert_equal(
                    store.get_stored_messages(source_id, vault_name, mode="raw"),
                    canonical_before,
                    "Origin validation must preserve source canonical records",
                )
                self.soft_assert_equal(
                    store.list_context_checkpoints(source_id, vault_name),
                    corrupted_checkpoints,
                    "Rejected forks must preserve source checkpoint records",
                )
                self.soft_assert_equal(
                    store.get_session_history_revision(source_id, vault_name),
                    revision_before,
                    "Invalid origins must not advance the source revision",
                )

    def _test_checkpoint_payload_validation(
        self, store: ChatStore, vault_name: str, database_path: Path
    ) -> None:
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
                            """
                            UPDATE chat_compaction_checkpoints
                            SET replacement_history_json = ? WHERE checkpoint_id = ?
                            """,
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
                        self.soft_assert_equal(
                            (exc.session_id, exc.vault_name, exc.checkpoint_id),
                            (source_id, vault_name, checkpoint_id),
                            "Fork validation should identify a damaged older eligible checkpoint",
                        )
                    else:
                        raise AssertionError(
                            "Forks must validate every eligible replacement payload"
                        )
                    self.soft_assert_equal(
                        store.get_session(child_id, vault_name),
                        None,
                        "A damaged eligible checkpoint must leave no child session",
                    )
                    self.soft_assert_equal(
                        store.get_stored_messages(child_id, vault_name, mode="raw"),
                        [],
                        "A rejected checkpoint payload must leave no child messages",
                    )
                    self.soft_assert_equal(
                        store.get_tool_events(child_id, vault_name),
                        [],
                        "A rejected checkpoint payload must leave no child tool events",
                    )
                    self.soft_assert_equal(
                        store.list_context_checkpoints(child_id, vault_name),
                        [],
                        "A rejected checkpoint payload must leave no child checkpoints",
                    )
            finally:
                with sqlite3.connect(database_path) as conn:
                    conn.execute(
                        """
                        UPDATE chat_compaction_checkpoints
                        SET replacement_history_json = ? WHERE checkpoint_id = ?
                        """,
                        (checkpoints_before[0].replacement_history_json, checkpoint_id),
                    )
            self.soft_assert_equal(
                store.get_history(source_id, vault_name, mode="raw"),
                canonical_messages,
                "Rejected checkpoint forks must preserve source canonical history",
            )
            self.soft_assert_equal(
                store.list_context_checkpoints(source_id, vault_name),
                checkpoints_before,
                "Rejected checkpoint forks must preserve source checkpoints",
            )
            self.soft_assert_equal(
                store.get_session_history_revision(source_id, vault_name),
                revision_before,
                "Rejected checkpoint forks must preserve the source revision",
            )

    def _test_concurrent_fork_snapshot(
        self, store: ChatStore, vault_name: str, database_path: Path
    ) -> None:
        source_id = "fork-cross-connection-source"
        child_id = "fork-cross-connection-child"
        original_messages = [_user("Original question"), _assistant("Original answer")]
        replacement_messages = [_user("Rewritten question"), _assistant("New answer")]
        store.ensure_session(
            source_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        store.add_messages(source_id, vault_name, original_messages)
        original_context = _map_context("Original context")
        store.add_context_checkpoint(
            session_id=source_id,
            vault_name=vault_name,
            checkpoint_id="fork-original-context",
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=2,
            last_message_sequence_index=1,
            summary_message=original_context,
            replacement_history=[original_context],
            metadata={"map_observed_through_sequence_index": 1},
        )
        external_store = ChatStore(str(database_path.parent))
        original_fetch = store._fetch_raw_messages_from_conn
        write_blocked = False

        def rewrite_source() -> None:
            external_store.replace_session_messages(
                source_id, vault_name, replacement_messages
            )
            replacement_context = _map_context("Rewritten context")
            external_store.add_context_checkpoint(
                session_id=source_id,
                vault_name=vault_name,
                checkpoint_id="fork-rewritten-context",
                checkpoint_kind="session_map",
                source="validation",
                message_count_before=2,
                last_message_sequence_index=1,
                summary_message=replacement_context,
                replacement_history=[replacement_context],
                metadata={"map_observed_through_sequence_index": 1},
            )

        def fetch_with_external_rewrite(
            conn: sqlite3.Connection,
            *,
            session_id: str,
            vault_name: str,
            through_sequence_index: int,
        ) -> list[StoredChatMessage]:
            nonlocal write_blocked
            prefix = original_fetch(
                conn,
                session_id=session_id,
                vault_name=vault_name,
                through_sequence_index=through_sequence_index,
            )
            try:
                rewrite_source()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc):
                    raise
                write_blocked = True
            return prefix

        with (
            patch.object(
                external_store,
                "_connect",
                side_effect=lambda: sqlite3.connect(database_path, timeout=0),
            ),
            patch.object(
                store,
                "_fetch_raw_messages_from_conn",
                side_effect=fetch_with_external_rewrite,
            ),
        ):
            store.fork_session(
                source_session_id=source_id,
                new_session_id=child_id,
                vault_name=vault_name,
                through_sequence_index=1,
                title="Consistent snapshot",
            )
        self.soft_assert(
            write_blocked, "A source writer must wait for the fork snapshot"
        )
        self.soft_assert_equal(
            store.get_history(child_id, vault_name, mode="raw"),
            original_messages,
            "A concurrent fork should preserve one canonical source snapshot",
        )
        self.soft_assert_equal(
            store.get_history(child_id, vault_name),
            [original_context],
            "Inherited context must describe the same snapshot as copied canonical history",
        )
        if write_blocked:
            rewrite_source()
        self.soft_assert_equal(
            store.get_history(source_id, vault_name, mode="raw"),
            replacement_messages,
            "The source writer should be able to complete after the fork commits",
        )
        self.soft_assert_equal(
            store.get_history(child_id, vault_name),
            [original_context],
            "A later source rewrite must not alter the child's inherited context",
        )

    def _test_reused_tool_event_lineage(
        self, store: ChatStore, vault_name: str
    ) -> None:
        source_id = "fork-reused-tool-id-source"
        child_id = "fork-reused-tool-id-child"
        store.ensure_session(
            source_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        for label in ("past", "future"):
            calls = [ToolCallPart(tool_name="probe", args={}, tool_call_id="reused")]
            returns = [
                ToolReturnPart(
                    tool_name="probe",
                    tool_call_id="reused",
                    content=f"{label} canonical result",
                )
            ]
            if label == "past":
                calls.append(
                    ToolCallPart(tool_name="probe", args={}, tool_call_id="unique")
                )
                returns.append(
                    ToolReturnPart(
                        tool_name="probe",
                        tool_call_id="unique",
                        content="Unique canonical result",
                    )
                )
            store.add_messages(
                source_id,
                vault_name,
                [
                    _user(label),
                    ModelResponse(parts=calls),
                    ModelRequest(parts=returns),
                    _assistant(f"{label} answer"),
                ],
            )
            store.add_tool_event(
                session_id=source_id,
                vault_name=vault_name,
                tool_call_id="reused",
                tool_name="probe",
                event_type="call",
                args={"label": label},
            )
            store.add_tool_event(
                session_id=source_id,
                vault_name=vault_name,
                tool_call_id="reused",
                tool_name="probe",
                event_type="result",
                result_text=f"{label} structured result",
            )
        store.add_tool_event(
            session_id=source_id,
            vault_name=vault_name,
            tool_call_id="unique",
            tool_name="probe",
            event_type="call",
            args={"label": "unique"},
        )
        store.add_tool_event(
            session_id=source_id,
            vault_name=vault_name,
            tool_call_id="unique",
            tool_name="probe",
            event_type="result",
            result_text="Unique structured result",
        )
        source_prefix = store.get_stored_messages_range(
            source_id, vault_name, after_sequence_index=-1, through_sequence_index=3
        )
        store.fork_session(
            source_session_id=source_id,
            new_session_id=child_id,
            vault_name=vault_name,
            through_sequence_index=3,
            title="Past invocation only",
        )
        self.soft_assert_equal(
            store.get_history(child_id, vault_name, mode="raw"),
            [message.message for message in source_prefix],
            "Ambiguous structured details must not prevent copying canonical tool history",
        )
        self.soft_assert_equal(
            store.get_tool_events_for_call(child_id, vault_name, "reused"),
            [],
            "Reused tool IDs must not carry future or ambiguous invocation details into a fork",
        )
        self.soft_assert_equal(
            len(store.get_tool_events(source_id, vault_name)),
            6,
            "Omitting ambiguous child details must preserve all parent event records",
        )
        self.soft_assert_equal(
            store.get_session_metadata(child_id, vault_name)["fork"][
                "copied_tool_event_count"
            ],
            2,
            "Fork lineage should report only events that were safely inherited",
        )
        self.soft_assert_equal(
            store.get_tool_events(child_id, vault_name),
            store.get_tool_events_for_call(source_id, vault_name, "unique"),
            "Unambiguous invocation details should remain intact in the child",
        )
        # A future in-flight or failed invocation can persist events before its
        # messages enter canonical history. Declaration counts alone cannot
        # establish event ownership for its reused ID.
        store.add_tool_event(
            session_id=source_id,
            vault_name=vault_name,
            tool_call_id="unique",
            tool_name="probe",
            event_type="call",
            args={"label": "uncommitted future invocation"},
        )
        store.add_tool_event(
            session_id=source_id,
            vault_name=vault_name,
            tool_call_id="unique",
            tool_name="probe",
            event_type="result",
            result_text="Uncommitted future result",
        )
        uncommitted_child_id = "fork-uncommitted-reused-tool-child"
        store.fork_session(
            source_session_id=source_id,
            new_session_id=uncommitted_child_id,
            vault_name=vault_name,
            through_sequence_index=3,
            title="Exclude uncommitted reused invocation",
        )
        self.soft_assert_equal(
            store.get_tool_events(uncommitted_child_id, vault_name),
            [],
            "Reused event IDs must fail closed even when the future invocation has no canonical declaration",
        )

    def _test_tool_event_group_lineage(self, store: ChatStore, vault_name: str) -> None:
        source_id = "fork-tool-event-groups-source"
        child_id = "fork-tool-event-groups-child"
        event_groups: dict[str, tuple[str, ...]] = {
            "interrupted": ("call",),
            "completed": ("call", "result"),
            "cached": ("call", "overflow_cached"),
            "missing": (),
            "orphan-result": ("result",),
            "orphan-cached": ("overflow_cached",),
            "result-before-call": ("result", "call"),
            "cached-before-call": ("overflow_cached", "call"),
            "duplicate-call": ("call", "call", "result"),
            "duplicate-result": ("call", "result", "result"),
            "duplicate-cached": ("call", "overflow_cached", "overflow_cached"),
            "mixed-terminals": ("call", "result", "overflow_cached"),
            "unknown-event": ("call", "unknown"),
            "mismatched-result-name": ("call", "result"),
            "mismatched-cached-name": ("call", "overflow_cached"),
        }
        safe_ids = {"interrupted", "completed", "cached"}
        store.ensure_session(
            source_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        canonical_messages = [
            _user("Check ordered tool event groups"),
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="probe", args={}, tool_call_id=call_id)
                    for call_id in event_groups
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="probe",
                        tool_call_id=call_id,
                        content=f"{call_id} canonical result",
                    )
                    for call_id in event_groups
                ]
            ),
            _assistant("All canonical tool results are present"),
        ]
        store.add_messages(source_id, vault_name, canonical_messages)
        for call_id, event_types in event_groups.items():
            for index, event_type in enumerate(event_types):
                store.add_tool_event(
                    session_id=source_id,
                    vault_name=vault_name,
                    tool_call_id=call_id,
                    tool_name=(
                        "different"
                        if call_id.startswith("mismatched-") and event_type != "call"
                        else "probe"
                    ),
                    event_type=event_type,
                    result_text=(
                        None
                        if event_type == "call"
                        else f"{call_id} diagnostic result {index}"
                    ),
                )
            response = self.call_api(
                f"/api/chat/sessions/{source_id}/tools/{call_id}?vault_name={vault_name}"
            )
            self.soft_assert_equal(
                response.status_code,
                200 if call_id in safe_ids else 404,
                f"Tool details should expose only ordered unambiguous events: {call_id}",
            )
        source_events = store.get_tool_events(source_id, vault_name)
        completed_events = store.get_tool_events_for_call(
            source_id, vault_name, "completed"
        )
        self.soft_assert(
            not tool_call_events_are_unambiguous(
                [
                    completed_events[0],
                    replace(completed_events[1], tool_call_id="different"),
                ]
            ),
            "The shared event-group boundary must reject mixed invocation IDs",
        )
        store.fork_session(
            source_session_id=source_id,
            new_session_id=child_id,
            vault_name=vault_name,
            through_sequence_index=3,
            title="Ordered event groups",
        )
        self.soft_assert_equal(
            store.get_history(child_id, vault_name, mode="raw"),
            canonical_messages,
            "Withholding ambiguous event groups must preserve complete canonical tool history",
        )
        self.soft_assert_equal(
            store.get_tool_events(child_id, vault_name),
            [event for event in source_events if event.tool_call_id in safe_ids],
            "Forks should inherit call-only, completed, and cached groups and omit every ambiguous group",
        )
        self.soft_assert_equal(
            store.get_session_metadata(child_id, vault_name)["fork"][
                "copied_tool_event_count"
            ],
            5,
            "Fork lineage should count only the five events in safe groups",
        )
        self.soft_assert_equal(
            store.get_tool_events(source_id, vault_name),
            source_events,
            "Filtering fork event groups must leave all source diagnostics intact",
        )
        for session_id in (source_id, child_id):
            response = self.call_api(
                f"/api/chat/sessions/{session_id}?vault_name={vault_name}"
            )
            assert response.status_code == 200, response.text
            self.soft_assert_equal(
                {item["tool_call_id"] for item in response.json()["tool_calls"]},
                safe_ids,
                "Session summaries and fork inheritance should share ordered event eligibility",
            )


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _map_context(text: str) -> ModelRequest:
    return ModelRequest(parts=[SystemPromptPart(content=text)])
