from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SourceKind(str, Enum):
    FILE = "file"
    URL = "url"
    API = "api"
    MAIL = "mail"


class RenderMode(str, Enum):
    FULL = "full"
    CHUNKED = "chunked"
    SUMMARY_ONLY = "summary_only"


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class RawDocument:
    source_uri: str
    kind: SourceKind
    mime: str | None
    payload: bytes | str
    suggested_title: str | None = None
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedDocument:
    plain_text: str
    mime: str | None
    strategy_id: str
    blocks: list[dict[str, Any]] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    id: str
    order: int
    text: str
    title: str | None = None
    parent_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    hash: str | None = None


@dataclass
class RenderOptions:
    mode: RenderMode = RenderMode.FULL
    path_pattern: str = "Imported/"
    max_tokens_per_chunk: int = 0  # 0 = no limit
    overlap: int = 0
    store_original: bool = False
    title: str | None = None
    vault: str | None = None
    source_filename: str | None = None
    source_uri: str | None = None
    effective_source_uri: str | None = None
    relative_dir: str = ""
    output_name: str | None = None
