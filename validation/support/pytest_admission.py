"""Import-free source admission shared by inventory and staged collection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScenarioAdmission:
    relative: str
    source: Path
    kind: str
    tests: tuple[str, ...]
    hash_matches: bool


def load_admissions(root: Path) -> list[ScenarioAdmission]:
    payload = json.loads((root / "registry/pytest-admission.json").read_text())
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or not isinstance(payload.get("files"), list)
    ):
        raise ValueError("Unsupported pytest staging schema version")
    entries: list[ScenarioAdmission] = []
    seen: set[Path] = set()
    staging = (root / "scenarios").resolve()
    for entry in payload["files"]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("sha256"), str)
        ):
            raise ValueError("Invalid pytest staging source declaration")
        relative = entry["path"]
        path = Path(relative)
        source = (root / path).resolve()
        if (
            path.is_absolute()
            or ".." in path.parts
            or not relative.startswith("scenarios/")
            or source in seen
        ):
            raise ValueError(f"Invalid or duplicate pytest staging path: {relative}")
        if not source.is_file() or not source.is_relative_to(staging):
            raise ValueError(f"Missing or external pytest staging source: {relative}")
        seen.add(source)
        kind = entry["kind"]
        tests = entry.get("tests", [])
        if kind == "behavioral":
            if not any(
                source.is_relative_to(staging / profile)
                for profile in ("integration/core", "experiments", "browser")
            ):
                raise ValueError(
                    f"Behavioral source outside admitted roots: {relative}"
                )
            if (
                not isinstance(tests, list)
                or not tests
                or any(
                    not isinstance(name, str)
                    or not name.startswith("test_")
                    or "::" in name
                    for name in tests
                )
                or len(tests) != len(set(tests))
            ):
                raise ValueError(f"Invalid pytest test identities: {relative}")
        elif kind != "support":
            raise ValueError(f"Unknown pytest staging source kind: {relative}")
        elif source.name.startswith("test_"):
            raise ValueError(f"Test source cannot be admitted as support: {relative}")
        entries.append(
            ScenarioAdmission(
                relative,
                source,
                kind,
                tuple(tests),
                hashlib.sha256(source.read_bytes()).hexdigest() == entry["sha256"],
            )
        )
    return entries
