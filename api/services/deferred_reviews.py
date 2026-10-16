"""Deferred chat-review inspection, validation, and resume API services."""

from collections import Counter
from typing import Any

from pydantic_ai import DeferredToolResults, ToolApproved, ToolDenied

from core.chat.deferred_reviews import (
    DeferredReviewError,
    StoredDeferredReview,
    attach_deferred_review_task,
    deferred_review_conflicts,
    get_deferred_review,
    mark_deferred_review_submitted,
    mark_deferred_review_terminal,
)
from core.chat.edit_proposals import (
    EditProposalError,
    get_edit_proposal,
)
from core.chat.task_execution import start_deferred_review_resume_task
from core.constants import INLINE_EDIT_DENIAL_MESSAGE
from core.llm.thinking import ThinkingValue, normalize_thinking_value

from ..exceptions import APIException
from ..models import (
    DeferredReviewResponse,
    DeferredReviewSubmitResponse,
    EditProposalResponse,
)
from .chat_sessions import (
    _deferred_review_response,
)
from .execution_tasks import (
    get_execution_task,
)
from .vault_files import (
    resolve_vault_root,
)


def get_chat_edit_proposal(
    *,
    vault_name: str,
    session_id: str,
    artifact_ref: str,
) -> EditProposalResponse:
    """Return one chat edit proposal artifact."""
    try:
        proposal = get_edit_proposal(
            vault_name=vault_name,
            session_id=session_id,
            artifact_ref=artifact_ref,
        )
    except EditProposalError as exc:
        raise _edit_proposal_api_error(exc) from exc
    return EditProposalResponse(**proposal)


def get_chat_deferred_review(
    *,
    vault_name: str,
    session_id: str,
    artifact_ref: str,
) -> DeferredReviewResponse:
    """Return one deferred inline review request."""
    review = get_deferred_review(
        vault_name=vault_name,
        session_id=session_id,
        artifact_ref=artifact_ref,
    )
    if review is None:
        raise APIException(
            status_code=404,
            error_type="DeferredReviewNotFound",
            message="Deferred review request was not found for this chat session.",
            details={
                "artifact_ref": artifact_ref,
                "session_id": session_id,
                "vault_name": vault_name,
            },
        )
    return _deferred_review_response(review)


async def submit_chat_deferred_review(
    *,
    vault_name: str,
    session_id: str,
    artifact_ref: str,
    decisions: list[dict[str, Any]],
) -> DeferredReviewSubmitResponse:
    """Submit deferred inline review decisions and start a resume task."""
    review = _require_pending_deferred_review(
        vault_name=vault_name,
        session_id=session_id,
        artifact_ref=artifact_ref,
        decisions=decisions,
    )
    approvals = _build_deferred_review_approvals(
        review=review,
        decisions=decisions,
        artifact_ref=artifact_ref,
    )
    results = DeferredToolResults(approvals=approvals)
    tools, model, thinking, context_template, chat_mode = _deferred_resume_options(
        review.resume_config,
        artifact_ref=artifact_ref,
    )
    vault_path = str(resolve_vault_root(vault_name))
    conflicts = deferred_review_conflicts(
        review=review,
        approved_call_ids={
            tool_call_id
            for tool_call_id, decision in approvals.items()
            if not isinstance(decision, ToolDenied)
        },
        vault_path=vault_path,
    )
    if conflicts:
        raise APIException(
            status_code=409,
            error_type="DeferredReviewTargetConflict",
            message="A reviewed file changed while approval was pending.",
            details={"artifact_ref": artifact_ref, "conflicts": conflicts},
        )
    try:
        claimed = mark_deferred_review_submitted(
            vault_name=vault_name,
            session_id=session_id,
            artifact_ref=artifact_ref,
            results=results,
            resumed_task_id="",
        )
    except DeferredReviewError as exc:
        raise _deferred_review_api_error(exc) from exc
    try:
        started = await start_deferred_review_resume_task(
            vault_name=vault_name,
            vault_path=vault_path,
            session_id=session_id,
            review=claimed,
            deferred_tool_results=results,
            tools=tools,
            model=model,
            thinking=thinking,
            context_template=context_template,
            chat_mode=chat_mode,
        )
        updated = attach_deferred_review_task(
            vault_name=vault_name,
            session_id=session_id,
            artifact_ref=artifact_ref,
            resumed_task_id=started.task.task_id,
        )
    except Exception as exc:
        try:
            mark_deferred_review_terminal(
                vault_name=vault_name,
                session_id=session_id,
                artifact_ref=artifact_ref,
                status="failed",
                error={"error_type": type(exc).__name__, "message": str(exc)},
            )
        except DeferredReviewError:
            pass
        raise
    task = await get_execution_task(started.task.task_id)
    return DeferredReviewSubmitResponse(
        artifact_ref=updated.artifact_ref,
        status=updated.status,
        session_id=session_id,
        task=task,
    )


def _require_pending_deferred_review(
    *,
    vault_name: str,
    session_id: str,
    artifact_ref: str,
    decisions: list[dict[str, Any]],
) -> StoredDeferredReview:
    review = get_deferred_review(
        vault_name=vault_name,
        session_id=session_id,
        artifact_ref=artifact_ref,
    )
    if review is None:
        raise APIException(
            status_code=404,
            error_type="DeferredReviewNotFound",
            message="Deferred review request was not found for this chat session.",
            details={"artifact_ref": artifact_ref},
        )
    if review.status != "pending":
        raise APIException(
            status_code=409,
            error_type="DeferredReviewAlreadySubmitted",
            message="Deferred review request has already been submitted.",
            details={
                "artifact_ref": artifact_ref,
                "status": review.status,
                "resumed_task_id": review.resumed_task_id,
            },
        )
    if review.requests.calls:
        raise APIException(
            status_code=400,
            error_type="UnsupportedDeferredCallReview",
            message="Deferred external call review is not supported yet.",
            details={"artifact_ref": artifact_ref},
        )
    if not decisions:
        raise APIException(
            status_code=400,
            error_type="NoDeferredReviewDecisions",
            message="Choose at least one review decision.",
            details={"artifact_ref": artifact_ref},
        )

    known_ids = {str(call.tool_call_id) for call in review.requests.approvals}
    submitted_ids = [str(decision.get("tool_call_id") or "") for decision in decisions]
    duplicate_ids = sorted(
        call_id for call_id, count in Counter(submitted_ids).items() if count > 1
    )
    unknown_ids = sorted(
        call_id for call_id in submitted_ids if call_id not in known_ids
    )
    missing_ids = sorted(known_ids - set(submitted_ids))
    if duplicate_ids or unknown_ids or missing_ids:
        raise APIException(
            status_code=400,
            error_type="DeferredReviewDecisionMismatch",
            message="Review decisions must cover the pending deferred tool calls.",
            details={
                "artifact_ref": artifact_ref,
                "duplicate_tool_call_ids": duplicate_ids,
                "unknown_tool_call_ids": unknown_ids,
                "missing_tool_call_ids": missing_ids,
            },
        )
    return review


def _build_deferred_review_approvals(
    *,
    review: StoredDeferredReview,
    decisions: list[dict[str, Any]],
    artifact_ref: str,
) -> dict[str, bool | ToolApproved | ToolDenied]:
    calls_by_id = {str(call.tool_call_id): call for call in review.requests.approvals}
    approvals: dict[str, bool | ToolApproved | ToolDenied] = {}
    for decision in decisions:
        tool_call_id = str(decision.get("tool_call_id") or "")
        decision_value = str(decision.get("decision") or "")
        if decision_value == "deny":
            message = str(decision.get("message") or "").strip()
            denial_message = INLINE_EDIT_DENIAL_MESSAGE
            if message:
                denial_message = f"{denial_message} User reason: {message}"
            approvals[tool_call_id] = ToolDenied(denial_message)
            continue
        if decision_value != "approve":
            raise APIException(
                status_code=400,
                error_type="InvalidDeferredReviewDecision",
                message="Deferred review decision must be approve or deny.",
                details={"tool_call_id": tool_call_id, "decision": decision_value},
            )

        override_args = decision.get("override_args") or {}
        if not isinstance(override_args, dict):
            override_args = {}
        _validate_deferred_review_overrides(
            tool_call_id=tool_call_id,
            original_args=calls_by_id[tool_call_id].args_as_dict(),
            override_args=override_args,
            artifact_ref=artifact_ref,
        )
        approvals[tool_call_id] = (
            ToolApproved(
                override_args={
                    **calls_by_id[tool_call_id].args_as_dict(),
                    **override_args,
                }
            )
            if override_args
            else True
        )
    return approvals


def _validate_deferred_review_overrides(
    *,
    tool_call_id: str,
    original_args: dict[str, Any],
    override_args: dict[str, Any],
    artifact_ref: str,
) -> None:
    if not override_args:
        return
    operation = str(original_args.get("operation") or "").strip().lower()
    editable_args = {
        "write": {"content"},
        "append": {"content"},
        "replace_text": {"old_text", "new_text"},
        "edit_line": {"old_text", "new_text"},
        "move": {"destination"},
        "mkdir": {"path"},
    }.get(operation, set())
    changed_immutable = sorted(
        key
        for key, value in override_args.items()
        if key not in editable_args and value != original_args.get(key)
    )
    if changed_immutable:
        raise APIException(
            status_code=400,
            error_type="DeferredReviewImmutableArgument",
            message="Review overrides cannot change the operation target or policy.",
            details={
                "artifact_ref": artifact_ref,
                "tool_call_id": tool_call_id,
                "immutable_arguments": changed_immutable,
            },
        )


def _deferred_resume_options(
    resume_config: dict[str, Any], *, artifact_ref: str
) -> tuple[list[str], str, ThinkingValue, str | None, str]:
    model = str(resume_config.get("model") or "").strip()
    if not model:
        raise APIException(
            status_code=500,
            error_type="DeferredReviewResumeConfigMissing",
            message="Deferred review request is missing the model needed to resume.",
            details={"artifact_ref": artifact_ref},
        )
    tools = [
        str(tool) for tool in resume_config.get("tools") or [] if str(tool).strip()
    ]
    thinking = normalize_thinking_value(
        resume_config.get("thinking"),
        source_name="stored deferred review thinking",
    )
    context_template = str(resume_config.get("context_template") or "").strip() or None
    chat_mode = str(resume_config.get("chat_mode") or "normal").strip() or "normal"
    return tools, model, thinking, context_template, chat_mode


def _edit_proposal_api_error(exc: EditProposalError) -> APIException:
    status_by_code = {
        "EditProposalNotFound": 404,
        "VaultFileNotFound": 404,
        "VaultFileConflict": 409,
        "EditTextMismatch": 409,
        "ProposalAlreadyApplied": 409,
        "ProposalDenied": 409,
        "VaultFileExists": 409,
        "VaultDestinationExists": 409,
        "InvalidPath": 400,
        "InvalidDestination": 400,
        "InvalidOperation": 400,
        "InvalidEdit": 400,
        "NoSelectedEdits": 400,
        "UnknownEdit": 400,
    }
    return APIException(
        status_code=status_by_code.get(exc.code, 400),
        error_type=exc.code,
        message=str(exc),
        details=exc.details,
    )


def _deferred_review_api_error(exc: DeferredReviewError) -> APIException:
    status_by_code = {
        "DeferredReviewNotFound": 404,
        "DeferredReviewAlreadySubmitted": 409,
        "DeferredReviewStateConflict": 409,
    }
    return APIException(
        status_code=status_by_code.get(exc.code, 400),
        error_type=exc.code,
        message=str(exc),
        details=exc.details,
    )
