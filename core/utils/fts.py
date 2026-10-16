"""Shared helpers for safe, tolerant SQLite FTS5 queries."""

from __future__ import annotations

import re
import unicodedata


def build_fts_query(value: str, *, max_terms: int = 12) -> str:
    """Build a quoted OR query from user- or model-shaped search text."""
    terms = fts_query_terms(value, max_terms=max_terms)
    return " OR ".join(_quote_fts_phrase(term) for term in terms)


def fts_query_terms(value: str, *, max_terms: int = 12) -> list[str]:
    """Extract bounded, deduplicated phrases and tokens for an FTS query."""
    if max_terms <= 0:
        return []
    normalized_value = unicodedata.normalize("NFC", value)
    phrases = [
        phrase.strip().lower()
        for phrase in re.findall(r'"([^"]+)"', normalized_value)
        if phrase.strip()
    ]
    without_phrases = re.sub(r'"[^"]+"', " ", normalized_value)
    tokens = [
        token.lower() for token in re.findall(r"[^\W_][\w-]{1,}", without_phrases)
    ]
    parts = [*phrases, *tokens]
    deduped: list[str] = []
    for part in parts:
        if part not in deduped:
            deduped.append(part)
        if len(deduped) >= max_terms:
            break
    return deduped


def _quote_fts_phrase(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'
