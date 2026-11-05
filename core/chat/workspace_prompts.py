"""Bounded, explicit workspace prompt libraries; never automatic context."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from markdown_it import MarkdownIt

from core.chat.workspace import normalize_workspace_path
from core.utils.frontmatter import parse_simple_frontmatter
from core.vault_state.pathing import resolve_vault_relative_path

LibraryStatus = Literal[
    "available", "no_workspace", "workspace_missing", "file_missing", "empty"
]
MAX_LIBRARY_BYTES = 256 * 1024
MAX_PROMPTS = 100


class PromptLibraryError(ValueError):
    """A controlled failure with no source content or OS paths in its message."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class SavedPrompt:
    id: str
    title: str
    body: str


@dataclass(frozen=True)
class PromptLibrary:
    workspace_path: str
    source_path: str | None
    status: LibraryStatus
    prompts: tuple[SavedPrompt, ...] = ()


def validate_prompt_filename(value: object) -> str:
    """One Markdown basename, shared by settings writes and library reads."""
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value.lower().endswith(".md")
        or value.lower() == ".md"
        or any(c in value for c in "/\\:*?[]\x00")
        or any(ord(c) < 32 for c in value)
        or ".." in value
    ):
        raise ValueError(
            "Use a single nonempty Markdown filename without directories or wildcards."
        )
    return value


def read_workspace_prompts(
    vault_root: Path, workspace_path: str, filename: object
) -> PromptLibrary:
    try:
        filename = validate_prompt_filename(filename)
    except ValueError as exc:
        raise PromptLibraryError("invalid_configuration", str(exc), 400) from exc
    try:
        normalized = normalize_workspace_path(workspace_path)
        workspace = resolve_vault_relative_path(vault_path=vault_root, path=normalized)
    except (ValueError, OSError, RuntimeError) as exc:
        raise PromptLibraryError(
            "invalid_scope", "Workspace must be a safe vault-relative folder.", 400
        ) from exc
    if not normalized:
        return PromptLibrary("", None, "no_workspace")
    try:
        if not workspace.is_dir():
            return PromptLibrary(normalized, None, "workspace_missing")
        candidate = workspace / filename
        if not candidate.exists() and not candidate.is_symlink():
            matches = [
                p
                for p in workspace.iterdir()
                if p.name.casefold() == filename.casefold()
            ]
            if len(matches) > 1:
                raise PromptLibraryError(
                    "ambiguous_filename",
                    "Multiple prompt libraries match the configured filename.",
                    409,
                )
            if not matches:
                return PromptLibrary(normalized, None, "file_missing")
            candidate = matches[0]
        resolved = candidate.resolve()
        if not resolved.is_relative_to(workspace):
            raise PromptLibraryError(
                "invalid_scope", "Prompt library must remain inside its workspace.", 400
            )
        if not resolved.is_file():
            raise PromptLibraryError(
                "invalid_file", "Prompt library must be a readable Markdown file."
            )
        with resolved.open("rb") as source:
            raw = source.read(MAX_LIBRARY_BYTES + 1)
        if len(raw) > MAX_LIBRARY_BYTES:
            raise PromptLibraryError(
                "library_too_large", "Prompt library exceeds the size limit.", 413
            )
        try:
            text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeError as exc:
            raise PromptLibraryError(
                "invalid_encoding", "Prompt library must use UTF-8."
            ) from exc
        prompts = _parse_prompts(text)
        relative = (Path(normalized) / candidate.name).as_posix()
        return PromptLibrary(
            normalized, relative, "available" if prompts else "empty", prompts
        )
    except PromptLibraryError:
        raise
    except (OSError, RuntimeError) as exc:
        raise PromptLibraryError(
            "read_failed", "Prompt library could not be read."
        ) from exc


def _parse_prompts(text: str) -> tuple[SavedPrompt, ...]:
    try:
        _, body = parse_simple_frontmatter(text)
    except ValueError as exc:
        raise PromptLibraryError(
            "invalid_format", "Prompt library frontmatter is invalid."
        ) from exc
    tokens = MarkdownIt().parse(body)
    lines = body.splitlines(keepends=True)
    boundaries: list[tuple[int, int, str, str]] = []
    for index, token in enumerate(tokens):
        if (
            token.type == "heading_open"
            and token.level == 0
            and token.tag in {"h1", "h2"}
            and token.map
        ):
            title = tokens[index + 1].content.strip()
            boundaries.append((token.map[0], token.map[1], token.tag, title))
    prompts: list[SavedPrompt] = []
    for index, (_, start, tag, title) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(lines)
        content = "".join(lines[start:end]).strip("\n")
        if tag == "h2" and title and content.strip():
            prompts.append(SavedPrompt(str(len(prompts)), title, content))
            if len(prompts) > MAX_PROMPTS:
                raise PromptLibraryError(
                    "too_many_prompts", "Prompt library exceeds the entry limit.", 413
                )
    return tuple(prompts)
