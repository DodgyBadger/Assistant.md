"""Keep consumed retrieval envelopes outside citable session-map evidence."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    NativeToolReturnPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.chat.chat_store import ChatStore  # noqa: E402
from core.chat.compaction import maybe_auto_compact_after_turn  # noqa: E402
from core.identity import (  # noqa: E402
    LOCAL_USER_AUTHORITY,
    LOCAL_USER_PRINCIPAL_ID,
    use_execution_authority,
)
from core.memory.session_map.checkpoints import (  # noqa: E402
    build_session_map_context_message,
    commit_session_map_context_checkpoint,
    load_session_map_checkpoint,
)
from core.memory.session_map.evidence import (  # noqa: E402
    SessionMapEvidence,
    build_session_map_evidence,
    resolve_previous_map_excluded_sources,
)
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SessionMapProvenanceError,
    SessionMapTrajectory,
    SourceRange,
    validate_session_map_provenance,
)
from core.memory.session_map.retained_evidence import (  # noqa: E402
    project_retained_session_map_evidence,
    project_retrieved_session_map_evidence,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SessionMapEvidenceAdmissionScenario(BaseScenario):
    """Exercise projection, inherited citations, and real reduction together."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("SessionMapEvidenceAdmissionVault")
        await self.start_system()
        for key, value in (
            ("compaction_strategy", "session_map"),
            ("compaction_author_model", "test"),
            ("compaction_author_thinking", "low"),
            ("compaction_low_watermark_tokens", "1"),
            ("compaction_retained_turns", "1"),
            ("compaction_high_watermark_tokens", "2"),
            ("compaction_type", "auto"),
        ):
            response = self.call_api(
                f"/api/system/settings/general/{key}",
                method="PUT",
                data={"value": value},
            )
            assert response.status_code == 200
        store = get_runtime_context().chat_store
        self._test_message_projection(store, vault.name)
        await self._test_evicted_windows_and_legacy_map(store, vault.name, str(vault))
        self.assert_no_failures()

    def _test_message_projection(self, store: ChatStore, vault_name: str) -> None:
        session_id = "map-evidence-message-variants"
        store.ensure_session(
            session_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        retrieval_text = "An unrelated retrieved recommendation must not become state."
        store.add_messages(
            session_id,
            vault_name,
            [
                _user("Eligible user fact."),
                ModelRequest(parts=[_return("pure", retrieval_text)]),
                ModelRequest(
                    parts=[
                        UserPromptPart(content="Eligible mixed user fact."),
                        _return("mixed-user", retrieval_text),
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="vault_ops",
                            tool_call_id="ordinary",
                            content="Eligible observed file.",
                        ),
                        _return("mixed-tool", retrieval_text),
                    ]
                ),
                ModelResponse(
                    parts=[
                        NativeToolReturnPart(
                            tool_name="session_ops", content=retrieval_text
                        )
                    ]
                ),
                ModelResponse(
                    parts=[
                        TextPart(content="Eligible mixed assistant finding."),
                        NativeToolReturnPart(
                            tool_name="session_ops", content=retrieval_text
                        ),
                    ]
                ),
            ],
        )
        raw = store.get_stored_messages(session_id, vault_name, mode="raw")
        envelope = build_session_map_evidence(
            session_id=session_id,
            vault_name=vault_name,
            history_revision=store.get_session_history_revision(session_id, vault_name),
            stored_messages=raw,
            model_messages=[message.message for message in raw],
        )
        assert (
            envelope.source_start_sequence_index,
            envelope.source_end_sequence_index,
            envelope.message_count,
        ) == (0, 5, 6)
        assert (
            envelope.source_digest
            == hashlib.sha256(
                "\n".join(message.message_json for message in raw).encode()
            ).hexdigest()
        )
        assert envelope.citable_source_ranges == (
            SourceRange(start=0, end=0),
            SourceRange(start=2, end=3),
            SourceRange(start=5, end=5),
        )
        assert retrieval_text not in envelope.projected_text
        retained = project_retained_session_map_evidence(raw)
        assert [message.sequence_index for message in retained] == [0, 2, 3, 5]
        assert retrieval_text not in "\n".join(
            message.content_text for message in retained
        )
        assert "Eligible observed file." in envelope.projected_text
        assert "Eligible mixed assistant finding." in envelope.projected_text
        valid = _draft(
            SourceRange(start=0, end=0),
            SourceRange(start=2, end=3),
            SourceRange(start=5, end=5),
        )
        validate_session_map_provenance(valid, evidence=(envelope,))
        for source in (
            SourceRange(start=1, end=1),
            SourceRange(start=4, end=4),
            SourceRange(start=0, end=5),
        ):
            _assert_rejected(_draft(source), evidence=(envelope,))

        # Legacy maps may have used the retrieval portion of a mixed message.
        previous = _draft(SourceRange(start=0, end=5))
        excluded = resolve_previous_map_excluded_sources(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            previous_map=previous,
        )
        assert excluded == tuple(
            SourceRange(start=index, end=index) for index in (1, 2, 3, 4, 5)
        )
        validate_session_map_provenance(
            _draft(SourceRange(start=0, end=0)),
            evidence=(),
            previous_map=previous,
            excluded_source_ranges=excluded,
        )
        _assert_rejected(
            _draft(SourceRange(start=2, end=2)),
            evidence=(),
            previous_map=previous,
            excluded=excluded,
        )
        # Explicitly supplied sanitized text permits a fresh mixed-message citation.
        validate_session_map_provenance(
            _draft(SourceRange(start=2, end=3)),
            evidence=retained,
            previous_map=previous,
            excluded_source_ranges=excluded,
        )
        _assert_rejected(
            _draft(SourceRange(start=1, end=1)),
            evidence=(envelope,),
            previous_map=previous,
            excluded=excluded,
        )

        # Even exact canonical windows must not recursively admit native returns.
        native = raw[4]
        payload = _window(session_id, vault_name, 4, native.role, native.content_text)
        store.add_messages(
            session_id,
            vault_name,
            [
                ModelResponse(
                    parts=[
                        NativeToolReturnPart(
                            tool_name="session_ops",
                            content=_window(
                                session_id,
                                vault_name,
                                0,
                                raw[0].role,
                                raw[0].content_text,
                            ),
                        )
                    ]
                ),
                ModelRequest(parts=[_return("recursive", payload)]),
            ],
        )
        retrieval = project_retrieved_session_map_evidence(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            retained=store.get_stored_messages(session_id, vault_name, mode="raw")[-2:],
        )
        assert [message.sequence_index for message in retrieval.messages] == [0]

        # A checkpoint authored under verified admission can keep its safe mixed
        # citations after those canonical rows have left all authoring evidence.
        current_revision = store.get_session_history_revision(session_id, vault_name)
        current_envelope = build_session_map_evidence(
            session_id=session_id,
            vault_name=vault_name,
            history_revision=current_revision,
            stored_messages=raw,
            model_messages=[message.message for message in raw],
        )
        committed = commit_session_map_context_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            draft=valid,
            previous_map=SessionMapDraft(),
            new_evidence=(current_envelope,),
            expected_history_revision=current_revision,
            message_count_before=8,
            source="validation",
        )
        assert (
            json.loads(committed.checkpoint.metadata_json or "{}")[
                "evidence_admission_version"
            ]
            == 1
        )
        exclusions = resolve_previous_map_excluded_sources(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            previous_map=valid,
        )
        assert exclusions == ()
        validate_session_map_provenance(
            valid, evidence=(), previous_map=valid, excluded_source_ranges=exclusions
        )
        # A marker belongs to its persisted map, not an arbitrary prior payload.
        different_previous = _draft(SourceRange(start=2, end=2))
        exclusions = resolve_previous_map_excluded_sources(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            previous_map=different_previous,
        )
        assert exclusions == (SourceRange(start=2, end=2),)
        _assert_rejected(
            different_previous,
            evidence=(),
            previous_map=different_previous,
            excluded=exclusions,
        )
        corrupt_previous = _draft(SourceRange(start=1, end=1))
        exclusions = resolve_previous_map_excluded_sources(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            previous_map=corrupt_previous,
        )
        assert exclusions == (SourceRange(start=1, end=1),)
        _assert_rejected(
            corrupt_previous,
            evidence=(),
            previous_map=corrupt_previous,
            excluded=exclusions,
        )

    async def _test_evicted_windows_and_legacy_map(
        self, store: ChatStore, vault_name: str, vault_path: str
    ) -> None:
        session_id = "map-evidence-legacy-eviction"
        store.ensure_session(
            session_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        original = "Historical exact evidence remains available."
        fragment = original[11:25]
        payload = _window(session_id, vault_name, 0, "user", fragment)
        payload["messages"][0].update(
            content_start=11, content_end=25, content_complete=False
        )
        store.add_messages(
            session_id,
            vault_name,
            [
                _user(original),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="session_ops", args={}, tool_call_id="old-return"
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        _return("old-return", "An unrelated session recommendation.")
                    ]
                ),
                _assistant("Historical turn complete."),
                _user("Recover the historical source."),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="session_ops", args={}, tool_call_id="window"
                        )
                    ]
                ),
                ModelRequest(parts=[_return("window", payload)]),
                _assistant("The selected source fragment was recovered."),
                _user("Continue using the verified historical evidence."),
                _assistant("The current turn is complete."),
            ],
        )
        legacy = SessionMapDraft(
            schema_version=1,
            entries=(
                SessionMapEntry(
                    id="legacy_retrieval_claim",
                    kind="finding",
                    state="active",
                    basis="tool_observed",
                    text="An unrelated session recommendation.",
                    sources=(SourceRange(start=2, end=2),),
                ),
            ),
        )
        context = build_session_map_context_message(legacy)
        store.add_context_checkpoint(
            session_id=session_id,
            vault_name=vault_name,
            checkpoint_id="legacy-map",
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=4,
            last_message_sequence_index=3,
            summary_message=context,
            replacement_history=[context],
            replacement_source_sequence_indexes=[None],
            metadata={
                "map": legacy.model_dump(mode="json"),
                "map_observed_through_sequence_index": 3,
            },
        )
        raw_before = store.get_history(session_id, vault_name, mode="raw")
        raw = store.get_stored_messages(session_id, vault_name, mode="raw")
        new_evidence = build_session_map_evidence(
            session_id=session_id,
            vault_name=vault_name,
            history_revision=store.get_session_history_revision(session_id, vault_name),
            stored_messages=raw[4:8],
            model_messages=[message.message for message in raw[4:8]],
        )
        for index in (2, 6):
            try:
                commit_session_map_context_checkpoint(
                    store=store,
                    session_id=session_id,
                    vault_name=vault_name,
                    draft=_draft(SourceRange(start=index, end=index)),
                    previous_map=legacy,
                    new_evidence=(new_evidence,),
                    expected_history_revision=new_evidence.history_revision,
                    message_count_before=7,
                    source="validation",
                )
            except SessionMapProvenanceError:
                pass
            else:
                raise AssertionError(
                    "Checkpoint commit must independently reject excluded citations"
                )

        async def invalid_author(**kwargs: object) -> SessionMapDraft:
            author_payload = json.loads(str(kwargs["prompt"]))
            assert author_payload["excluded_source_ranges"] == [{"start": 2, "end": 2}]
            return _draft(SourceRange(start=2, end=2))

        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=invalid_author,
        ):
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                rejected = await maybe_auto_compact_after_turn(
                    session_id=session_id, vault_name=vault_name, vault_path=vault_path
                )
        assert rejected is None
        assert len(store.list_context_checkpoints(session_id, vault_name)) == 1
        assert store.get_history(session_id, vault_name, mode="raw") == raw_before

        valid = _draft(SourceRange(start=0, end=0), SourceRange(start=8, end=8))

        async def valid_author(**kwargs: object) -> SessionMapDraft:
            author_payload = json.loads(str(kwargs["prompt"]))
            assert author_payload["excluded_source_ranges"] == [{"start": 2, "end": 2}]
            envelopes = author_payload["new_evidence_envelopes"]
            assert envelopes[0]["source_range"] == {"start": 4, "end": 7}
            assert envelopes[0]["citable_source_ranges"] == [
                {"start": 4, "end": 5},
                {"start": 7, "end": 7},
            ]
            assert "[source:6]" not in envelopes[0]["projected_text"]
            retrieved = author_payload["retrieved_canonical_evidence"]
            assert [
                (
                    message["sequence_index"],
                    message["content"],
                    message["content_start"],
                    message["content_end"],
                    message["content_complete"],
                )
                for message in retrieved
            ] == [(0, fragment, 11, 25, False)]
            return valid

        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=valid_author,
        ):
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                reduced = await maybe_auto_compact_after_turn(
                    session_id=session_id, vault_name=vault_name, vault_path=vault_path
                )
        assert reduced is not None
        latest = store.get_latest_context_checkpoint(session_id, vault_name)
        assert latest is not None
        assert latest.last_message_sequence_index == 7
        assert latest.observed_through_sequence_index == 9
        assert load_session_map_checkpoint(latest) == valid
        assert store.get_history(session_id, vault_name, mode="raw") == raw_before

        # A retired gate may have consumed a window before its evidence rewrite.
        pending_context = build_session_map_context_message(valid)
        store.add_context_checkpoint(
            session_id=session_id,
            vault_name=vault_name,
            checkpoint_id="pending-window-map",
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=7,
            last_message_sequence_index=7,
            summary_message=pending_context,
            replacement_history=[pending_context],
            replacement_source_sequence_indexes=[None],
            metadata={
                "map": valid.model_dump(mode="json"),
                "map_observed_through_sequence_index": 9,
                "pending_evidence": {
                    "start_sequence_index": 4,
                    "end_sequence_index": 7,
                    "source_digest": new_evidence.source_digest,
                    "estimated_tokens": new_evidence.estimated_tokens,
                },
            },
        )
        store.add_messages(
            session_id,
            vault_name,
            [
                _user("Continue the next complete turn."),
                _assistant("The next turn is complete."),
            ],
        )

        async def repair_author(**kwargs: object) -> SessionMapDraft:
            author_payload = json.loads(str(kwargs["prompt"]))
            assert [
                (item["source_range"]["start"], item["source_range"]["end"])
                for item in author_payload["new_evidence_envelopes"]
            ] == [(4, 7), (8, 9)]
            assert [
                (item["sequence_index"], item["content"])
                for item in author_payload["retrieved_canonical_evidence"]
            ] == [(0, fragment)]
            return valid

        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=repair_author,
        ):
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                repaired = await maybe_auto_compact_after_turn(
                    session_id=session_id, vault_name=vault_name, vault_path=vault_path
                )
        assert repaired is not None
        latest = store.get_latest_context_checkpoint(session_id, vault_name)
        assert latest is not None
        assert latest.last_message_sequence_index == 9
        assert latest.observed_through_sequence_index == 11


def _draft(*sources: SourceRange) -> SessionMapDraft:
    return SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="A supported historical finding guides current work.", sources=sources
        ),
        entries=(
            SessionMapEntry(
                id="supported_finding",
                kind="finding",
                state="active",
                basis="tool_observed",
                text="Supported historical evidence.",
                sources=sources,
            ),
        ),
    )


def _assert_rejected(
    draft: SessionMapDraft,
    *,
    evidence: tuple[SessionMapEvidence, ...],
    previous_map: SessionMapDraft | None = None,
    excluded: tuple[SourceRange, ...] = (),
) -> None:
    try:
        validate_session_map_provenance(
            draft,
            evidence=evidence,
            previous_map=previous_map,
            excluded_source_ranges=excluded,
        )
    except SessionMapProvenanceError:
        return
    raise AssertionError("Excluded retrieval-envelope citations must fail closed")


def _window(
    session_id: str, vault_name: str, sequence_index: int, role: str, content: str
) -> dict[str, object]:
    return {
        "operation": "get_transcript_window",
        "status": "ok",
        "session_id": session_id,
        "vault_name": vault_name,
        "messages": [
            {"sequence_index": sequence_index, "role": role, "content": content}
        ],
    }


def _return(tool_call_id: str, content: object) -> ToolReturnPart:
    return ToolReturnPart(
        tool_name="session_ops", tool_call_id=tool_call_id, content=content
    )


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
