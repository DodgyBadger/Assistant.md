"""Live comparison of recovery cards and stepped session maps.

This experiment reads one private local transcript, spends live OpenAI and Jev
quota, and writes its generated artifacts only under ignored validation runs.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.chat.compaction import estimate_history_tokens  # noqa: E402
from core.memory.session_map.checkpoints import (  # noqa: E402
    load_session_map_checkpoint,
)
from core.runtime.execution_tasks import (  # noqa: E402
    ExecutionTaskKind,
    chat_session_scope,
)
from core.secrets.crypto import (  # noqa: E402
    LEGACY_ACTIVE_KEY_VERSION_ENV,
    LEGACY_KEYRING_ENV,
)
from validation.core.base_scenario import (  # noqa: E402
    BaseScenario,
    with_local_user_authority,
)

SOURCE_SESSION_ENV = "SESSION_CONTEXT_COMPARISON_SOURCE_SESSION"
SOURCE_SESSION_ID = os.environ.get(
    SOURCE_SESSION_ENV,
    "Ashley_Personal_20260501_101403",
).strip()
SOURCE_DB_ENV = "SESSION_CONTEXT_COMPARISON_DB"
SOURCE_DB_DEFAULT = Path("system/chat_sessions.db")
CONDITIONS_ENV = "SESSION_CONTEXT_COMPARISON_CONDITIONS"
ALL_CONDITIONS = ("recovery_card", "unconditional_map", "gated_map")
BATCH_ENDS_ENV = "SESSION_CONTEXT_COMPARISON_BATCH_ENDS"
BATCH_ENDS = tuple(
    int(value.strip())
    for value in os.environ.get(BATCH_ENDS_ENV, "15,31,47,63,71").split(",")
    if value.strip()
)
MODEL_ALIAS = "gpt-mini"
MODEL_THINKING = "low"
GATE_MODEL_ALIAS = "jev"
_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)


class SessionContextStrategyComparisonScenario(BaseScenario):
    """Replay identical source batches through three context strategies."""

    @with_local_user_authority
    async def test_scenario(self) -> None:
        source_db = Path(os.environ.get(SOURCE_DB_ENV, SOURCE_DB_DEFAULT)).resolve()
        assert source_db.exists(), f"Private source database not found: {source_db}"
        source_messages = _load_source_messages(source_db)
        assert len(source_messages) == BATCH_ENDS[-1] + 1

        vault = self.create_vault("SessionContextStrategyComparisonVault")
        self._configure_live_runtime()
        await self.start_system()

        try:
            results: dict[str, Any] = {}
            conditions = _selected_conditions()
            for condition in conditions:
                self._log_timeline(f"Starting comparison condition: {condition}")
                results[condition] = await self._run_condition(
                    condition=condition,
                    messages=source_messages,
                    vault_name=vault.name,
                    vault_path=str(vault),
                )
            _assert_expected_task_paths(
                results,
                conditions=conditions,
                reduction_count=len(BATCH_ENDS),
            )
            artifact = {
                "source_session_id": SOURCE_SESSION_ID,
                "source_message_count": len(source_messages),
                "batch_ends": list(BATCH_ENDS),
                "author_model": MODEL_ALIAS,
                "author_thinking": MODEL_THINKING,
                "gate_model": GATE_MODEL_ALIAS,
                "gate_threshold": 0.5,
                "selected_conditions": list(conditions),
                "conditions": results,
            }
            (self.artifacts_dir / "comparison.json").write_text(
                json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            (self.artifacts_dir / "comparison.md").write_text(
                _render_summary(artifact),
                encoding="utf-8",
            )
        finally:
            await self.stop_system()

        self.teardown_scenario()
        self.assert_no_failures()

    def _configure_live_runtime(self) -> None:
        controller = self._get_system_controller()
        live_root = Path("system").resolve()
        live_settings = (
            yaml.safe_load((live_root / "settings.yaml").read_text(encoding="utf-8"))
            or {}
        )
        isolated_settings_path = (
            controller._system_root / "settings.yaml"
        )  # noqa: SLF001
        isolated_settings = (
            yaml.safe_load(isolated_settings_path.read_text(encoding="utf-8")) or {}
        )
        isolated_settings.setdefault("settings", {})
        isolated_settings.setdefault("models", {})
        isolated_settings.setdefault("providers", {})

        live_general = live_settings.get("settings", {})
        for key in ("default_model", "openai_oauth_enabled"):
            if key in live_general:
                isolated_settings["settings"][key] = live_general[key]
        isolated_settings["settings"]["default_model"]["value"] = MODEL_ALIAS
        for alias in (MODEL_ALIAS, GATE_MODEL_ALIAS):
            isolated_settings["models"][alias] = live_settings["models"][alias]
        for provider in ("openai", "typesafe"):
            isolated_settings["providers"][provider] = live_settings["providers"][
                provider
            ]
        isolated_settings_path.write_text(
            yaml.safe_dump(isolated_settings, sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )

        _copy_sqlite_database(
            live_root / "access.db", controller._system_root / "access.db"
        )  # noqa: SLF001
        controller._validation_secret_key = _active_live_secret_key(  # noqa: SLF001
            controller._original_secret_key  # noqa: SLF001
        )

    async def _run_condition(
        self,
        *,
        condition: str,
        messages: list[ModelMessage],
        vault_name: str,
        vault_path: str,
    ) -> dict[str, Any]:
        from core.chat.compaction import maybe_auto_compact_after_turn
        from core.identity import LOCAL_USER_PRINCIPAL_ID
        from core.runtime.state import get_runtime_context

        await self._set_condition_settings(condition)
        runtime = get_runtime_context()
        store = runtime.chat_store
        session_id = f"context-strategy-comparison-{condition}"
        store.ensure_session(
            session_id,
            vault_name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        if condition != "recovery_card":
            from core.memory.session_map.readiness import (
                evaluate_session_map_readiness,
            )

            readiness = evaluate_session_map_readiness(
                store=store,
                session_id=session_id,
                vault_name=vault_name,
            )
            assert (
                readiness.enabled
            ), f"{condition} is not eligible for map reduction: {readiness.reason}"

        checkpoints: list[dict[str, Any]] = []
        prior_end = -1
        for batch_number, batch_end in enumerate(BATCH_ENDS, start=1):
            store.add_messages(
                session_id,
                vault_name,
                messages[prior_end + 1 : batch_end + 1],
            )
            raw_before = store.get_history(session_id, vault_name, mode="raw") or []
            effective_before = store.get_history(session_id, vault_name) or []
            result = await maybe_auto_compact_after_turn(
                session_id=session_id,
                vault_name=vault_name,
                vault_path=vault_path,
            )
            assert (
                result is not None
            ), f"{condition} batch {batch_number} did not reduce"
            effective_after = store.get_history(session_id, vault_name) or []
            checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
            assert checkpoint is not None
            checkpoint_metadata = json.loads(checkpoint.metadata_json or "{}")
            record: dict[str, Any] = {
                "batch": batch_number,
                "source_end": batch_end,
                "raw_message_count": len(raw_before),
                "effective_messages_before": len(effective_before),
                "effective_tokens_before": estimate_history_tokens(effective_before),
                "effective_messages_after": len(effective_after),
                "effective_tokens_after": estimate_history_tokens(effective_after),
                "artifact_tokens": estimate_history_tokens(effective_after[:1]),
                "tail_messages": max(0, len(effective_after) - 1),
                "checkpoint_kind": checkpoint.checkpoint_kind,
                "checkpoint_boundary": checkpoint.last_message_sequence_index,
                "map_observed_through": checkpoint_metadata.get(
                    "map_observed_through_sequence_index"
                ),
                "result": asdict(result),
                "artifact_text": _message_text(effective_after[0]),
                "tail_text": [
                    _message_text(message) for message in effective_after[1:]
                ],
            }
            if checkpoint.checkpoint_kind == "session_map":
                draft = load_session_map_checkpoint(checkpoint)
                record["map"] = draft.model_dump(mode="json")
                record["map_reference_status"] = _map_reference_status(
                    draft.model_dump(mode="json"),
                    raw_count=len(raw_before),
                )
            checkpoints.append(record)
            prior_end = batch_end

        tasks = await runtime.task_coordinator.list_tasks(
            scope=chat_session_scope(session_id),
            include_terminal=True,
        )
        relevant_tasks = [
            _task_summary(task)
            for task in tasks
            if task.kind
            in {
                ExecutionTaskKind.HISTORY_COMPACTION.value,
                ExecutionTaskKind.SESSION_MAP_AUTHORING.value,
                ExecutionTaskKind.SESSION_MAP_CLASSIFICATION.value,
            }
        ]
        raw_final = store.get_history(session_id, vault_name, mode="raw") or []
        return {
            "session_id": session_id,
            "checkpoints": checkpoints,
            "tasks": relevant_tasks,
            "raw_messages_preserved": len(raw_final) == len(messages),
            "raw_message_count": len(raw_final),
        }

    async def _set_condition_settings(self, condition: str) -> None:
        values: dict[str, object] = {
            "default_model": MODEL_ALIAS,
            "compaction_type": "auto",
            "compaction_keep_recent": 2,
            "compaction_token_threshold": 1000,
            "session_map_author_model": MODEL_ALIAS,
            "session_map_author_thinking": MODEL_THINKING,
            "session_map_low_watermark_tokens": 1,
            "session_map_gate_threshold": 0.5,
            "session_map_gate_max_input_tokens": 24000,
        }
        if condition == "recovery_card":
            values["context_reduction_strategy"] = "recovery_card"
            values["session_map_gate_model"] = "none"
        elif condition == "unconditional_map":
            values["context_reduction_strategy"] = "stepped_session_map"
            values["session_map_gate_model"] = "none"
        else:
            values["context_reduction_strategy"] = "stepped_session_map"
            values["session_map_gate_model"] = GATE_MODEL_ALIAS
        for key, value in values.items():
            response = self.call_api(
                f"/api/system/settings/general/{key}",
                method="PUT",
                data={"value": str(value)},
            )
            assert (
                response.status_code == 200
            ), f"Could not set {key}={value!r}: {response.text}"


def _load_source_messages(source_db: Path) -> list[ModelMessage]:
    with sqlite3.connect(source_db) as connection:
        rows = connection.execute(
            """
            SELECT sequence_index, message_json
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY sequence_index ASC
            """,
            (SOURCE_SESSION_ID,),
        ).fetchall()
    assert [int(row[0]) for row in rows] == list(range(BATCH_ENDS[-1] + 1))
    return [_MESSAGE_ADAPTER.validate_json(str(row[1])) for row in rows]


def _copy_sqlite_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
        source_db.backup(target_db)


def _active_live_secret_key(original_primary: str | None) -> str:
    if original_primary:
        return original_primary
    keyring = json.loads(os.environ[LEGACY_KEYRING_ENV])
    active = os.environ[LEGACY_ACTIVE_KEY_VERSION_ENV]
    raw = str(keyring[active])
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    assert len(decoded) == 32
    return base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")


def _message_text(message: ModelMessage) -> str:
    parts: list[str] = []
    for part in getattr(message, "parts", ()) or ():
        content = getattr(part, "content", None)
        if content is not None:
            parts.append(str(content))
    return "\n".join(parts)


def _map_reference_status(payload: dict[str, Any], *, raw_count: int) -> dict[str, Any]:
    ranges = [
        source
        for entry in payload.get("entries", [])
        for source in entry.get("sources", [])
    ]
    invalid = [
        source
        for source in ranges
        if source["start"] < 0
        or source["end"] < source["start"]
        or source["end"] >= raw_count
    ]
    return {
        "range_count": len(ranges),
        "invalid_ranges": invalid,
        "all_resolve": not invalid,
    }


def _task_summary(task: Any) -> dict[str, Any]:
    duration_seconds = None
    if task.started_at is not None and task.finished_at is not None:
        duration_seconds = (task.finished_at - task.started_at).total_seconds()
    result = task.result if isinstance(task.result, dict) else {}
    return {
        "task_id": task.task_id,
        "kind": task.kind,
        "status": task.status,
        "duration_seconds": duration_seconds,
        "metadata": task.metadata,
        "result": result,
    }


def _render_summary(artifact: dict[str, Any]) -> str:
    lines = [
        "# Session context strategy comparison",
        "",
        f"Source messages: {artifact['source_message_count']}",
        f"Batch ends: {artifact['batch_ends']}",
        "",
    ]
    for name, condition in artifact["conditions"].items():
        tasks = condition["tasks"]
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task["kind"]] = counts.get(task["kind"], 0) + 1
        lines.extend(
            [
                f"## {name}",
                "",
                f"- Raw messages preserved: {condition['raw_messages_preserved']}",
                f"- Task counts: {counts}",
                "- Checkpoints:",
                "",
            ]
        )
        for checkpoint in condition["checkpoints"]:
            lines.append(
                f"  - Batch {checkpoint['batch']} through {checkpoint['source_end']}: "
                f"{checkpoint['effective_tokens_before']} -> "
                f"{checkpoint['effective_tokens_after']} effective tokens; "
                f"artifact {checkpoint['artifact_tokens']} tokens; "
                f"action {checkpoint['result'].get('action', checkpoint['result'].get('status'))}."
            )
        lines.append("")
    return "\n".join(lines)


def _selected_conditions() -> tuple[str, ...]:
    configured = os.environ.get(CONDITIONS_ENV, "").strip()
    if not configured:
        return ALL_CONDITIONS
    selected = tuple(
        condition.strip() for condition in configured.split(",") if condition.strip()
    )
    unknown = sorted(set(selected) - set(ALL_CONDITIONS))
    if unknown:
        raise ValueError(f"Unknown comparison conditions: {', '.join(unknown)}")
    if not selected:
        raise ValueError("At least one comparison condition is required")
    return selected


def _assert_expected_task_paths(
    results: dict[str, Any],
    *,
    conditions: tuple[str, ...] = ALL_CONDITIONS,
    reduction_count: int = len(BATCH_ENDS),
) -> None:
    expected = {
        "recovery_card": {ExecutionTaskKind.HISTORY_COMPACTION.value: reduction_count},
        "unconditional_map": {
            ExecutionTaskKind.SESSION_MAP_AUTHORING.value: reduction_count
        },
    }
    for condition in conditions:
        actual: dict[str, int] = {}
        for task in results[condition]["tasks"]:
            kind = str(task["kind"])
            actual[kind] = actual.get(kind, 0) + 1
        if condition != "gated_map":
            assert (
                actual == expected[condition]
            ), f"{condition} used unexpected governed task paths: {actual}"
            continue
        expected_classifications = max(0, reduction_count - 1)
        assert actual.get(ExecutionTaskKind.SESSION_MAP_CLASSIFICATION.value, 0) == (
            expected_classifications
        ), f"gated_map used an unexpected classifier path: {actual}"
        author_count = actual.get(ExecutionTaskKind.SESSION_MAP_AUTHORING.value, 0)
        assert (
            1 <= author_count <= reduction_count
        ), f"gated_map used an unexpected author path: {actual}"
        assert set(actual) <= {
            ExecutionTaskKind.SESSION_MAP_AUTHORING.value,
            ExecutionTaskKind.SESSION_MAP_CLASSIFICATION.value,
        }, f"gated_map used an unexpected governed task kind: {actual}"
