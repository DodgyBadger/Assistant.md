"""Probe Jev separation on labelled, cumulative session-map movement cases."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.chat.compaction import CanonicalEvictionEnvelope  # noqa: E402
from core.identity import LOCAL_USER_AUTHORITY  # noqa: E402
from core.memory.session_map.gate import (  # noqa: E402
    SessionMapGateRequest,
    run_session_map_gate,
)
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SourceRange,
)
from core.runtime.execution_tasks import (  # noqa: E402
    ExecutionTaskKind,
    ExecutionTaskSource,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import (  # noqa: E402
    BaseScenario,
    with_local_user_authority,
)
from validation.scenarios.experiments.session_context_strategy_comparison import (  # noqa: E402
    _active_live_secret_key,
    _copy_sqlite_database,
)

MODEL_ALIAS = "jev"
THRESHOLD = 0.5
REPETITIONS = 3
SESSION_ID = "session-map-gate-steady-state"


@dataclass(frozen=True)
class GateCase:
    name: str
    expected_update: bool
    current_map: SessionMapDraft
    envelopes: tuple[CanonicalEvictionEnvelope, ...]


class SessionMapGateSteadyStateScenario(BaseScenario):
    """Run the production classifier over preregistered movement labels."""

    @with_local_user_authority
    async def test_scenario(self) -> None:
        vault = self.create_vault("SessionMapGateSteadyStateVault")
        self._configure_live_runtime()
        await self.start_system()

        try:
            cases = _cases(vault.name)
            records: list[dict[str, object]] = []
            for case in cases:
                scores: list[float] = []
                for _ in range(REPETITIONS):
                    result = await run_session_map_gate(
                        SessionMapGateRequest(
                            session_id=SESSION_ID,
                            vault_name=vault.name,
                            model_alias=MODEL_ALIAS,
                            current_map=case.current_map,
                            envelopes=case.envelopes,
                        ),
                        authority=LOCAL_USER_AUTHORITY,
                        source=ExecutionTaskSource.SYSTEM,
                    )
                    scores.append(result.score)
                records.append(
                    {
                        "name": case.name,
                        "expected_update": case.expected_update,
                        "scores": scores,
                        "mean_score": sum(scores) / len(scores),
                        "threshold_votes": sum(score >= THRESHOLD for score in scores),
                    }
                )

            runtime = get_runtime_context()
            tasks = await runtime.task_coordinator.list_tasks(
                kind=ExecutionTaskKind.SESSION_MAP_CLASSIFICATION.value,
                include_terminal=True,
            )
            assert len(tasks) == len(cases) * REPETITIONS
            assert all(task.status == "completed" for task in tasks)
            artifact = {
                "model_alias": MODEL_ALIAS,
                "threshold": THRESHOLD,
                "repetitions": REPETITIONS,
                "cases": records,
                "task_count": len(tasks),
            }
            (self.artifacts_dir / "gate_probe.json").write_text(
                json.dumps(artifact, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            (self.artifacts_dir / "gate_probe.md").write_text(
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
        isolated_settings.setdefault("models", {})
        isolated_settings.setdefault("providers", {})
        isolated_settings["models"][MODEL_ALIAS] = live_settings["models"][MODEL_ALIAS]
        isolated_settings["providers"]["typesafe"] = live_settings["providers"][
            "typesafe"
        ]
        isolated_settings_path.write_text(
            yaml.safe_dump(isolated_settings, sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )
        _copy_sqlite_database(
            live_root / "access.db",
            controller._system_root / "access.db",  # noqa: SLF001
        )
        controller._validation_secret_key = _active_live_secret_key(  # noqa: SLF001
            controller._original_secret_key  # noqa: SLF001
        )


def _cases(vault_name: str) -> tuple[GateCase, ...]:
    base = _base_map()
    after_approval = _after_approval_map()
    repetition = _envelope(
        vault_name,
        10,
        11,
        "user: Keep the invoice portal focused on PHHC finance review.\nassistant: Understood; the existing goal is unchanged.",
    )
    clarification = _envelope(
        vault_name,
        12,
        13,
        "user: The reviewers are the same finance committee already named in the specification.\nassistant: Noted; this clarifies wording but does not change the plan.",
    )
    completion = _envelope(
        vault_name,
        14,
        15,
        "user: Finance approved the portal today, so that approval action is complete.\nassistant: Confirmed; finance approval is no longer pending.",
    )
    transient = _envelope(
        vault_name,
        16,
        17,
        "user: Brainstorm possible launch-page colours, but do not choose one yet.\nassistant: Here are blue, green, and charcoal directions to consider.",
    )
    proposal = _envelope(
        vault_name,
        18,
        19,
        "user: What database might fit later?\nassistant: PostgreSQL could be a good option, but this is only a proposal.",
    )
    adoption = _envelope(
        vault_name,
        20,
        21,
        "user: Agreed. Use PostgreSQL for the portal.\nassistant: PostgreSQL is now the selected database.",
    )
    reversal = _envelope(
        vault_name,
        22,
        23,
        "user: Reverse the earlier upload constraint: applicants must now attach one PDF invoice.\nassistant: The portal will require one PDF upload per application.",
    )
    relocation = _envelope(
        vault_name,
        24,
        25,
        "user: Move the approved specification from PHHC/portal.md to PHHC/Finance/invoice-portal.md.\nassistant: The specification was moved to PHHC/Finance/invoice-portal.md.",
    )
    return (
        GateCase("repetition", False, base, (repetition,)),
        GateCase(
            "cumulative_minor_clarification",
            False,
            base,
            (repetition, clarification),
        ),
        GateCase(
            "cumulative_explicit_completion",
            True,
            base,
            (repetition, clarification, completion),
        ),
        GateCase("transient_brainstorm", False, after_approval, (transient,)),
        GateCase(
            "cumulative_assistant_proposal",
            False,
            after_approval,
            (transient, proposal),
        ),
        GateCase(
            "cumulative_user_adoption",
            True,
            after_approval,
            (transient, proposal, adoption),
        ),
        GateCase("constraint_reversal", True, after_approval, (reversal,)),
        GateCase("artifact_relocation", True, after_approval, (relocation,)),
    )


def _base_map() -> SessionMapDraft:
    return SessionMapDraft(
        entries=(
            _entry(
                "invoice_portal_goal",
                "goal",
                "active",
                "Deliver a PHHC invoice portal for finance review.",
                0,
            ),
            _entry(
                "finance_approval",
                "next_action",
                "active",
                "Obtain finance committee approval for the portal.",
                2,
            ),
            _entry(
                "django_admin",
                "decision",
                "active",
                "Use Django Admin for the first review surface.",
                4,
            ),
            _entry(
                "no_uploads",
                "constraint",
                "active",
                "The initial portal must not accept applicant uploads.",
                6,
            ),
            _entry(
                "portal_spec",
                "artifact",
                "active",
                "The approved specification is at PHHC/portal.md.",
                8,
            ),
        )
    )


def _after_approval_map() -> SessionMapDraft:
    base = _base_map()
    return SessionMapDraft(
        entries=tuple(entry for entry in base.entries if entry.id != "finance_approval")
    )


def _entry(
    entry_id: str,
    kind: str,
    state: str,
    text: str,
    source: int,
) -> SessionMapEntry:
    return SessionMapEntry.model_validate(
        {
            "id": entry_id,
            "kind": kind,
            "state": state,
            "basis": "user_established",
            "text": text,
            "sources": [SourceRange(start=source, end=source).model_dump()],
        }
    )


def _envelope(
    vault_name: str,
    start: int,
    end: int,
    text: str,
) -> CanonicalEvictionEnvelope:
    return CanonicalEvictionEnvelope(
        envelope_id=f"steady-state-{start}-{end}",
        session_id=SESSION_ID,
        vault_name=vault_name,
        history_revision=1,
        source_start_sequence_index=start,
        source_end_sequence_index=end,
        message_count=end - start + 1,
        estimated_tokens=max(1, len(text) // 4),
        projected_text=text,
        source_digest="0" * 64,
    )


def _render_summary(artifact: dict[str, Any]) -> str:
    lines = [
        "# Session-map gate steady-state probe",
        "",
        f"Threshold: {artifact['threshold']}",
        f"Repetitions per case: {artifact['repetitions']}",
        "",
    ]
    for raw_case in artifact["cases"]:
        case = dict(raw_case)
        lines.append(
            f"- {case['name']}: expected_update={case['expected_update']}; "
            f"scores={case['scores']}; votes={case['threshold_votes']}"
        )
    lines.append("")
    return "\n".join(lines)
