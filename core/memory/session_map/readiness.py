"""Fail-closed readiness checks for experimental stepped session maps."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.chat.chat_store import ChatStore
from core.llm.model_utils import model_supports_capability, validate_api_keys
from core.llm.thinking import ThinkingValue
from core.settings import (
    get_compaction_token_threshold,
    get_compaction_type,
    get_context_reduction_strategy,
    get_session_map_author_model,
    get_session_map_author_thinking,
    get_session_map_low_watermark_tokens,
)

from .checkpoints import load_session_map_checkpoint


@dataclass(frozen=True)
class SessionMapReadiness:
    """Resolved strategy inputs and one stable eligibility decision."""

    enabled: bool
    reason: str
    strategy: str
    author_model: str | None
    author_thinking: ThinkingValue
    high_watermark_tokens: int
    low_watermark_tokens: int


def evaluate_session_map_readiness(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    model_availability_check: Callable[[str], None] = validate_api_keys,
) -> SessionMapReadiness:
    """Resolve whether one session may use stepped-map context reduction."""
    strategy = get_context_reduction_strategy()
    author_model = get_session_map_author_model()
    try:
        author_thinking = get_session_map_author_thinking()
        thinking_valid = True
    except ValueError:
        author_thinking = None
        thinking_valid = False
    high_watermark = get_compaction_token_threshold()
    low_watermark = get_session_map_low_watermark_tokens()

    def result(enabled: bool, reason: str) -> SessionMapReadiness:
        return SessionMapReadiness(
            enabled=enabled,
            reason=reason,
            strategy=strategy,
            author_model=author_model,
            author_thinking=author_thinking,
            high_watermark_tokens=high_watermark,
            low_watermark_tokens=low_watermark,
        )

    if strategy != "stepped_session_map":
        return result(False, "strategy_not_enabled")
    if get_compaction_type() != "auto":
        return result(False, "automatic_context_reduction_disabled")
    if author_model is None:
        return result(False, "author_model_not_configured")
    if not thinking_valid:
        return result(False, "author_thinking_invalid")
    try:
        supports_text = model_supports_capability(author_model, "text")
    except ValueError:
        return result(False, "author_model_unknown")
    if not supports_text:
        return result(False, "author_model_not_text_capable")
    try:
        if author_model.strip().lower() != "test":
            model_availability_check(author_model)
    except (RuntimeError, ValueError):
        return result(False, "author_model_unavailable")
    if low_watermark >= high_watermark:
        return result(False, "invalid_watermarks")

    session = store.get_session(session_id=session_id, vault_name=vault_name)
    if session is None:
        return result(False, "session_not_found")
    checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
    if checkpoint is None:
        return result(True, "ready_canonical_history")
    if checkpoint.checkpoint_kind != "session_map":
        return result(False, "recovery_card_checkpoint_present")
    try:
        load_session_map_checkpoint(checkpoint)
    except ValueError:
        return result(False, "session_map_checkpoint_invalid")
    return result(True, "ready_existing_session_map")
