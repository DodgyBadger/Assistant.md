"""Governed generative authoring for eviction-derived session maps."""

from __future__ import annotations

from dataclasses import dataclass

from core.constants import SESSION_MAP_AUTHORING_PROMPT_VERSION
from core.identity import ExecutionAuthority
from core.llm.agents import collect_response, create_agent
from core.llm.model_factory import build_model_instance
from core.llm.model_selection import ModelExecutionSpec
from core.llm.thinking import ThinkingValue
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSource,
    chat_session_scope,
    get_current_execution_task,
    session_map_task_label,
)
from core.runtime.state import get_runtime_context
from core.runtime.task_runner import ExecutionTaskSpec

from .authoring import SessionMapEvidenceEnvelope, build_session_map_authoring_prompt
from .models import SessionMapDraft, validate_session_map_provenance

logger = UnifiedLogger(
    tag="session-map-authoring",
    default_sinks=["activity", "logfire", "validation"],
)


@dataclass(frozen=True)
class SessionMapAuthoringRequest:
    """Complete input required for one whole-map replacement."""

    session_id: str
    vault_name: str
    model_alias: str
    previous_map: SessionMapDraft
    envelopes: tuple[SessionMapEvidenceEnvelope, ...]
    thinking: ThinkingValue = None

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("Session-map authoring requires a session ID")
        if not self.vault_name.strip():
            raise ValueError("Session-map authoring requires a vault name")
        if not self.model_alias.strip():
            raise ValueError("Session-map authoring requires a model alias")
        if not self.envelopes:
            raise ValueError("Session-map authoring requires evidence envelopes")
        revisions = {envelope.history_revision for envelope in self.envelopes}
        if len(revisions) != 1:
            raise ValueError("Session-map evidence must share one history revision")
        prior_end = -1
        for envelope in self.envelopes:
            if envelope.session_id != self.session_id:
                raise ValueError("Session-map evidence belongs to another session")
            if envelope.vault_name != self.vault_name:
                raise ValueError("Session-map evidence belongs to another vault")
            if envelope.source_start_sequence_index <= prior_end:
                raise ValueError(
                    "Session-map evidence ranges must be ordered and non-overlapping"
                )
            if (
                envelope.source_end_sequence_index
                < envelope.source_start_sequence_index
            ):
                raise ValueError("Session-map evidence range is reversed")
            prior_end = envelope.source_end_sequence_index


@dataclass(frozen=True)
class SessionMapAuthoringResult:
    """Validated authored map plus its governed execution identity."""

    draft: SessionMapDraft
    task_id: str
    model_alias: str
    prompt_contract_version: str
    evidence_envelope_count: int


async def run_session_map_authoring(
    request: SessionMapAuthoringRequest,
    *,
    authority: ExecutionAuthority,
    source: ExecutionTaskSource = ExecutionTaskSource.SYSTEM,
) -> SessionMapAuthoringResult:
    """Author one replacement map inside the normal execution-task lifecycle."""
    runtime = get_runtime_context()
    result = await runtime.task_runner.run_inline(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING,
            scope=chat_session_scope(request.session_id),
            source=source,
            label=session_map_task_label(request.session_id),
            authority=authority,
            metadata={
                "vault": request.vault_name,
                "session_id": request.session_id,
                "model_alias": request.model_alias,
                "prompt_contract_version": SESSION_MAP_AUTHORING_PROMPT_VERSION,
                "evidence_envelope_count": len(request.envelopes),
            },
        ),
        lambda task: _execute_session_map_authoring(request, task_id=task.task_id),
    )
    if not isinstance(result, SessionMapAuthoringResult):
        raise TypeError("Session-map execution returned an invalid result")
    return result


async def _execute_session_map_authoring(
    request: SessionMapAuthoringRequest,
    *,
    task_id: str,
) -> SessionMapAuthoringResult:
    task = get_current_execution_task()
    if task is None or task.task_id != task_id:
        raise RuntimeError("Session-map authoring requires its owning execution task")
    if task.kind != ExecutionTaskKind.SESSION_MAP_AUTHORING.value:
        raise RuntimeError("Session-map authoring requires a session-map task")

    first_source = request.envelopes[0].source_start_sequence_index
    last_source = request.envelopes[-1].source_end_sequence_index
    logger.info(
        "session_map_authoring_started",
        data={
            "event": "session_map_authoring_started",
            "task_id": task_id,
            "session_id": request.session_id,
            "vault_name": request.vault_name,
            "model_alias": request.model_alias,
            "prompt_contract_version": SESSION_MAP_AUTHORING_PROMPT_VERSION,
            "evidence_envelope_count": len(request.envelopes),
            "evidence_source_start": first_source,
            "evidence_source_end": last_source,
            "previous_entry_count": len(request.previous_map.entries),
        },
    )
    try:
        prompt = build_session_map_authoring_prompt(
            previous_map=request.previous_map,
            envelopes=request.envelopes,
        )
        draft = await _invoke_session_map_model(
            model_alias=request.model_alias,
            thinking=request.thinking,
            prompt=prompt,
        )
        validate_session_map_provenance(
            draft,
            envelopes=request.envelopes,
            previous_map=request.previous_map,
        )
    except Exception as exc:
        logger.warning(
            "session_map_authoring_failed",
            data={
                "event": "session_map_authoring_failed",
                "task_id": task_id,
                "session_id": request.session_id,
                "vault_name": request.vault_name,
                "model_alias": request.model_alias,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise

    logger.info(
        "session_map_authoring_completed",
        data={
            "event": "session_map_authoring_completed",
            "task_id": task_id,
            "session_id": request.session_id,
            "vault_name": request.vault_name,
            "model_alias": request.model_alias,
            "prompt_contract_version": SESSION_MAP_AUTHORING_PROMPT_VERSION,
            "entry_count": len(draft.entries),
            "evidence_envelope_count": len(request.envelopes),
        },
    )
    return SessionMapAuthoringResult(
        draft=draft,
        task_id=task_id,
        model_alias=request.model_alias,
        prompt_contract_version=SESSION_MAP_AUTHORING_PROMPT_VERSION,
        evidence_envelope_count=len(request.envelopes),
    )


async def _invoke_session_map_model(
    *,
    model_alias: str,
    thinking: ThinkingValue,
    prompt: str,
) -> SessionMapDraft:
    model = build_model_instance(model_alias, thinking=thinking)
    if isinstance(model, ModelExecutionSpec):
        raise ValueError("Session-map authoring requires a generative model")
    agent = await create_agent(model=model, output_type=SessionMapDraft)
    result = await collect_response(agent, prompt)
    if not isinstance(result.output, SessionMapDraft):
        raise TypeError("Session-map model returned an invalid structured output")
    return result.output
