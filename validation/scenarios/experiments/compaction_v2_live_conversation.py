"""Exercise repeated Compaction v2 checkpoints through the normal live chat path.

This opt-in scenario spends live model quota. Terra generates the simulated user
messages, ordinary AssistantMD replies, and each Compaction v2 session map. The
assistant side always enters through the public chat-task API.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field
from pydantic_ai import Agent

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.chat.compaction import estimate_history_tokens  # noqa: E402
from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority  # noqa: E402
from core.llm.agents import collect_response  # noqa: E402
from core.llm.model_factory import build_model_instance  # noqa: E402
from core.llm.model_selection import ModelExecutionSpec  # noqa: E402
from core.llm.openai_auth import OPENAI_OAUTH_TOKEN_SECRET  # noqa: E402
from core.runtime.state import get_runtime_context  # noqa: E402
from core.secrets.crypto import SECRET_KEY_ENV, SecretKeyring  # noqa: E402
from core.secrets.service import EncryptedSecretsService  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402

MODEL_ALIAS = "gpt-mini"
OPENAI_OAUTH_NAMESPACE = "oauth.openai"
SESSION_ID = "compaction-v2-live-conversation"
HIGH_WATERMARK_TOKENS = 2_200
LOW_WATERMARK_TOKENS = 800


class SimulatedUserMessage(BaseModel):
    """One natural user utterance generated from the staged scenario brief."""

    message: str = Field(min_length=3, max_length=1_500)


@dataclass(frozen=True)
class LiveSecrets:
    """Minimum live credentials copied into one isolated validation run."""

    openai_oauth_token: str


class CompactionV2LiveConversationScenario(BaseScenario):
    """Drive a real conversation through several structured compactions."""

    async def test_scenario(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source_system_root = Path(
            os.environ.get("COMPACTION_V2_LIVE_SYSTEM_ROOT", repo_root / "system")
        )
        source_env_path = Path(
            os.environ.get("COMPACTION_V2_LIVE_ENV", repo_root / ".env")
        )
        live_settings = _load_live_settings(source_system_root)
        live_secrets = _load_live_secrets(source_system_root, source_env_path)

        vault = self.create_vault("CompactionV2LiveConversationVault")
        self.create_file(
            vault,
            "AssistantMD/soul.md",
            "Keep replies focused and under 250 words. Preserve corrections, decisions, constraints, artifacts, blockers, and open questions.\n",
        )
        controller = self._get_system_controller()  # noqa: SLF001
        _configure_isolated_settings(controller, live_settings)
        _seed_isolated_secrets(controller, live_secrets)

        await self.start_system()
        transcript: list[dict[str, Any]] = []
        checkpoints: list[dict[str, Any]] = []
        last_revision_count = 0
        try:
            simulator = _build_user_simulator()
            _assert_live_model_readiness(self)

            for turn_index, brief in enumerate(TURN_BRIEFS, start=1):
                user_message = await _simulate_user_message(
                    simulator=simulator,
                    turn_index=turn_index,
                    brief=brief,
                    transcript=transcript,
                )
                assistant_text, task_ids = await _run_assistant_turn(
                    self,
                    vault_name=vault.name,
                    user_message=user_message,
                    turn_index=turn_index,
                )
                transcript.append(
                    {
                        "turn": turn_index,
                        "brief": brief,
                        "user": user_message,
                        "assistant": assistant_text,
                        "chat_task_ids": task_ids,
                    }
                )
                last_revision_count = _capture_new_checkpoints(
                    self,
                    vault_name=vault.name,
                    through_turn=turn_index,
                    previous_revision_count=last_revision_count,
                    checkpoints=checkpoints,
                )
                _write_json(self.artifacts_dir / "conversation.json", transcript)
                _write_json(
                    self.artifacts_dir / "compaction_checkpoints.json", checkpoints
                )

            for audit_index, prompt in enumerate(AUDIT_PROMPTS, start=1):
                turn_index = len(TURN_BRIEFS) + audit_index
                assistant_text, task_ids = await _run_assistant_turn(
                    self,
                    vault_name=vault.name,
                    user_message=prompt,
                    turn_index=turn_index,
                )
                transcript.append(
                    {
                        "turn": turn_index,
                        "brief": "fixed retrospective audit",
                        "user": prompt,
                        "assistant": assistant_text,
                        "chat_task_ids": task_ids,
                    }
                )
                last_revision_count = _capture_new_checkpoints(
                    self,
                    vault_name=vault.name,
                    through_turn=turn_index,
                    previous_revision_count=last_revision_count,
                    checkpoints=checkpoints,
                )
                _write_json(self.artifacts_dir / "conversation.json", transcript)
                _write_json(
                    self.artifacts_dir / "compaction_checkpoints.json", checkpoints
                )

            final_map_response = self.call_api(
                f"/api/chat/sessions/{SESSION_ID}/map?vault_name={vault.name}"
            )
            assert final_map_response.status_code == 200
            final_map = final_map_response.json()
            runtime = get_runtime_context()
            store = runtime.chat_store
            raw_history = store.get_history(SESSION_ID, vault.name, mode="raw") or []
            effective_history = store.get_history(SESSION_ID, vault.name) or []
            tasks = [
                {
                    "task_id": task.task_id,
                    "kind": task.kind,
                    "scope": task.scope,
                    "status": task.status,
                    "terminal_reason": task.terminal_reason,
                    "terminal_error_type": task.terminal_error_type,
                    "metadata": task.metadata,
                    "result": task.result,
                }
                for task in await runtime.task_coordinator.list_tasks(
                    kind="session_map_authoring"
                )
            ]
            summary = {
                "conversation_turns": len(transcript),
                "checkpoint_count": len(final_map.get("revisions") or []),
                "raw_message_count": len(raw_history),
                "effective_message_count": len(effective_history),
                "raw_estimated_tokens": estimate_history_tokens(raw_history),
                "effective_estimated_tokens": estimate_history_tokens(
                    effective_history
                ),
                "author_task_statuses": [task.get("status") for task in tasks],
                "latest_entry_count": len(
                    (final_map.get("session_map") or {}).get("entries") or []
                ),
                "latest_trajectory_chars": len(
                    str(
                        (
                            (final_map.get("session_map") or {}).get("trajectory") or {}
                        ).get("text")
                        or ""
                    )
                ),
            }
            _write_json(self.artifacts_dir / "final_session_map.json", final_map)
            _write_json(self.artifacts_dir / "session_map_authoring_tasks.json", tasks)
            _write_json(self.artifacts_dir / "probe_summary.json", summary)

            self.soft_assert_equal(
                len(transcript),
                len(TURN_BRIEFS) + len(AUDIT_PROMPTS),
                "Every simulated and audit turn should complete through the chat API",
            )
            self.soft_assert(
                summary["checkpoint_count"] >= 2,
                "The live conversation should cross at least two Compaction v2 boundaries",
            )
            self.soft_assert(
                bool(final_map.get("session_map")),
                "The latest Compaction v2 map should remain inspectable",
            )
            self.soft_assert(
                summary["latest_trajectory_chars"] > 0,
                "The latest Compaction v2 checkpoint should include a narrative trajectory",
            )
            self.soft_assert(
                summary["raw_message_count"] >= len(transcript) * 2,
                "Every completed turn and any tool traffic should remain in canonical raw history",
            )
            self.soft_assert(
                summary["effective_message_count"] < summary["raw_message_count"],
                "Compaction v2 should reduce effective history without deleting raw messages",
            )
            self.soft_assert(
                bool(tasks)
                and all(task.get("status") == "completed" for task in tasks),
                "Every Compaction v2 authoring task should complete through the task executor",
            )
        finally:
            await self.stop_system()

        self.teardown_scenario()
        self.assert_no_failures()


async def _run_assistant_turn(
    scenario: BaseScenario,
    *,
    vault_name: str,
    user_message: str,
    turn_index: int,
) -> tuple[str, list[str]]:
    result = await scenario.run_chat_task(
        {
            "vault_name": vault_name,
            "prompt": user_message,
            "session_id": SESSION_ID,
            "tools": [],
            "model": MODEL_ALIAS,
            "thinking": "low",
        },
        timeout_seconds=300.0,
    )
    assert result["start_response"].status_code == 200
    terminal = result.get("terminal_event") or {}
    assert terminal.get("event") == "done", f"Turn {turn_index} failed: {terminal}"
    assistant_text = str(result.get("text") or "").strip()
    assert assistant_text, f"Assistant turn {turn_index} should return text"
    return assistant_text, list(result.get("task_ids") or [])


def _capture_new_checkpoints(
    scenario: BaseScenario,
    *,
    vault_name: str,
    through_turn: int,
    previous_revision_count: int,
    checkpoints: list[dict[str, Any]],
) -> int:
    response = scenario.call_api(
        f"/api/chat/sessions/{SESSION_ID}/map?vault_name={vault_name}"
    )
    assert response.status_code == 200
    payload = response.json()
    revisions = list(payload.get("revisions") or [])
    if len(revisions) > previous_revision_count:
        checkpoints.append(
            {
                "through_turn": through_turn,
                "revision_count": len(revisions),
                "latest_revision": revisions[-1],
                "session_map": payload.get("session_map"),
            }
        )
    return len(revisions)


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
    if not oauth_token:
        raise RuntimeError("The live OpenAI OAuth connection is not available.")
    return LiveSecrets(openai_oauth_token=_access_token_only(oauth_token))


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
            "The live OpenAI OAuth access token expires too soon. Use the app once to refresh it, then rerun the scenario."
        )
    payload.pop("refresh_token", None)
    return json.dumps(payload, separators=(",", ":"))


def _configure_isolated_settings(controller: Any, live: dict[str, Any]) -> None:
    settings_path = controller._system_root / "settings.yaml"  # noqa: SLF001
    isolated = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    live_settings = live.get("settings") or {}
    live_models = live.get("models") or {}
    live_providers = live.get("providers") or {}
    isolated.setdefault("settings", {})
    isolated.setdefault("models", {})
    isolated.setdefault("providers", {})
    isolated["models"][MODEL_ALIAS] = copy.deepcopy(live_models[MODEL_ALIAS])
    isolated["providers"]["openai"] = copy.deepcopy(live_providers["openai"])
    for name in ("default_context_script", "openai_oauth_enabled"):
        if name in live_settings:
            isolated["settings"][name] = copy.deepcopy(live_settings[name])
    for name, value in (
        ("default_model", MODEL_ALIAS),
        ("context_reduction_strategy", "stepped_session_map"),
        ("session_map_author_model", MODEL_ALIAS),
        ("session_map_author_thinking", "low"),
        ("session_map_low_watermark_tokens", LOW_WATERMARK_TOKENS),
        ("compaction_token_threshold", HIGH_WATERMARK_TOKENS),
        ("compaction_type", "auto"),
    ):
        _set_setting(isolated, name, value)
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


def _assert_live_model_readiness(scenario: BaseScenario) -> None:
    response = scenario.call_api("/api/metadata")
    assert response.status_code == 200
    models = response.json().get("models", [])
    by_name = {item.get("name"): item for item in models if isinstance(item, dict)}
    assert by_name.get(MODEL_ALIAS, {}).get("available") is True


def _build_user_simulator() -> Agent[None, SimulatedUserMessage]:
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        model = build_model_instance(MODEL_ALIAS, thinking="low")
    if isinstance(model, ModelExecutionSpec):
        raise RuntimeError("The simulated-user role requires a generative model.")
    return Agent(
        model,
        output_type=SimulatedUserMessage,
        name="compaction_v2_user_simulator",
        instructions=(
            "Simulate the human user in a realistic ongoing chat. Produce exactly one natural next user message. Follow the current turn brief, react to the assistant's latest response, and preserve established context. Do not mention testing, simulation, compaction, maps, or memory. Do not answer as the assistant. Keep the message under 180 words."
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
    prompt = f"TURN {turn_index} BRIEF\n{brief}\n\nVISIBLE CONVERSATION\n{visible_transcript or '[No prior turns]'}\n\nWrite the next user message now."
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        result = await collect_response(simulator, prompt)  # type: ignore[arg-type]
    if not isinstance(result.output, SimulatedUserMessage):
        raise RuntimeError(f"Simulated user turn {turn_index} returned invalid output.")
    return result.output.message.strip()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


TURN_BRIEFS = (
    "Open a planning discussion about a neighborhood heat-resilience pilot that can launch before next summer. Success means a practical, fundable pilot rather than a broad strategy report.",
    "Add a preliminary budget ceiling of $120,000, three candidate neighborhoods, and involvement from public health, parks, and two community organizations. Ask the assistant to keep the work focused.",
    "Set the first deliverable: compare the three candidate sites using heat exposure, vulnerable population, implementation feasibility, and resident trust. Ask what evidence is missing.",
    "Correct the record: finance reduced the ceiling from $120,000 to $90,000, and Riverside must be removed because its land agreement will not be ready. These replace the earlier assumptions.",
    "Settle on the two remaining sites for continued comparison. Ask for a concise decision path without pretending the final site has been selected.",
    "Introduce a one-page council briefing due Friday covering the pilot, reduced budget, two-site shortlist, and unresolved selection question.",
    "Require that the briefing not claim quantified energy or health savings because those estimates are unverified. Ask the assistant to distinguish facts from assumptions.",
    "Pause community outreach planning while legal counsel reviews whether incentives can be offered. Keep site analysis and the briefing active, and identify the legal review as the blocker.",
    "Report that counsel conditionally cleared incentives up to $50 per household if participation is voluntary and privacy language is included. Resume outreach planning under those constraints.",
    "Make the one-page council briefing the immediate priority. The site recommendation remains provisional pending one missing temperature dataset.",
    "Say the briefing structure is accepted, but the temperature dataset will not arrive until next week. Ask what can be finalized now and what must remain open.",
    "Close this planning phase: the briefing is ready for drafting, the two-site decision remains provisional, the temperature-data question remains open, and the next handoff should not reopen retired assumptions.",
)

AUDIT_PROMPTS = (
    "Before we wrap, remind me which original budget and site assumptions were explicitly replaced, and what replaced them.",
    "What workstream did we pause, and exactly what allowed it to resume?",
    "Give me the current state in one concise handoff: active deliverable, settled constraints, and the one material question still open.",
)
