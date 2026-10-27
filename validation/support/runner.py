"""AssistantMD project facade for the generic scenario runner."""

from __future__ import annotations

from pathlib import Path

from scenario_harness.runner import GenericRunner


class ValidationRunner(GenericRunner):
    def __init__(
        self,
        validation_root: str | Path | None = None,
        *,
        timeout_seconds: float | None = 300,
        cleanup_timeout_seconds: float = 30,
        environment_names: tuple[str, ...] = (),
        secret_environment_names: tuple[str, ...] = (),
    ) -> None:
        from validation.support.project import load_project

        project = load_project()
        if validation_root is not None:
            project = project.replace_suite_root(Path(validation_root))
        super().__init__(
            project,
            timeout_seconds=timeout_seconds,
            cleanup_timeout_seconds=cleanup_timeout_seconds,
            environment_names=environment_names,
            secret_environment_names=secret_environment_names,
        )
