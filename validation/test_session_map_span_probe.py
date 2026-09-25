"""Deterministic contracts for private session-map span partitioning."""

import json

from validation.session_map_span_probe import (
    ProjectedSnapshotMessage,
    _project_snapshot_row,
    partition_by_target_tokens,
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
