"""Assertions over published event envelopes, independent of logger implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from .evidence import Evidence


def contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and contains(actual[key], value)
            for key, value in expected.items()
        )
    return bool(actual == expected)


@dataclass
class Observations:
    evidence: Evidence
    records: list[dict[str, Any]] = field(default_factory=list)
    _lock: Any = field(default_factory=RLock, repr=False)

    def capture(self, record: dict[str, Any]) -> None:
        with self._lock:
            projected = self.evidence.redact(record)
            self.records.append(projected)
            self.evidence.append("application-events.jsonl", projected)

    def checkpoint(self) -> int:
        with self._lock:
            return len(self.records)

    def require(self, name: str, *, after: int = 0, **fields: Any) -> dict[str, Any]:
        with self._lock:
            candidates = [
                record for record in self.records[after:] if record.get("name") == name
            ]
        matches = [
            record for record in candidates if contains(record.get("data", {}), fields)
        ]
        query = {
            "event": name,
            "expected": fields,
            "after": after,
            "matched": bool(matches),
            "candidates": candidates,
        }
        self.evidence.append("event-assertions.jsonl", query)
        assert (
            matches
        ), f"Missing application event {name!r} matching {fields!r}; observed candidates: {candidates!r}"
        return matches[-1]
