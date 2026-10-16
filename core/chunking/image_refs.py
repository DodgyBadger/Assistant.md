"""
Shared embedded-image resolution and policy evaluation for markdown inputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.constants import SUPPORTED_READ_FILE_TYPES
from core.utils.image_inputs import (
    format_image_ref_marker,
    format_missing_image_marker,
    format_remote_image_ref_marker,
)
from core.utils.tokens import estimate_token_count

from .markdown import MarkdownChunk
from .policy import ChunkingPolicy


@dataclass(frozen=True)
class MarkdownImageDecision:
    attach_images: bool
    reason: str | None = None
    normalized_text: str | None = None


def resolve_local_image_path(
    *,
    image_ref: str,
    source_markdown_path: str,
    vault_path: str,
) -> Path | None:
    """Resolve local embedded image reference against a markdown source path."""
    ref = (image_ref or "").strip()
    if not ref:
        return None
    if ref.startswith(("http://", "https://")):
        return None

    vault_root = Path(vault_path).resolve()
    source_path = Path(source_markdown_path)
    source_dir = source_path.parent
    candidate = Path(ref)
    if candidate.is_absolute():
        resolved = (vault_root / str(candidate).lstrip("/")).resolve()
    else:
        resolved = (vault_root / source_dir / candidate).resolve()

    if not (resolved == vault_root or vault_root in resolved.parents):
        return None
    if resolved.is_file():
        return resolved

    if "." not in candidate.name:
        for ext, kind in SUPPORTED_READ_FILE_TYPES.items():
            if kind != "image":
                continue
            fallback = resolved.with_suffix(ext)
            if fallback.is_file():
                return fallback
    return None


def normalize_embedded_image_refs(
    *,
    markdown_chunks: Sequence[MarkdownChunk],
    source_markdown_path: str,
    vault_path: str,
) -> str:
    """Rewrite embedded image chunks into explicit followable reference markers."""
    vault_root = Path(vault_path).resolve()
    parts: list[str] = []
    for chunk in markdown_chunks:
        if chunk.kind == "text":
            parts.append(chunk.text)
            continue

        image_ref = (chunk.image_ref or "").strip()
        if image_ref.startswith(("http://", "https://")):
            parts.append(format_remote_image_ref_marker(image_ref))
            continue

        resolved = resolve_local_image_path(
            image_ref=image_ref,
            source_markdown_path=source_markdown_path,
            vault_path=vault_path,
        )
        if resolved is None:
            parts.append(format_missing_image_marker(image_ref))
            continue

        relative = resolved.relative_to(vault_root).as_posix()
        parts.append(format_image_ref_marker(relative))
    return "".join(parts)


def evaluate_markdown_image_policy(
    *,
    file_content: str,
    markdown_chunks: Sequence[MarkdownChunk],
    source_markdown_path: str,
    vault_path: str,
    auto_cache_max_tokens: int,
    policy: ChunkingPolicy,
) -> MarkdownImageDecision:
    """
    Determine whether images should be attached for this markdown input.

    Rules:
    - If raw markdown text exceeds auto-cache token limit, skip multimodal attachments.
    - Attach images only if all deduped local images satisfy policy limits.
    """
    if auto_cache_max_tokens > 0:
        raw_text_tokens = estimate_token_count(file_content)
        if raw_text_tokens > auto_cache_max_tokens:
            return MarkdownImageDecision(
                attach_images=False,
                reason=(
                    "raw text exceeds auto-cache limit "
                    f"({raw_text_tokens} > {auto_cache_max_tokens})"
                ),
                normalized_text=normalize_embedded_image_refs(
                    markdown_chunks=markdown_chunks,
                    source_markdown_path=source_markdown_path,
                    vault_path=vault_path,
                ),
            )

    unique_images: dict[str, int] = {}
    for chunk in markdown_chunks:
        if chunk.kind != "image_ref":
            continue
        image_ref = (chunk.image_ref or "").strip()
        if not image_ref or image_ref.startswith(("http://", "https://")):
            continue
        resolved = resolve_local_image_path(
            image_ref=image_ref,
            source_markdown_path=source_markdown_path,
            vault_path=vault_path,
        )
        if resolved is None:
            continue
        image_key = str(resolved)
        if image_key in unique_images:
            continue
        image_size = resolved.stat().st_size
        if (
            policy.max_image_bytes_per_image > 0
            and image_size > policy.max_image_bytes_per_image
        ):
            return MarkdownImageDecision(
                attach_images=False,
                reason="per-image size limit exceeded",
                normalized_text=normalize_embedded_image_refs(
                    markdown_chunks=markdown_chunks,
                    source_markdown_path=source_markdown_path,
                    vault_path=vault_path,
                ),
            )
        unique_images[image_key] = image_size

    if (
        policy.max_images_per_prompt > 0
        and len(unique_images) > policy.max_images_per_prompt
    ):
        return MarkdownImageDecision(
            attach_images=False,
            reason="max image count exceeded",
            normalized_text=normalize_embedded_image_refs(
                markdown_chunks=markdown_chunks,
                source_markdown_path=source_markdown_path,
                vault_path=vault_path,
            ),
        )
    if (
        policy.max_image_bytes_total > 0
        and sum(unique_images.values()) > policy.max_image_bytes_total
    ):
        return MarkdownImageDecision(
            attach_images=False,
            reason="total image byte budget exceeded",
            normalized_text=normalize_embedded_image_refs(
                markdown_chunks=markdown_chunks,
                source_markdown_path=source_markdown_path,
                vault_path=vault_path,
            ),
        )

    return MarkdownImageDecision(attach_images=True)
