"""Governed scalar movement decisions for stepped session maps."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from core.constants import (
    SESSION_MAP_GATE_INSTRUCTION,
    SESSION_MAP_GATE_PROMPT_VERSION,
)
from core.identity import ExecutionAuthority
from core.llm.decision import (
    DecisionRequest,
    DecisionResult,
    build_decision_classifier,
)
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSource,
    chat_session_scope,
    get_current_execution_task,
    session_map_classification_task_label,
)
from core.runtime.state import get_runtime_context
from core.runtime.task_runner import ExecutionTaskSpec
from core.utils.tokens import estimate_token_count

from .authoring import SessionMapEvidenceEnvelope
from .models import SessionMapDraft

logger = UnifiedLogger(
    tag="session-map-classification",
    default_sinks=["activity", "logfire", "validation"],
)


class SessionMapMovementDecision(BaseModel):
    """One provider-neutral probability that whole-map authoring is warranted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    material_map_update_probability: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class SessionMapGateRequest:
    """Complete bounded input for one cumulative map-movement decision."""

    session_id: str
    vault_name: str
    model_alias: str
    current_map: SessionMapDraft
    envelopes: tuple[SessionMapEvidenceEnvelope, ...]

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("Session-map classification requires a session ID")
        if not self.vault_name.strip():
            raise ValueError("Session-map classification requires a vault name")
        if not self.model_alias.strip():
            raise ValueError("Session-map classification requires a model alias")
        if not self.envelopes:
            raise ValueError("Session-map classification requires evidence envelopes")
        revisions = {envelope.history_revision for envelope in self.envelopes}
        if len(revisions) != 1:
            raise ValueError(
                "Session-map classification evidence must share one revision"
            )
        prior_end = -1
        for envelope in self.envelopes:
            if envelope.session_id != self.session_id:
                raise ValueError(
                    "Session-map classification evidence belongs to another session"
                )
            if envelope.vault_name != self.vault_name:
                raise ValueError(
                    "Session-map classification evidence belongs to another vault"
                )
            if envelope.source_start_sequence_index <= prior_end:
                raise ValueError(
                    "Session-map classification evidence must be ordered and non-overlapping"
                )
            if (
                envelope.source_end_sequence_index
                < envelope.source_start_sequence_index
            ):
                raise ValueError(
                    "Session-map classification evidence range is reversed"
                )
            prior_end = envelope.source_end_sequence_index


@dataclass(frozen=True)
class SessionMapGateResult:
    """Scalar decision plus governed execution and provider diagnostics."""

    task_id: str
    score: float
    model_alias: str
    resolved_model_name: str
    provider_name: str
    prompt_contract_version: str
    evidence_envelope_count: int
    input_token_estimate: int
    latency_seconds: float
    input_tokens: int
    output_tokens: int


def build_session_map_gate_state(
    *,
    current_map: SessionMapDraft,
    envelopes: tuple[SessionMapEvidenceEnvelope, ...],
) -> str:
    """Build the bounded provider-neutral state passed to a decision adapter."""
    if not envelopes:
        raise ValueError("Session-map classification requires evidence envelopes")
    return json.dumps(
        {
            "prompt_contract_version": SESSION_MAP_GATE_PROMPT_VERSION,
            "current_map": current_map.model_dump(mode="json"),
            "cumulative_new_evidence_envelopes": [
                {
                    "envelope_id": envelope.envelope_id,
                    "source_range": {
                        "start": envelope.source_start_sequence_index,
                        "end": envelope.source_end_sequence_index,
                    },
                    "projected_text": envelope.projected_text,
                }
                for envelope in envelopes
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def estimate_session_map_gate_tokens(
    *,
    current_map: SessionMapDraft,
    envelopes: tuple[SessionMapEvidenceEnvelope, ...],
) -> int:
    """Estimate the complete decision request before provider dispatch."""
    state = build_session_map_gate_state(
        current_map=current_map,
        envelopes=envelopes,
    )
    return estimate_token_count(f"{SESSION_MAP_GATE_INSTRUCTION}\n\n{state}")


async def run_session_map_gate(
    request: SessionMapGateRequest,
    *,
    authority: ExecutionAuthority,
    source: ExecutionTaskSource = ExecutionTaskSource.SYSTEM,
) -> SessionMapGateResult:
    """Classify cumulative map movement inside the normal task lifecycle."""
    runtime = get_runtime_context()
    input_token_estimate = estimate_session_map_gate_tokens(
        current_map=request.current_map,
        envelopes=request.envelopes,
    )
    result = await runtime.task_runner.run_inline(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.SESSION_MAP_CLASSIFICATION,
            scope=chat_session_scope(request.session_id),
            source=source,
            label=session_map_classification_task_label(request.session_id),
            authority=authority,
            metadata={
                "vault": request.vault_name,
                "session_id": request.session_id,
                "model_alias": request.model_alias,
                "prompt_contract_version": SESSION_MAP_GATE_PROMPT_VERSION,
                "evidence_envelope_count": len(request.envelopes),
                "input_token_estimate": input_token_estimate,
            },
        ),
        lambda task: _execute_session_map_gate(
            request,
            task_id=task.task_id,
            input_token_estimate=input_token_estimate,
        ),
    )
    if not isinstance(result, SessionMapGateResult):
        raise TypeError("Session-map classification returned an invalid result")
    return result


async def _execute_session_map_gate(
    request: SessionMapGateRequest,
    *,
    task_id: str,
    input_token_estimate: int,
) -> SessionMapGateResult:
    task = get_current_execution_task()
    if task is None or task.task_id != task_id:
        raise RuntimeError("Session-map classification requires its owning task")
    if task.kind != ExecutionTaskKind.SESSION_MAP_CLASSIFICATION.value:
        raise RuntimeError("Session-map classification requires a classification task")

    first_source = request.envelopes[0].source_start_sequence_index
    last_source = request.envelopes[-1].source_end_sequence_index
    logger.info(
        "session_map_classification_started",
        data={
            "event": "session_map_classification_started",
            "task_id": task_id,
            "session_id": request.session_id,
            "vault_name": request.vault_name,
            "model_alias": request.model_alias,
            "prompt_contract_version": SESSION_MAP_GATE_PROMPT_VERSION,
            "evidence_envelope_count": len(request.envelopes),
            "evidence_source_start": first_source,
            "evidence_source_end": last_source,
            "input_token_estimate": input_token_estimate,
        },
    )
    try:
        state = build_session_map_gate_state(
            current_map=request.current_map,
            envelopes=request.envelopes,
        )
        classifier = build_decision_classifier(request.model_alias)
        classified: DecisionResult[SessionMapMovementDecision] = (
            await classifier.classify(
                DecisionRequest(
                    state=state,
                    output_type=SessionMapMovementDecision,
                    instructions=SESSION_MAP_GATE_INSTRUCTION,
                )
            )
        )
    except Exception as exc:
        logger.warning(
            "session_map_classification_failed",
            data={
                "event": "session_map_classification_failed",
                "task_id": task_id,
                "session_id": request.session_id,
                "vault_name": request.vault_name,
                "model_alias": request.model_alias,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise

    score = classified.output.material_map_update_probability
    logger.info(
        "session_map_classification_completed",
        data={
            "event": "session_map_classification_completed",
            "task_id": task_id,
            "session_id": request.session_id,
            "vault_name": request.vault_name,
            "model_alias": request.model_alias,
            "resolved_model": classified.resolved_model_name,
            "provider": classified.provider_name,
            "prompt_contract_version": SESSION_MAP_GATE_PROMPT_VERSION,
            "evidence_envelope_count": len(request.envelopes),
            "input_token_estimate": input_token_estimate,
            "score": score,
            "latency_seconds": classified.latency_seconds,
            "input_tokens": classified.usage.input_tokens,
            "output_tokens": classified.usage.output_tokens,
        },
    )
    return SessionMapGateResult(
        task_id=task_id,
        score=score,
        model_alias=request.model_alias,
        resolved_model_name=classified.resolved_model_name,
        provider_name=classified.provider_name,
        prompt_contract_version=SESSION_MAP_GATE_PROMPT_VERSION,
        evidence_envelope_count=len(request.envelopes),
        input_token_estimate=input_token_estimate,
        latency_seconds=classified.latency_seconds,
        input_tokens=classified.usage.input_tokens,
        output_tokens=classified.usage.output_tokens,
    )
