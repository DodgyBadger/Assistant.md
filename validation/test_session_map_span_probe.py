"""Deterministic contracts for private session-map span partitioning."""

import json
from datetime import UTC, datetime

import pytest

import validation.session_map_span_probe as span_probe
from core.memory.session_map.authoring import SessionMapAuthoringResult
from core.memory.session_map.models import MapPatchSet, NoopPatch
from validation.session_map_span_probe import (
    ProjectedSnapshotMessage,
    SnapshotCheckpoint,
    _project_snapshot_row,
    cumulative_checkpoint_boundaries,
    partition_by_target_tokens,
    run_span_regime,
)


def _projected(index: int, tokens: int) -> ProjectedSnapshotMessage:
    item = _project_snapshot_row(
        (
            index,
            "user",
            json.dumps(
                {
                    "kind": "request",
                    "parts": [
                        {
                            "part_kind": "user-prompt",
                            "content": "evidence",
                        }
                    ],
                }
            ),
        )
    )
    return ProjectedSnapshotMessage(
        message=item.message,
        estimated_tokens=tokens,
        excluded_part_kinds=item.excluded_part_kinds,
    )


def test_projection_preserves_visible_tool_data_and_excludes_thinking() -> None:
    projected = _project_snapshot_row(
        (
            4,
            "assistant",
            json.dumps(
                {
                    "kind": "response",
                    "parts": [
                        {"part_kind": "thinking", "content": "private"},
                        {
                            "part_kind": "tool-call",
                            "tool_name": "read_file",
                            "tool_call_id": "call-1",
                            "args": {"path": "notes.md"},
                        },
                    ],
                }
            ),
        )
    )
    assert projected.message.role == "assistant"
    assert "read_file" in projected.message.content
    assert "notes.md" in projected.message.content
    assert "private" not in projected.message.content
    assert projected.excluded_part_kinds == ("thinking",)

    tool_result = _project_snapshot_row(
        (
            5,
            "user",
            json.dumps(
                {
                    "kind": "request",
                    "parts": [
                        {
                            "part_kind": "tool-return",
                            "tool_name": "read_file",
                            "tool_call_id": "call-1",
                            "content": {"status": "ok", "lines": 3},
                        }
                    ],
                }
            ),
        )
    )
    assert tool_result.message.role == "tool"
    assert '"status":"ok"' in tool_result.message.content


def test_token_partition_keeps_contiguous_oversized_messages_intact() -> None:
    boundaries = partition_by_target_tokens(
        tuple(
            _projected(index, tokens) for index, tokens in enumerate((4, 4, 15, 4, 4))
        ),
        target_tokens=10,
    )
    assert [
        (
            boundary.from_sequence_index,
            boundary.through_sequence_index,
            boundary.estimated_source_tokens,
        )
        for boundary in boundaries
    ] == [(0, 1, 8), (2, 2, 15), (3, 4, 8)]


def test_checkpoint_rebase_boundaries_repeat_complete_prefix() -> None:
    messages = tuple(_projected(index, 10) for index in range(6))
    checkpoints = tuple(
        SnapshotCheckpoint(
            checkpoint_id=index,
            last_message_sequence_index=coverage + 2,
            coverage_through_sequence_index=coverage,
            keep_recent=2,
            reason="threshold",
            effective_tokens_before=None,
            summary_message_json="{}",
        )
        for index, coverage in enumerate((2, 5), start=1)
    )

    boundaries = cumulative_checkpoint_boundaries(messages, checkpoints)

    assert [
        (
            boundary.from_sequence_index,
            boundary.through_sequence_index,
            boundary.message_count,
            boundary.estimated_source_tokens,
        )
        for boundary in boundaries
    ] == [(0, 2, 3, 30), (0, 5, 6, 60)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("update_mode", "boundaries", "expected_predecessors"),
    (
        (
            "incremental",
            ((0, 1), (2, 3)),
            ((0, -1), (1, 1)),
        ),
        (
            "rebase",
            ((0, 1), (0, 3)),
            ((0, -1), (0, -1)),
        ),
    ),
)
async def test_span_regime_uses_declared_map_update_mode(
    monkeypatch: pytest.MonkeyPatch,
    update_mode: span_probe.MapUpdateMode,
    boundaries: tuple[tuple[int, int], ...],
    expected_predecessors: tuple[tuple[int, int], ...],
) -> None:
    messages = tuple(_projected(index, 10) for index in range(4))
    source_boundaries = tuple(
        span_probe._span_boundary(list(messages[start : end + 1]))
        for start, end in boundaries
    )
    predecessors: list[tuple[int, int]] = []

    async def fake_author(**kwargs: object) -> SessionMapAuthoringResult:
        request = kwargs["request"]
        assert isinstance(request, span_probe.SessionMapAuthoringRequest)
        predecessors.append(
            (
                request.current_map.revision,
                request.current_map.updated_through_sequence_index,
            )
        )
        patch_set = MapPatchSet(
            expected_revision=request.current_map.revision,
            through_sequence_index=request.through_sequence_index,
            observed_source_content_revision=request.observed_source_content_revision,
            operations=(NoopPatch(reason="test"),),
        )
        updated = request.current_map.model_copy(
            update={
                "revision": request.current_map.revision + 1,
                "updated_through_sequence_index": request.through_sequence_index,
                "observed_source_content_revision": (
                    request.observed_source_content_revision
                ),
                "updated_at": datetime(2026, 9, 25, tzinfo=UTC),
                "authoring_status": "complete",
            }
        )
        return SessionMapAuthoringResult(
            patch_set=patch_set,
            session_map=updated,
            requested_model_alias="test-model",
            requested_thinking="low",
            resolved_model_name="test-model",
            provider_name="test-provider",
            latency_seconds=0,
            requests=1,
            input_tokens=0,
            output_tokens=0,
        )

    monkeypatch.setattr(span_probe, "author_session_map_patch", fake_author)

    result = await run_span_regime(
        regime_name="test",
        boundaries=source_boundaries,
        update_mode=update_mode,
        projected_messages=messages,
        session_id="session",
        model_alias="test-model",
        thinking="low",
    )

    assert result["completed"] is True
    assert result["update_mode"] == update_mode
    assert tuple(predecessors) == expected_predecessors
