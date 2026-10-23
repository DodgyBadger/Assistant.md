"""Chat-only fallback for names removed from the configured model registry."""

from core.llm.model_utils import resolve_canonical_model_alias
from core.logger import UnifiedLogger
from core.settings.model_registry import InvalidModelAlias, UnknownModelAlias
from core.settings.store import get_general_settings
from core.utils.value_parser import DirectiveValueParser

logger = UnifiedLogger(tag="chat")


def resolve_chat_model_alias(model_alias: str, *, session_id: str | None = None) -> str:
    """Resolve one chat selection, using the default only when the name cannot resolve."""
    _, parameters = DirectiveValueParser.parse_value_with_parameters(model_alias)
    if parameters:
        raise ValueError(
            "Model alias parameters are not supported. Pass thinking separately."
        )
    if model_alias.strip().lower() in {"test", "none"}:
        # Preserve the existing validation fixture and explicit skip-mode error.
        return model_alias.strip().lower()
    try:
        return resolve_canonical_model_alias(model_alias)
    except (UnknownModelAlias, InvalidModelAlias):
        entry = get_general_settings().get("default_model")
        default = entry.value if entry is not None else None
        if not isinstance(default, str) or default.strip().lower() in {"", "none"}:
            raise UnknownModelAlias(
                "The default model is not configured. Check the System tab."
            ) from None
        canonical = resolve_canonical_model_alias(default)
        logger.warning(
            "Chat model alias no longer resolves; using the default",
            data={
                "event": "chat_model_fallback",
                "status": "resolved",
                "requested_alias": model_alias,
                "effective_alias": canonical,
                "session_id": session_id,
                "issue": f"chat_model_fallback:{session_id}:{model_alias}:{canonical}",
            },
        )
        return canonical
