"""Fail-closed mechanical readiness checks for Compaction v2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.chat.chat_store import ChatStore
from core.llm.model_utils import model_supports_capability, validate_api_keys
from core.llm.thinking import ThinkingValue
from core.settings import (
    get_compaction_author_model,
    get_compaction_author_thinking,
    get_compaction_high_watermark_tokens,
    get_compaction_low_watermark_tokens,
    get_compaction_retained_turns,
    get_compaction_strategy,
)

from .checkpoints import load_session_map_checkpoint


@dataclass(frozen=True)
class CompactionAuthorReadiness:
    """Shared generative-author eligibility for both compaction strategies."""

    enabled: bool
    reason: str
    model: str | None
    thinking: ThinkingValue


def evaluate_compaction_author_readiness(
    *, model_availability_check: Callable[[str], None] | None = None
) -> CompactionAuthorReadiness:
    """Require a text-capable author with valid thinking and usable credentials."""
    model = get_compaction_author_model()
    try:
        thinking = get_compaction_author_thinking()
    except ValueError:
        return CompactionAuthorReadiness(False, "author_thinking_invalid", model, None)
    if model is None:
        return CompactionAuthorReadiness(
            False, "author_model_not_configured", model, thinking
        )
    try:
        supports_text = model_supports_capability(model, "text")
    except ValueError:
        return CompactionAuthorReadiness(False, "author_model_unknown", model, thinking)
    if not supports_text:
        return CompactionAuthorReadiness(
            False, "author_model_not_text_capable", model, thinking
        )
    try:
        if model.strip().lower() != "test":
            (model_availability_check or validate_api_keys)(model)
    except (RuntimeError, ValueError):
        return CompactionAuthorReadiness(
            False, "author_model_unavailable", model, thinking
        )
    return CompactionAuthorReadiness(True, "ready_author", model, thinking)


@dataclass(frozen=True)
class SessionMapCompactionReadiness:
    """Resolved inputs and eligibility for one Compaction v2 reduction."""

    enabled: bool
    reason: str
    configured_strategy: str
    strategy: str
    author_model: str | None
    author_thinking: ThinkingValue
    high_watermark_tokens: int
    low_watermark_tokens: int
    minimum_retained_groups: int


def evaluate_session_map_compaction_readiness(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    model_availability_check: Callable[[str], None] | None = None,
) -> SessionMapCompactionReadiness:
    """Resolve whether one session can run Compaction v2 when initiated."""
    configured_strategy = get_compaction_strategy()
    checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
    strategy = resolve_session_compaction_strategy(
        store=store,
        session_id=session_id,
        vault_name=vault_name,
    )
    author = evaluate_compaction_author_readiness(
        model_availability_check=model_availability_check
    )
    high_watermark = get_compaction_high_watermark_tokens()
    low_watermark = get_compaction_low_watermark_tokens()
    minimum_retained_groups = get_compaction_retained_turns()

    def result(enabled: bool, reason: str) -> SessionMapCompactionReadiness:
        return SessionMapCompactionReadiness(
            enabled=enabled,
            reason=reason,
            configured_strategy=configured_strategy,
            strategy=strategy,
            author_model=author.model,
            author_thinking=author.thinking,
            high_watermark_tokens=high_watermark,
            low_watermark_tokens=low_watermark,
            minimum_retained_groups=minimum_retained_groups,
        )

    if strategy != "session_map":
        reason = (
            "recovery_card_checkpoint_present"
            if checkpoint is not None and checkpoint.checkpoint_kind == "recovery_card"
            else "strategy_not_enabled"
        )
        return result(False, reason)
    if not author.enabled:
        return result(False, author.reason)
    if low_watermark >= high_watermark:
        return result(False, "invalid_watermarks")

    session = store.get_session(session_id=session_id, vault_name=vault_name)
    if session is None:
        return result(False, "session_not_found")
    if checkpoint is None:
        return result(True, "ready_canonical_history")
    try:
        load_session_map_checkpoint(checkpoint)
    except ValueError:
        return result(False, "session_map_checkpoint_invalid")
    return result(True, "ready_existing_session_map")


def resolve_session_compaction_strategy(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
) -> str:
    """Resolve the strategy pinned by a checkpoint or use the configured default."""
    checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
    if checkpoint is None:
        return get_compaction_strategy()
    if checkpoint.checkpoint_kind == "session_map":
        return "session_map"
    return "recovery_card"
