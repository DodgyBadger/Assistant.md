"""Shared OpenAI SDK client construction for AssistantMD-owned retry policy."""

from __future__ import annotations

from typing import Any, cast

import httpx
from openai import AsyncOpenAI


def build_openai_sdk_client(
    *,
    api_key: Any,
    base_url: str | None,
    http_client: httpx.AsyncClient,
    default_headers: dict[str, str] | None = None,
) -> AsyncOpenAI:
    """Build an SDK client without its nested automatic retry layer."""
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers=default_headers,
        # OpenAI 3.x annotates this as its httpx2 client while Pydantic AI's
        # retry transport supplies a runtime-compatible httpx client.
        http_client=cast(Any, http_client),
        max_retries=0,
    )
