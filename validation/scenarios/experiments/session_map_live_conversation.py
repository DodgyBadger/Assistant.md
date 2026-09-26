"""Full-path live conversation probe for shadow session-map maintenance.

This scenario spends live model quota. Terra generates both the simulated user messages and the ordinary Assistant.md replies, while Jev gates map authoring. The assistant side always enters through the public chat-task API.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field
from pydantic_ai import Agent

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.identity import (  # noqa: E402
    LOCAL_USER_AUTHORITY,
    SYSTEM_AUTHORITY,
    use_execution_authority,
)
from core.llm.agents import collect_response  # noqa: E402
from core.llm.model_factory import build_model_instance  # noqa: E402
from core.llm.model_selection import ModelExecutionSpec  # noqa: E402
from core.llm.openai_auth import OPENAI_OAUTH_TOKEN_SECRET  # noqa: E402
from core.secrets.crypto import SECRET_KEY_ENV, SecretKeyring  # noqa: E402
from core.secrets.legacy_migration import DEFAULT_NAMESPACE  # noqa: E402
from core.secrets.service import EncryptedSecretsService  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402

MODEL_ALIAS = "gpt-mini"
DECISION_MODEL_ALIAS = "jev"
OPENAI_OAUTH_NAMESPACE = "oauth.openai"
TYPESAFE_SECRET_NAME = "TYPESAFE_API_KEY"
LOGFIRE_SECRET_NAME = "LOGFIRE_TOKEN"
DEFAULT_TURN_COUNT = 12
MEMORY_INTERVAL_TURNS = 3
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "timed_out", "skipped"}
SUCCESSFUL_MEMORY_TASK_STATUSES = {"completed", "skipped"}


class SimulatedUserMessage(BaseModel):
    """One natural user utterance generated from the staged scenario brief."""

    message: str = Field(min_length=3, max_length=1_500)


@dataclass(frozen=True)
class LiveSecrets:
    """Minimum live credentials copied into one isolated validation run."""

    openai_oauth_token: str
    typesafe_api_key: str
    logfire_token: str | None


class SessionMapLiveConversationScenario(BaseScenario):
    """Drive a real multi-turn chat using a separate Terra user simulator."""

    async def test_scenario(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source_system_root = Path(
            os.environ.get("SESSION_MAP_LIVE_SYSTEM_ROOT", repo_root / "system")
        )
        source_env_path = Path(
            os.environ.get("SESSION_MAP_LIVE_ENV", repo_root / ".env")
        )
        live_settings = _load_live_settings(source_system_root)
        live_secrets = _load_live_secrets(source_system_root, source_env_path)

        vault = self.create_vault("SessionMapLiveConversationVault")
        self.create_file(
            vault,
            "AssistantMD/soul.md",
            (
                "Keep ordinary chat replies focused and under 250 words unless the "
                "user explicitly requests a longer artifact. Preserve important "
                "constraints, corrections, decisions, and open questions.\n"
            ),
        )
        controller = self._get_system_controller()  # noqa: SLF001
        _configure_isolated_settings(controller, live_settings)
        _seed_isolated_secrets(controller, live_secrets)

        await self.start_system()
        transcript: list[dict[str, Any]] = []
        checkpoints: list[dict[str, Any]] = []
        session_id = "session-map-live-conversation"
        turn_count = _turn_count()

        try:
            simulator = _build_user_simulator()
            _assert_live_model_readiness(self, MODEL_ALIAS, DECISION_MODEL_ALIAS)

            for turn_index, brief in enumerate(TURN_BRIEFS[:turn_count], start=1):
                user_message = await _simulate_user_message(
                    simulator=simulator,
                    turn_index=turn_index,
                    brief=brief,
                    transcript=transcript,
                )
                assistant_result = await self.run_chat_task(
                    {
                        "vault_name": vault.name,
                        "prompt": user_message,
                        "session_id": session_id,
                        "tools": [],
                        "model": MODEL_ALIAS,
                        "thinking": "low",
                    },
                    timeout_seconds=300.0,
                )
                assert (
                    assistant_result["start_response"].status_code == 200
                ), f"Assistant turn {turn_index} should start through the chat-task API"
                terminal = assistant_result.get("terminal_event") or {}
                assert (
                    terminal.get("event") == "done"
                ), f"Assistant turn {turn_index} should complete, got {terminal}"
                assistant_text = str(assistant_result.get("text") or "").strip()
                assert assistant_text, f"Assistant turn {turn_index} should return text"
                transcript.append(
                    {
                        "turn": turn_index,
                        "brief": brief,
                        "user": user_message,
                        "assistant": assistant_text,
                        "chat_task_ids": assistant_result.get("task_ids", []),
                    }
                )
                _write_json(self.artifacts_dir / "conversation.json", transcript)

                if turn_index % MEMORY_INTERVAL_TURNS == 0:
                    checkpoint = await _wait_for_memory_checkpoint(
                        self,
                        vault_name=vault.name,
                        session_id=session_id,
                        expected_attempts=turn_index // MEMORY_INTERVAL_TURNS,
                    )
                    checkpoint["through_turn"] = turn_index
                    checkpoints.append(checkpoint)
                    _write_json(
                        self.artifacts_dir / "session_map_checkpoints.json",
                        checkpoints,
                    )

            final_map_response = self.call_api(
                f"/api/chat/sessions/{session_id}/map?vault_name={vault.name}"
            )
            assert (
                final_map_response.status_code == 200
            ), "Final durable session-map inspection should succeed"
            final_map = final_map_response.json()
            memory_tasks = _session_memory_tasks(self, session_id)
            probe_summary = _summarize_probe(
                final_map=final_map,
                memory_tasks=memory_tasks,
                expected_attempts=turn_count // MEMORY_INTERVAL_TURNS,
            )
            _write_json(self.artifacts_dir / "final_session_map.json", final_map)
            _write_json(self.artifacts_dir / "session_memory_tasks.json", memory_tasks)
            _write_json(self.artifacts_dir / "probe_summary.json", probe_summary)

            self.soft_assert_equal(
                len(transcript),
                turn_count,
                "Every simulated user turn should complete through the real chat path",
            )
            self.soft_assert(
                len(memory_tasks) >= turn_count // MEMORY_INTERVAL_TURNS,
                "Every eligible interval should create a tracked session-memory task",
            )
            self.soft_assert_equal(
                probe_summary["active_tasks"],
                [],
                "No tracked session-memory task should remain active",
            )
            self.soft_assert_equal(
                probe_summary["fatal_tasks"],
                [],
                "No tracked session-memory task should be cancelled or time out",
            )
            self.soft_assert(
                probe_summary["latest_attempt_succeeded"],
                "The latest memory attempt should succeed even if an earlier attempt recovered",
            )
            self.soft_assert(
                int(final_map.get("latest_revision") or 0) >= 1,
                "Consequential staged changes should produce a committed session map",
            )
            self.soft_assert(
                bool(final_map.get("session_map")),
                "The committed session map should be available through its product API",
            )
            self.soft_assert_equal(
                (final_map.get("maintenance") or {}).get("last_error"),
                None,
                "Final durable maintenance state should not retain an error",
            )
            self.soft_assert(
                probe_summary["pending_tail_is_bounded"],
                "Any tail accumulated during maintenance should remain below eligibility",
            )
        finally:
            await self.stop_system()

        self.teardown_scenario()
        self.assert_no_failures()


def _load_live_settings(system_root: Path) -> dict[str, Any]:
    settings_path = system_root / "settings.yaml"
    if not settings_path.exists():
        raise RuntimeError(f"Live settings file not found: {settings_path}")
    payload = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Live settings are not a mapping: {settings_path}")
    return payload


def _load_live_secrets(system_root: Path, env_path: Path) -> LiveSecrets:
    if not env_path.exists():
        raise RuntimeError(f"Live secret key environment file not found: {env_path}")
    env_values = {
        name: value
        for name, value in dotenv_values(env_path).items()
        if value is not None
    }
    with patch.dict(os.environ, env_values, clear=False):
        source_keyring = SecretKeyring.from_environment()
    source = EncryptedSecretsService(
        system_root=str(system_root),
        keyring=source_keyring,
        initialize_schema=False,
    )
    oauth_token = source.get_for_authority(
        LOCAL_USER_AUTHORITY,
        OPENAI_OAUTH_NAMESPACE,
        OPENAI_OAUTH_TOKEN_SECRET,
    )
    typesafe_key = source.get_for_authority(
        LOCAL_USER_AUTHORITY,
        DEFAULT_NAMESPACE,
        TYPESAFE_SECRET_NAME,
    )
    logfire_token = source.get_for_authority(
        SYSTEM_AUTHORITY,
        DEFAULT_NAMESPACE,
        LOGFIRE_SECRET_NAME,
    )
    if not oauth_token:
        raise RuntimeError("The live OpenAI OAuth connection is not available.")
    if not typesafe_key:
        raise RuntimeError("The live TypeSafe/Jev credential is not available.")
    return LiveSecrets(
        openai_oauth_token=_access_token_only(oauth_token),
        typesafe_api_key=typesafe_key,
        logfire_token=logfire_token,
    )


def _access_token_only(raw_token: str) -> str:
    payload = json.loads(raw_token)
    if not isinstance(payload, dict):
        raise RuntimeError("The live OpenAI OAuth token state is malformed.")
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at.strip():
        raise RuntimeError("The live OpenAI OAuth token has no expiry.")
    parsed_expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if parsed_expiry.tzinfo is None:
        parsed_expiry = parsed_expiry.replace(tzinfo=UTC)
    if parsed_expiry <= datetime.now(UTC) + timedelta(minutes=20):
        raise RuntimeError(
            "The live OpenAI OAuth access token expires too soon for this probe. "
            "Use the app once to refresh the connection, then rerun the scenario."
        )
    payload.pop("refresh_token", None)
    return json.dumps(payload, separators=(",", ":"))


def _configure_isolated_settings(controller: Any, live: dict[str, Any]) -> None:
    settings_path = controller._system_root / "settings.yaml"  # noqa: SLF001
    isolated = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    isolated.setdefault("settings", {})
    isolated.setdefault("models", {})
    isolated.setdefault("providers", {})
    live_settings = live.get("settings") or {}
    live_models = live.get("models") or {}
    live_providers = live.get("providers") or {}

    for alias in (MODEL_ALIAS, DECISION_MODEL_ALIAS):
        if alias not in live_models:
            raise RuntimeError(f"Live model alias is missing: {alias}")
        isolated["models"][alias] = copy.deepcopy(live_models[alias])
    for provider_name in ("openai", "typesafe"):
        if provider_name not in live_providers:
            raise RuntimeError(f"Live provider is missing: {provider_name}")
        isolated["providers"][provider_name] = copy.deepcopy(
            live_providers[provider_name]
        )

    copied_setting_names = {
        "default_context_script",
        "default_model_thinking",
        "openai_oauth_enabled",
        "live_session_memory_broad_change_threshold",
        "live_session_memory_field_change_threshold",
        "live_session_memory_max_pending_turns",
        "live_session_memory_max_pending_tokens",
        "live_session_memory_task_timeout_seconds",
        "live_session_memory_max_concurrent_tasks",
    }
    for name in copied_setting_names:
        if name in live_settings:
            isolated["settings"][name] = copy.deepcopy(live_settings[name])
    _set_setting(isolated, "default_model", MODEL_ALIAS)
    _set_setting(isolated, "live_session_memory_mode", "observe")
    _set_setting(isolated, "live_session_memory_decision_model", DECISION_MODEL_ALIAS)
    _set_setting(isolated, "live_session_memory_author_model", MODEL_ALIAS)
    _set_setting(
        isolated, "live_session_memory_eligibility_turns", MEMORY_INTERVAL_TURNS
    )
    settings_path.write_text(
        yaml.safe_dump(isolated, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def _set_setting(settings: dict[str, Any], name: str, value: Any) -> None:
    existing = settings["settings"].get(name)
    if isinstance(existing, dict):
        existing["value"] = value
    else:
        settings["settings"][name] = {"value": value}


def _seed_isolated_secrets(controller: Any, secrets: LiveSecrets) -> None:
    with patch.dict(
        os.environ,
        {SECRET_KEY_ENV: controller._validation_secret_key},  # noqa: SLF001
        clear=False,
    ):
        destination_keyring = SecretKeyring.from_environment()
    destination = EncryptedSecretsService(
        system_root=str(controller._system_root),  # noqa: SLF001
        keyring=destination_keyring,
    )
    destination.set_for_authority(
        LOCAL_USER_AUTHORITY,
        OPENAI_OAUTH_NAMESPACE,
        OPENAI_OAUTH_TOKEN_SECRET,
        secrets.openai_oauth_token,
    )
    destination.set_for_authority(
        LOCAL_USER_AUTHORITY,
        DEFAULT_NAMESPACE,
        TYPESAFE_SECRET_NAME,
        secrets.typesafe_api_key,
    )
    if secrets.logfire_token:
        destination.set_for_authority(
            SYSTEM_AUTHORITY,
            DEFAULT_NAMESPACE,
            LOGFIRE_SECRET_NAME,
            secrets.logfire_token,
        )


def _assert_live_model_readiness(
    scenario: BaseScenario, assistant_alias: str, decision_alias: str
) -> None:
    response = scenario.call_api("/api/metadata")
    assert response.status_code == 200, "Model metadata should be available"
    models = response.json().get("models", [])
    by_name = {item.get("name"): item for item in models if isinstance(item, dict)}
    assert (
        by_name.get(assistant_alias, {}).get("available") is True
    ), f"Assistant model should be ready: {assistant_alias}"
    assert (
        by_name.get(decision_alias, {}).get("available") is True
    ), f"Decision model should be ready: {decision_alias}"


def _build_user_simulator() -> Agent[None, SimulatedUserMessage]:
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        model = build_model_instance(MODEL_ALIAS, thinking="low")
    if isinstance(model, ModelExecutionSpec):
        raise RuntimeError("The simulated-user role requires a generative model.")
    return Agent(
        model,
        output_type=SimulatedUserMessage,
        name="session_map_user_simulator",
        instructions=(
            "Simulate the human user in a realistic ongoing chat. Produce exactly one "
            "natural next user message. Follow the current turn brief, react to the "
            "assistant's latest response, and preserve relevant established context. "
            "Do not mention the brief, testing, simulation, session maps, or memory. "
            "Do not answer as the assistant. Keep the message under 180 words."
        ),
    )


async def _simulate_user_message(
    *,
    simulator: Agent[None, SimulatedUserMessage],
    turn_index: int,
    brief: str,
    transcript: list[dict[str, Any]],
) -> str:
    visible_transcript = "\n\n".join(
        f"User: {item['user']}\nAssistant: {item['assistant']}" for item in transcript
    )
    prompt = (
        f"TURN {turn_index} BRIEF\n{brief}\n\n"
        "VISIBLE CONVERSATION\n"
        f"{visible_transcript or '[No prior turns]'}\n\n"
        "Write the next user message now."
    )
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        result = await collect_response(simulator, prompt)
    if not isinstance(result.output, SimulatedUserMessage):
        raise RuntimeError(
            f"Simulated user turn {turn_index} returned an unexpected output."
        )
    message = result.output.message.strip()
    if not message:
        raise RuntimeError(f"Simulated user turn {turn_index} was empty.")
    return message


async def _wait_for_memory_checkpoint(
    scenario: BaseScenario,
    *,
    vault_name: str,
    session_id: str,
    expected_attempts: int,
    timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_payload: dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        response = scenario.call_api(
            f"/api/chat/sessions/{session_id}/map?vault_name={vault_name}"
        )
        if response.status_code == 200:
            last_payload = response.json()
            maintenance = last_payload.get("maintenance") or {}
            attempt_count = int(maintenance.get("attempt_count") or 0)
            status = maintenance.get("status")
            tasks = _session_memory_tasks(scenario, session_id)
            terminal_tasks = [
                task for task in tasks if task.get("status") in TERMINAL_TASK_STATUSES
            ]
            if (
                attempt_count >= expected_attempts
                and status != "processing"
                and len(terminal_tasks) >= expected_attempts
            ):
                return {
                    "map": last_payload,
                    "memory_tasks": tasks,
                    "settled_attempt": terminal_tasks[-1],
                }
        await asyncio.sleep(0.25)
    raise TimeoutError(
        f"Session-memory checkpoint {expected_attempts} did not settle: {last_payload}"
    )


def _session_memory_tasks(
    scenario: BaseScenario, session_id: str
) -> list[dict[str, Any]]:
    response = scenario.call_api(
        "/api/tasks",
        params={
            "kind": "session_memory",
            "scope": f"session_memory:{session_id}",
            "include_terminal": True,
        },
    )
    assert response.status_code == 200, "Session-memory task listing should succeed"
    tasks = cast(list[dict[str, Any]], response.json().get("tasks", []))
    return sorted(tasks, key=lambda task: str(task.get("created_at") or ""))


def _summarize_probe(
    *,
    final_map: dict[str, Any],
    memory_tasks: list[dict[str, Any]],
    expected_attempts: int,
) -> dict[str, Any]:
    maintenance = final_map.get("maintenance") or {}
    terminal_tasks = [
        task for task in memory_tasks if task.get("status") in TERMINAL_TASK_STATUSES
    ]
    active_tasks = [
        task
        for task in memory_tasks
        if task.get("status") not in TERMINAL_TASK_STATUSES
    ]
    fatal_tasks = [
        task
        for task in memory_tasks
        if task.get("status") in {"cancelled", "timed_out"}
    ]
    failed_tasks = [task for task in memory_tasks if task.get("status") == "failed"]
    latest_task = terminal_tasks[-1] if terminal_tasks else None
    pending_turn_count = int(maintenance.get("pending_turn_count") or 0)
    return {
        "expected_attempts": expected_attempts,
        "actual_attempts": int(maintenance.get("attempt_count") or 0),
        "terminal_task_count": len(terminal_tasks),
        "active_tasks": active_tasks,
        "fatal_tasks": fatal_tasks,
        "failed_tasks": failed_tasks,
        "recovered_failure_count": (
            len(failed_tasks)
            if latest_task
            and latest_task.get("status") in SUCCESSFUL_MEMORY_TASK_STATUSES
            and maintenance.get("last_error") is None
            else 0
        ),
        "latest_attempt_succeeded": bool(
            latest_task
            and latest_task.get("status") in SUCCESSFUL_MEMORY_TASK_STATUSES
            and maintenance.get("last_error") is None
        ),
        "pending_tail_turns": pending_turn_count,
        "pending_tail_tokens": int(maintenance.get("pending_token_count") or 0),
        "pending_tail_is_bounded": pending_turn_count < MEMORY_INTERVAL_TURNS,
        "latest_revision": int(final_map.get("latest_revision") or 0),
    }


def _turn_count() -> int:
    raw = os.environ.get("SESSION_MAP_CONVERSATION_TURNS", str(DEFAULT_TURN_COUNT))
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            "SESSION_MAP_CONVERSATION_TURNS must be an integer."
        ) from exc
    if parsed < MEMORY_INTERVAL_TURNS or parsed > len(TURN_BRIEFS):
        raise RuntimeError(
            f"SESSION_MAP_CONVERSATION_TURNS must be between "
            f"{MEMORY_INTERVAL_TURNS} and {len(TURN_BRIEFS)}."
        )
    if parsed % MEMORY_INTERVAL_TURNS != 0:
        raise RuntimeError(
            "SESSION_MAP_CONVERSATION_TURNS must align with the three-turn checkpoint."
        )
    return parsed


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


TURN_BRIEFS = (
    "Open a new planning discussion. Ask for help designing a neighborhood heat-resilience pilot that can launch before next summer. Establish that success means a practical, fundable pilot rather than a broad strategy report.",
    "Add concrete constraints: a preliminary budget ceiling of $120,000, three candidate neighborhoods, and involvement from public health, parks, and two community organizations. Ask the assistant to keep the work focused.",
    "Clarify that the first deliverable should compare the three candidate sites using heat exposure, vulnerable population, implementation feasibility, and resident trust. Ask what evidence is still missing.",
    "Correct earlier information: finance has reduced the ceiling from $120,000 to $90,000, and the Riverside site must be removed because its land agreement will not be ready. Make clear these supersede the earlier assumptions.",
    "React to the assistant's latest response and settle on the two remaining sites for continued comparison. Ask for a concise decision path without pretending the final site has been selected.",
    "Introduce an urgent new artifact: a one-page council briefing is due Friday. It must explain the pilot, the reduced budget, the two-site shortlist, and the unresolved selection question.",
    "Add a strict content constraint: the briefing must not claim quantified energy or health savings because those estimates are not verified. Ask the assistant to distinguish known facts from assumptions.",
    "Pause community outreach planning because legal counsel is reviewing whether incentives can be offered. Keep site analysis and the council briefing active, and identify the legal review as the blocker.",
    "Report that legal counsel has conditionally cleared incentives up to $50 per household if participation is voluntary and privacy language is included. Resume outreach planning under those constraints.",
    "Shift the immediate priority to finishing the one-page council briefing. Ask for a draft structure and make clear that the site recommendation remains provisional pending one missing temperature dataset.",
    "Say that the briefing structure has been reviewed and accepted, but the temperature dataset will not arrive until next week. Ask what can be finalized now and what must remain explicitly open.",
    "Close the current planning phase: direct the assistant to treat the council briefing as ready for drafting, preserve the provisional two-site decision and the open temperature-data question, and summarize the next handoff without reopening retired assumptions.",
)
