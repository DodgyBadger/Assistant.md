"""Markdown transcript export from persisted chat session data."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from core.constants import ASSISTANTMD_ROOT_DIR, CHAT_SESSIONS_DIR

from .chat_store import ChatStore, StoredChatMessage, StoredChatSession


@dataclass(frozen=True)
class ExportedTranscript:
    """Result of exporting one chat transcript."""

    path: str
    filename: str


def export_chat_transcript(
    *,
    store: ChatStore,
    vault_path: str,
    vault_name: str,
    session_id: str,
) -> ExportedTranscript:
    """Write one markdown transcript for a persisted session, overwriting any prior export."""
    session = store.get_session(session_id=session_id, vault_name=vault_name)
    if session is None:
        raise ValueError(
            f"Chat session '{session_id}' was not found in vault '{vault_name}'."
        )

    sessions_dir = _resolve_sessions_dir(vault_path=vault_path)
    history_file = _build_history_file(sessions_dir=sessions_dir, session=session)
    _remove_prior_transcript_variants(
        sessions_dir=sessions_dir, session_id=session.session_id
    )

    messages = store.get_stored_messages(session_id=session_id, vault_name=vault_name)
    lines = [
        *_build_frontmatter(session=session),
        f"Chat Session: {_build_session_export_stem(session)}",
        "",
    ]
    for message in messages:
        rendered = _render_message_block(message)
        if rendered:
            lines.extend(rendered)

    history_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return ExportedTranscript(path=str(history_file), filename=history_file.name)


def remove_chat_transcript_exports(*, vault_path: str, session_ids: list[str]) -> None:
    """Delete transcript exports for one or more session IDs."""
    sessions_dir = _resolve_sessions_dir(vault_path=vault_path)
    for session_id in session_ids:
        _remove_prior_transcript_variants(
            sessions_dir=sessions_dir, session_id=session_id
        )


def _render_message_block(message: StoredChatMessage) -> list[str]:
    role, content = _extract_transcript_role_and_text(message)
    if role not in ("user", "assistant") or not content:
        return []
    timestamp = (message.created_at or "").strip() or "unknown time"
    label = "User" if role == "user" else "Assistant"
    return [
        f"*{timestamp}*",
        "",
        f"**{label}:**",
        f" {content}",
        "",
    ]


def _extract_transcript_role_and_text(message: StoredChatMessage) -> tuple[str, str]:
    parts = getattr(message.message, "parts", None) or []
    visible_parts: list[str] = []

    if isinstance(message.message, ModelRequest):
        for part in parts:
            if isinstance(part, UserPromptPart):
                content = getattr(part, "content", None)
                if isinstance(content, str):
                    visible_parts.append(content)
        return (
            "user",
            "\n".join(
                part.rstrip() for part in visible_parts if part and part.rstrip()
            ).strip(),
        )

    if isinstance(message.message, ModelResponse):
        for part in parts:
            if isinstance(part, TextPart) and isinstance(
                getattr(part, "content", None), str
            ):
                visible_parts.append(part.content)
        return (
            "assistant",
            "\n".join(
                part.rstrip() for part in visible_parts if part and part.rstrip()
            ).strip(),
        )

    return "", ""


def _build_frontmatter(*, session: StoredChatSession) -> list[str]:
    title = (session.title or "").strip()
    if not title:
        return []

    lines = ["---"]
    lines.append(f"title: {json.dumps(title)}")
    lines.extend(["---", ""])
    return lines


def _resolve_sessions_dir(*, vault_path: str) -> Path:
    sessions_dir = Path(vault_path) / ASSISTANTMD_ROOT_DIR / CHAT_SESSIONS_DIR
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def _build_history_file(*, sessions_dir: Path, session: StoredChatSession) -> Path:
    stem = _build_session_export_stem(session)
    history_file = sessions_dir / f"{stem}.md"
    resolved_history = history_file.resolve()
    resolved_sessions = sessions_dir.resolve()
    if resolved_sessions not in resolved_history.parents:
        raise ValueError(
            "Resolved chat history path is outside the chat sessions directory."
        )
    return history_file


def _build_session_export_stem(session: StoredChatSession) -> str:
    safe_session_id = _sanitize_filename_component(session.session_id)
    title = (session.title or "").strip()
    if not title:
        return safe_session_id
    safe_title = _sanitize_filename_component(title)
    return f"{safe_session_id} - {safe_title}"


def _remove_prior_transcript_variants(*, sessions_dir: Path, session_id: str) -> None:
    safe_session_id = _sanitize_filename_component(session_id)
    for candidate in sessions_dir.glob("*.md"):
        stem = candidate.stem
        if stem != safe_session_id and not stem.startswith(f"{safe_session_id} - "):
            continue
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            continue


def _sanitize_filename_component(value: str) -> str:
    if not value:
        return "session"
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", value)
    safe = safe.strip("._-")
    return safe or "session"
