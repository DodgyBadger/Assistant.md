"""Opt-in live smoke for the configured TypeSafe/Jev decision runtime."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority  # noqa: E402
from core.llm.decision import DecisionRequest, build_decision_classifier  # noqa: E402
from core.settings.secrets_store import set_secret_value  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SmokeSignals(BaseModel):
    """Classify two independent changes in a synthetic conversation delta."""

    goal_changed: float = Field(
        ge=0,
        le=1,
        description="Did the user's active goal materially change?",
    )
    constraint_changed: float = Field(
        ge=0,
        le=1,
        description="Did the user add or revise a constraint?",
    )


class JevDecisionRuntimeProbeScenario(BaseScenario):
    """Prove a configured Jev alias returns typed bounded values live."""

    async def test_scenario(self) -> None:
        api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "Set TYPESAFE_API_KEY only for this opt-in live experiment. "
                "Deterministic validation does not require it."
            )

        await self.start_system()
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            set_secret_value("TYPESAFE_API_KEY", api_key)
            classifier = build_decision_classifier("jev")
            result = await classifier.classify(
                DecisionRequest(
                    state=(
                        "User: Keep the current objective. Also, do not publish any "
                        "artifacts externally."
                    ),
                    output_type=SmokeSignals,
                    instructions=(
                        "Judge only changes explicitly established by this conversation delta."
                    ),
                )
            )

        self.soft_assert(
            0 <= result.output.goal_changed <= 1,
            "Jev should return a bounded raw probability for goal change",
        )
        self.soft_assert(
            0 <= result.output.constraint_changed <= 1,
            "Jev should return a bounded raw probability for constraint change",
        )
        self.soft_assert(
            result.resolved_model_name.startswith("jev-"),
            "The live result should expose Jev's resolved versioned model identity",
        )
        self.soft_assert_equal(
            result.provider_name,
            "typesafe",
            "The configured alias should execute through the TypeSafe adapter",
        )
        evidence = {
            "requested_model_alias": result.requested_model_alias,
            "resolved_model_name": result.resolved_model_name,
            "provider_name": result.provider_name,
            "output": result.output.model_dump(),
            "latency_seconds": result.latency_seconds,
            "usage": {
                "requests": result.usage.requests,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
            },
        }
        (self.artifacts_dir / "jev_decision_result.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        self.teardown_scenario()
        self.assert_no_failures()
