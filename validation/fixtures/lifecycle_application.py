"""Small independent file-producing application for framework lifecycle checks."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from validation.framework.lifecycle import RunContext, current_run_context, execute


async def run(root: Path, mode: str) -> None:
    secret = "fixture-private-credential"
    context = RunContext(root, secrets={"credential": secret})
    loop_ids: list[int] = []

    def append(value: str) -> None:
        with (root / "resource.log").open("a") as output:
            output.write(value + "\n")

    class FileApplication:
        async def setup(self, context: RunContext) -> None:
            loop_ids.append(id(asyncio.get_running_loop()))
            assert current_run_context() is context
            assert context.secret("credential") == secret
            append("opened")
            context.defer_cleanup("file-resource", lambda: append("closed"))
            if mode == "setup_failure":
                raise ValueError(f"startup rejected {secret}")
            context.emit("application_ready", credential=secret)

        async def teardown(self, context: RunContext) -> None:
            loop_ids.append(id(asyncio.get_running_loop()))
            append("stopped")
            context.emit("application_stopped")
            if mode == "cleanup_failure":
                raise RuntimeError(f"shutdown rejected {secret}")

    async def body() -> None:
        loop_ids.append(id(asyncio.get_running_loop()))
        if mode in {"timeout", "cancel"}:
            if mode == "cancel":
                task = asyncio.current_task()
                assert task is not None
                asyncio.get_running_loop().call_soon(task.cancel)
            await asyncio.Event().wait()
        if mode in {"assertion_failure", "cleanup_failure"}:
            if mode == "cleanup_failure":

                def failed_cleanup() -> None:
                    append("failed-cleanup-attempted")
                    raise OSError(f"resource close rejected {secret}")

                context.defer_cleanup("failed-resource", failed_cleanup)
            raise AssertionError(f"missing output {secret}")
        (root / "output.md").write_text("# Actual fixture output\n")
        context.emit("output_written", path="output.md")
        if mode == "evidence_failure":
            # Exercise the actual closed-file error, without replacing the sink.
            context.close()

    outcome = await execute(
        context,
        FileApplication(),
        body,
        timeout_seconds=0.02 if mode == "timeout" else None,
    )
    assert current_run_context() is None
    report = {
        "passed": outcome.passed,
        "primary_type": (
            type(outcome.primary_error).__name__ if outcome.primary_error else None
        ),
        "primary_phase": outcome.primary_phase,
        "cancelled": outcome.cancelled,
        "timed_out": outcome.timed_out,
        "cleanup_errors": [asdict(error) for error in outcome.cleanup_errors],
        "evidence_errors": [asdict(error) for error in outcome.evidence_errors],
        "same_loop": len(set(loop_ids)) == 1,
        "application_imported": any(
            name == "core"
            or name.startswith("core.")
            or name == "api"
            or name.startswith("api.")
            for name in sys.modules
        ),
    }
    (root / "fixture-result.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]), sys.argv[2]))
