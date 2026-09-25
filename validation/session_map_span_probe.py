"""Private-data span screening for offline session-map authoring experiments."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
from core.llm.thinking import ThinkingValue, normalize_thinking_value
from core.memory.session_map.authoring import (
    SESSION_MAP_AUTHORING_PROMPT_VERSION,
    CanonicalMapMessage,
    SessionMapAuthoringRequest,
    author_session_map_patch,
    build_session_map_authoring_prompt,
)
from core.memory.session_map.models import SessionMap, render_session_map
from core.runtime.paths import set_bootstrap_roots
from core.secrets import initialize_secrets_bootstrap
from core.tools.utils import estimate_token_count

SOURCE_PROJECTION_VERSION = "canonical-visible-parts-v1"
DEFAULT_PRIVATE_DATABASE = Path(
    "validation/data/live_session_memory/ashley_personal_sessions.sqlite3"
)
DEFAULT_OUTPUT_DIRECTORY = Path("validation/data/live_session_memory/results")
DEFAULT_SESSION_ID = "Ashley_Personal_20260429_103553"
DEFAULT_VAULT_NAME = "Ashley_Personal"
DEFAULT_CHECKPOINT_COUNT = 3
SMALL_TARGET_TOKENS = 15_000
MEDIUM_TARGET_TOKENS = 40_000

PartKind = Literal[
    "user-prompt",
    "text",
    "tool-call",
    "tool-return",
    "thinking",
]
MapUpdateMode = Literal["incremental", "rebase"]


@dataclass(frozen=True)
class SnapshotCheckpoint:
    checkpoint_id: int
    last_message_sequence_index: int
    coverage_through_sequence_index: int
    keep_recent: int
    reason: str
    effective_tokens_before: int | None
    summary_message_json: str


@dataclass(frozen=True)
class ProjectedSnapshotMessage:
    message: CanonicalMapMessage
    estimated_tokens: int
    excluded_part_kinds: tuple[str, ...]


@dataclass(frozen=True)
class SpanBoundary:
    from_sequence_index: int
    through_sequence_index: int
    message_count: int
    estimated_source_tokens: int
    source_characters: int


@dataclass(frozen=True)
class SpanRegime:
    """Source boundaries plus the map-state policy used for every batch."""

    boundaries: tuple[SpanBoundary, ...]
    update_mode: MapUpdateMode


def load_projected_messages(
    database: Path,
    *,
    session_id: str,
    vault_name: str,
    through_sequence_index: int,
) -> tuple[ProjectedSnapshotMessage, ...]:
    """Load one exact raw prefix and project its visible canonical parts."""
    with _read_only_connection(database) as connection:
        rows = connection.execute(
            """
            SELECT sequence_index, role, message_json
            FROM chat_messages
            WHERE session_id = ? AND vault_name = ? AND sequence_index <= ?
            ORDER BY sequence_index ASC
            """,
            (session_id, vault_name, through_sequence_index),
        ).fetchall()
    indexes = tuple(int(row[0]) for row in rows)
    if indexes != tuple(range(through_sequence_index + 1)):
        raise ValueError("private snapshot prefix is not canonically contiguous")
    return tuple(_project_snapshot_row(row) for row in rows)


def load_checkpoints(
    database: Path,
    *,
    session_id: str,
    vault_name: str,
) -> tuple[SnapshotCheckpoint, ...]:
    """Load natural compaction boundaries without reading effective history."""
    with _read_only_connection(database) as connection:
        rows = connection.execute(
            """
            SELECT id, last_message_sequence_index, metadata_json,
                   summary_message_json
            FROM chat_compaction_checkpoints
            WHERE session_id = ? AND vault_name = ?
            ORDER BY id ASC
            """,
            (session_id, vault_name),
        ).fetchall()
    checkpoints = []
    for checkpoint_id, last_index, metadata_json, summary_json in rows:
        metadata = json.loads(str(metadata_json or "{}"))
        keep_recent = int(metadata.get("compaction_keep_recent", 8))
        checkpoints.append(
            SnapshotCheckpoint(
                checkpoint_id=int(checkpoint_id),
                last_message_sequence_index=int(last_index),
                coverage_through_sequence_index=int(last_index) - keep_recent,
                keep_recent=keep_recent,
                reason=str(metadata.get("reason") or "unknown"),
                effective_tokens_before=_optional_int(
                    metadata.get("estimated_tokens_before")
                ),
                summary_message_json=str(summary_json),
            )
        )
    return tuple(checkpoints)


def partition_by_target_tokens(
    messages: tuple[ProjectedSnapshotMessage, ...],
    *,
    target_tokens: int,
) -> tuple[SpanBoundary, ...]:
    """Partition a contiguous prefix without splitting or dropping a message."""
    if target_tokens <= 0:
        raise ValueError("target_tokens must be positive")
    if not messages:
        return ()
    boundaries: list[SpanBoundary] = []
    batch: list[ProjectedSnapshotMessage] = []
    batch_tokens = 0
    for projected in messages:
        if batch and batch_tokens + projected.estimated_tokens > target_tokens:
            boundaries.append(_span_boundary(batch))
            batch = []
            batch_tokens = 0
        batch.append(projected)
        batch_tokens += projected.estimated_tokens
    if batch:
        boundaries.append(_span_boundary(batch))
    return tuple(boundaries)


def natural_boundaries(
    messages: tuple[ProjectedSnapshotMessage, ...],
    checkpoints: tuple[SnapshotCheckpoint, ...],
) -> tuple[SpanBoundary, ...]:
    """Partition a prefix at its natural mapped compaction boundaries."""
    by_index = {item.message.sequence_index: item for item in messages}
    boundaries: list[SpanBoundary] = []
    start = 0
    for checkpoint in checkpoints:
        end = checkpoint.coverage_through_sequence_index
        batch = [by_index[index] for index in range(start, end + 1)]
        boundaries.append(_span_boundary(batch))
        start = end + 1
    if start != messages[-1].message.sequence_index + 1:
        raise ValueError("natural checkpoints do not cover the selected prefix")
    return tuple(boundaries)


def cumulative_checkpoint_boundaries(
    messages: tuple[ProjectedSnapshotMessage, ...],
    checkpoints: tuple[SnapshotCheckpoint, ...],
) -> tuple[SpanBoundary, ...]:
    """Build a complete source prefix ending at every selected checkpoint."""
    by_index = {item.message.sequence_index: item for item in messages}
    boundaries: list[SpanBoundary] = []
    for checkpoint in checkpoints:
        end = checkpoint.coverage_through_sequence_index
        batch = [by_index[index] for index in range(0, end + 1)]
        boundaries.append(_span_boundary(batch))
    if not boundaries or boundaries[-1].through_sequence_index != (
        messages[-1].message.sequence_index
    ):
        raise ValueError("checkpoint prefixes do not cover the selected source")
    return tuple(boundaries)


def build_regime_boundaries(
    messages: tuple[ProjectedSnapshotMessage, ...],
    checkpoints: tuple[SnapshotCheckpoint, ...],
) -> dict[str, SpanRegime]:
    """Build the predeclared span regimes over one identical source prefix."""
    return {
        "small_15k": SpanRegime(
            boundaries=partition_by_target_tokens(
                messages, target_tokens=SMALL_TARGET_TOKENS
            ),
            update_mode="incremental",
        ),
        "medium_40k": SpanRegime(
            boundaries=partition_by_target_tokens(
                messages, target_tokens=MEDIUM_TARGET_TOKENS
            ),
            update_mode="incremental",
        ),
        "natural_compaction": SpanRegime(
            boundaries=natural_boundaries(messages, checkpoints),
            update_mode="incremental",
        ),
        "full_prefix_rebase": SpanRegime(
            boundaries=cumulative_checkpoint_boundaries(messages, checkpoints),
            update_mode="rebase",
        ),
        "full_prefix": SpanRegime(
            boundaries=(_span_boundary(list(messages)),),
            update_mode="rebase",
        ),
    }


async def run_span_regime(
    *,
    regime_name: str,
    boundaries: tuple[SpanBoundary, ...],
    update_mode: MapUpdateMode,
    projected_messages: tuple[ProjectedSnapshotMessage, ...],
    session_id: str,
    model_alias: str,
    thinking: ThinkingValue,
) -> dict[str, Any]:
    """Author maps under an explicit incremental or from-empty rebase policy."""
    by_index = {
        item.message.sequence_index: item.message for item in projected_messages
    }
    current = SessionMap.empty(
        session_id=f"span-probe-{session_id}-{regime_name}",
        created_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    records: list[dict[str, Any]] = []
    for batch_index, boundary in enumerate(boundaries):
        if update_mode == "rebase":
            current = SessionMap.empty(
                session_id=f"span-probe-{session_id}-{regime_name}",
                created_at=datetime(2026, 9, 25, tzinfo=UTC),
            )
        delta = tuple(
            by_index[index]
            for index in range(
                boundary.from_sequence_index,
                boundary.through_sequence_index + 1,
            )
        )
        request = SessionMapAuthoringRequest(
            current_map=current,
            delta=delta,
            observed_source_content_revision=batch_index + 1,
        )
        prompt_tokens = estimate_token_count(
            build_session_map_authoring_prompt(request)
        )
        try:
            result = await author_session_map_patch(
                model_alias=model_alias,
                thinking=thinking,
                request=request,
                created_at=datetime(2026, 9, 25, tzinfo=UTC)
                + timedelta(seconds=batch_index + 1),
            )
        except Exception as exc:
            records.append(
                {
                    "batch_index": batch_index,
                    "boundary": asdict(boundary),
                    "prompt_token_estimate": prompt_tokens,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
            break
        current = result.session_map
        rendered = render_session_map(current, max_chars=20_000)
        records.append(
            {
                "batch_index": batch_index,
                "boundary": asdict(boundary),
                "prompt_token_estimate": prompt_tokens,
                "status": "applied",
                "patch_set": result.patch_set.model_dump(mode="json"),
                "session_map": current.model_dump(mode="json"),
                "entry_count": len(rendered.included_entry_ids),
                "rendered_characters": len(rendered.text),
                "requested_model_alias": result.requested_model_alias,
                "requested_thinking": result.requested_thinking,
                "resolved_model_name": result.resolved_model_name,
                "provider_name": result.provider_name,
                "latency_seconds": result.latency_seconds,
                "requests": result.requests,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            }
        )
    return {
        "regime": regime_name,
        "update_mode": update_mode,
        "records": records,
        "completed": len(records) == len(boundaries)
        and all(record["status"] == "applied" for record in records),
    }


def _project_snapshot_row(row: tuple[Any, ...]) -> ProjectedSnapshotMessage:
    sequence_index, stored_role, raw_message_json = row
    payload = json.loads(str(raw_message_json))
    parts = payload.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError(f"message {sequence_index} has no canonical parts")
    rendered_parts: list[str] = []
    visible_kinds: list[str] = []
    excluded_kinds: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError(f"message {sequence_index} contains a non-object part")
        kind = str(part.get("part_kind") or "unknown")
        if kind == "thinking":
            excluded_kinds.append(kind)
            continue
        visible_kinds.append(kind)
        rendered_parts.append(_render_visible_part(part, kind=kind))
    if not rendered_parts:
        raise ValueError(f"message {sequence_index} has no admissible visible parts")
    role: Literal["user", "assistant", "tool"]
    if visible_kinds and set(visible_kinds) == {"tool-return"}:
        role = "tool"
    elif str(stored_role) == "assistant":
        role = "assistant"
    else:
        role = "user"
    content = "\n\n".join(rendered_parts)
    message = CanonicalMapMessage(
        sequence_index=int(sequence_index),
        role=role,
        content=content,
    )
    return ProjectedSnapshotMessage(
        message=message,
        estimated_tokens=estimate_token_count(content),
        excluded_part_kinds=tuple(excluded_kinds),
    )


def _render_visible_part(part: dict[str, Any], *, kind: str) -> str:
    if kind in {"user-prompt", "text"}:
        return _render_value(part.get("content"))
    if kind == "tool-call":
        name = str(part.get("tool_name") or "tool")
        call_id = str(part.get("tool_call_id") or "unknown")
        return f"[tool call: {name}; id={call_id}] {_render_value(part.get('args'))}"
    if kind == "tool-return":
        name = str(part.get("tool_name") or "tool")
        call_id = str(part.get("tool_call_id") or "unknown")
        return (
            f"[tool result: {name}; id={call_id}] "
            f"{_render_value(part.get('content'))}"
        )
    raise ValueError(f"unsupported canonical part kind '{kind}'")


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _span_boundary(batch: list[ProjectedSnapshotMessage]) -> SpanBoundary:
    if not batch:
        raise ValueError("span boundary cannot be empty")
    return SpanBoundary(
        from_sequence_index=batch[0].message.sequence_index,
        through_sequence_index=batch[-1].message.sequence_index,
        message_count=len(batch),
        estimated_source_tokens=sum(item.estimated_tokens for item in batch),
        source_characters=sum(len(item.message.content) for item in batch),
    )


def _read_only_connection(database: Path) -> sqlite3.Connection:
    resolved = database.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"private session database not found: {resolved}")
    return sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _source_fingerprint(messages: tuple[ProjectedSnapshotMessage, ...]) -> str:
    digest = hashlib.sha256()
    for projected in messages:
        digest.update(projected.message.model_dump_json().encode("utf-8"))
    return digest.hexdigest()


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_PRIVATE_DATABASE)
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    parser.add_argument("--vault-name", default=DEFAULT_VAULT_NAME)
    parser.add_argument(
        "--checkpoint-count", type=int, default=DEFAULT_CHECKPOINT_COUNT
    )
    parser.add_argument("--model-alias", default="gpt-mini")
    parser.add_argument("--thinking", default="low")
    parser.add_argument(
        "--regime",
        choices=(
            "inventory",
            "small_15k",
            "medium_40k",
            "natural_compaction",
            "full_prefix_rebase",
            "full_prefix",
            "all",
        ),
        default="inventory",
    )
    parser.add_argument(
        "--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_arguments()
    checkpoints = load_checkpoints(
        args.database,
        session_id=args.session_id,
        vault_name=args.vault_name,
    )[: args.checkpoint_count]
    if not checkpoints:
        raise SystemExit("selected session has no compaction checkpoints")
    through_index = checkpoints[-1].coverage_through_sequence_index
    messages = load_projected_messages(
        args.database,
        session_id=args.session_id,
        vault_name=args.vault_name,
        through_sequence_index=through_index,
    )
    regimes = build_regime_boundaries(messages, checkpoints)
    excluded_counts: dict[str, int] = {}
    for projected in messages:
        for kind in projected.excluded_part_kinds:
            excluded_counts[kind] = excluded_counts.get(kind, 0) + 1
    private_checkpoints = [
        {
            **asdict(checkpoint),
            "recovery_card_token_estimate": estimate_token_count(
                checkpoint.summary_message_json
            ),
        }
        for checkpoint in checkpoints
    ]
    manifest = {
        "source_projection_version": SOURCE_PROJECTION_VERSION,
        "prompt_contract_version": SESSION_MAP_AUTHORING_PROMPT_VERSION,
        "database_name": args.database.name,
        "session_id": args.session_id,
        "vault_name": args.vault_name,
        "source_fingerprint": _source_fingerprint(messages),
        "through_sequence_index": through_index,
        "message_count": len(messages),
        "estimated_source_tokens": sum(item.estimated_tokens for item in messages),
        "source_characters": sum(len(item.message.content) for item in messages),
        "excluded_part_counts": excluded_counts,
        "checkpoints": [
            {
                **checkpoint,
                "summary_message_json": None,
            }
            for checkpoint in private_checkpoints
        ],
        "regimes": {
            name: {
                "update_mode": regime.update_mode,
                "boundaries": [asdict(boundary) for boundary in regime.boundaries],
            }
            for name, regime in regimes.items()
        },
    }
    print(json.dumps(manifest, indent=2, sort_keys=True))
    if args.regime == "inventory":
        return

    root = Path.cwd()
    load_dotenv(root / ".env")
    set_bootstrap_roots(root / "data", root / "system")
    secrets_status = initialize_secrets_bootstrap(root / "system")
    if not secrets_status.ready:
        raise SystemExit(f"secrets bootstrap failed: {secrets_status.reason}")
    from core.logger import UnifiedLogger

    logger = UnifiedLogger(
        tag="session-map-span-probe",
        default_sinks=["logfire"],
    )
    logger.info(
        "Starting private session-map span probe",
        data={
            "session_id": args.session_id,
            "regime": args.regime,
            "model_alias": args.model_alias,
            "thinking": args.thinking,
            "through_sequence_index": through_index,
        },
    )
    thinking = normalize_thinking_value(args.thinking, source_name="--thinking")
    selected = regimes if args.regime == "all" else {args.regime: regimes[args.regime]}
    results = []
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        for name, regime in selected.items():
            result = await run_span_regime(
                regime_name=name,
                boundaries=regime.boundaries,
                update_mode=regime.update_mode,
                projected_messages=messages,
                session_id=args.session_id,
                model_alias=args.model_alias,
                thinking=thinking,
            )
            results.append(result)
            print(
                json.dumps(
                    {
                        "regime": name,
                        "update_mode": regime.update_mode,
                        "completed": result["completed"],
                        "batches": [
                            {
                                "status": record["status"],
                                "through_sequence_index": record["boundary"][
                                    "through_sequence_index"
                                ],
                                "prompt_token_estimate": record[
                                    "prompt_token_estimate"
                                ],
                                "input_tokens": record.get("input_tokens"),
                                "output_tokens": record.get("output_tokens"),
                                "entry_count": record.get("entry_count"),
                                "rendered_characters": record.get(
                                    "rendered_characters"
                                ),
                                "latency_seconds": record.get("latency_seconds"),
                                "error_type": record.get("error_type"),
                                "error_message": record.get("error_message"),
                            }
                            for record in result["records"]
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
    args.output_directory.mkdir(parents=True, exist_ok=True)
    output_path = args.output_directory / (
        f"session_map_span_{args.session_id}_{args.regime}_"
        f"{args.model_alias}_{args.thinking}.json"
    )
    output_path.write_text(
        json.dumps(
            {
                "manifest": {
                    **manifest,
                    "checkpoints": private_checkpoints,
                },
                "model_alias": args.model_alias,
                "thinking": args.thinking,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Private result written to ignored path: {output_path}")


if __name__ == "__main__":
    asyncio.run(_main())
