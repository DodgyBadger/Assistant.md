"""Private single-scenario worker entry point; never dispatch another worker."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TextIO

from validation.core.runner import ValidationRunner
from validation.framework.lifecycle import current_run_context


class _RedactedStream:
    def __init__(self, stream: TextIO, secrets: dict[str, str]):
        self._stream = stream
        self._secrets = sorted(
            (v for v in secrets.values() if v), key=len, reverse=True
        )

    def write(self, value: str) -> int:
        original_length = len(value)
        context = current_run_context()
        if context is not None:
            value = context.redact(value)
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        self._stream.write(value)
        return original_length

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def main() -> int:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if request["schema_version"] != 1:
        raise ValueError("Unsupported worker request")
    secrets = {name: os.environ[name] for name in request["secret_environment_names"]}
    # Install before any application imports. Environment inputs are always
    # redacted; the active lifecycle additionally projects registered values.
    sys.stdout = _RedactedStream(sys.stdout, secrets)
    sys.stderr = _RedactedStream(sys.stderr, secrets)
    root = Path(request["evidence_root"])
    runner = ValidationRunner(
        request["validation_root"],
        timeout_seconds=request["timeout_seconds"],
        cleanup_timeout_seconds=request["cleanup_timeout_seconds"],
    )
    result = runner._execute_scenario_in_process(
        request["scenario_name"], root, secret_inputs=secrets
    )
    target = root / "worker-result.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"schema_version": 1, "result": result.to_dict()}), encoding="utf-8"
    )
    temporary.replace(target)
    return 130 if result.interrupted else 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
