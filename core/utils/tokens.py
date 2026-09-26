"""Shared approximate token counting."""

from __future__ import annotations

import tiktoken


def estimate_token_count(text: str, encoding_name: str = "cl100k_base") -> int:
    """Estimate text tokens with the shared cl100k baseline by default."""
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(text))
