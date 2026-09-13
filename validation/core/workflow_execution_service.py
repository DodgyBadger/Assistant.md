"""
Workflow execution service for V2 validation scenarios.

Provides isolated workflow execution with complete path redirection and environment control.
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.logger import UnifiedLogger
from core.runtime.state import get_runtime_context
from core.scheduling.jobs import create_job_args

# Type definitions
VaultPath = Path


class WorkflowResult:
    """Result from workflow execution."""

    def __init__(
        self,
        status: str,
        created_files: list[str] = None,
        error_message: str = None,
        logs: list[str] = None,
    ):
        self.status = status
        self.created_files = created_files or []
        self.error_message = error_message
        self.logs = logs or []

    def __str__(self):
        return f"WorkflowResult(status={self.status}, files={len(self.created_files)})"


class WorkflowExecutionService:
    """Manages isolated workflow execution for validation scenarios."""

    def __init__(self, test_vaults_path: Path):
        """
        Initialize workflow execution service.

        Args:
            test_vaults_path: Root path for all test vaults, typically under
                validation/runs/<scenario>/test_vaults
        """
        self.test_vaults_path = test_vaults_path
        self.logger = UnifiedLogger(tag="workflow-execution-service")

    async def run_workflow(
        self,
        vault: VaultPath,
        workflow_name: str,
        step_name: str = None,
        week_start_day: int = 1,
        test_date: Any | None = None,
    ) -> WorkflowResult:
        """
        Execute workflow with complete isolation and environment control.

        Args:
            vault: Test vault path (within test_vaults)
            workflow_name: Name of workflow to execute
            step_name: Optional specific step to run
            week_start_day: Week start day (0=Monday, 6=Sunday), default Monday
            test_date: Optional datetime to override system date for testing

        Returns:
            WorkflowResult with execution status and created files
        """
        vault_name = vault.name
        global_id = f"{vault_name}/{workflow_name}"

        try:
            # Execute workflow in isolated environment
            with self._create_isolated_environment(test_date):

                # Get workflow function from runtime context workflow_loader
                runtime = get_runtime_context()
                workflow_def = runtime.workflow_loader.get_workflow_by_global_id(
                    global_id
                )
                if not workflow_def:
                    raise ValueError(
                        f"Workflow {global_id} not found in workflow_loader"
                    )

                workflow_function = workflow_def.workflow_function

                # Record files before execution
                files_before = self._get_vault_files(vault)

                self.logger.info(
                    "Workflow execution started",
                    data={
                        "global_id": global_id,
                        "vault_name": vault_name,
                        "workflow_name": workflow_name,
                        "step_name": step_name,
                    },
                )

                # Create job arguments with test data root for clean dependency injection
                job_args = create_job_args(
                    global_id,
                    workflow_def.owner_principal_id,
                    data_root=str(self.test_vaults_path),
                    file_path=workflow_def.file_path,
                )

                # Execute workflow with job arguments - clean dependency injection
                kwargs = {}
                if step_name is not None:
                    kwargs["step_name"] = step_name
                await workflow_function(job_args, **kwargs)

                # Record files after execution
                files_after = self._get_vault_files(vault)
                created_files = list(files_after - files_before)

                return WorkflowResult(status="completed", created_files=created_files)

        except Exception as e:
            self.logger.error(
                f"Workflow execution failed: {str(e)}",
                vault=vault_name,
                workflow=workflow_name,
                error_type=type(e).__name__,
            )

            return WorkflowResult(status="error", error_message=str(e))

    @contextmanager
    def _create_isolated_environment(self, test_date: Any | None = None):
        """
        Create isolated environment for workflow execution.

        Uses dependency injection for path isolation instead of global constant patching.
        """
        try:
            # Force TestModel usage for scenarios by patching general settings
            from core.settings.store import SettingsEntry
            from core.settings.store import (
                get_general_settings as _load_general_settings,
            )

            def _test_general_settings():
                original = _load_general_settings()
                mutated = dict(original)
                entry = mutated.get("default_model")
                mutated["default_model"] = SettingsEntry(
                    value="test",
                    description=getattr(entry, "description", "Validation override."),
                    restart_required=getattr(entry, "restart_required", False),
                )
                return mutated

            with patch(
                "core.llm.agents.get_general_settings",
                side_effect=_test_general_settings,
            ):
                self.logger.info(
                    "Created isolated workflow environment",
                    test_vaults_path=str(self.test_vaults_path),
                    model_override="test",
                    **({"test_date": str(test_date)} if test_date else {}),
                )
                yield

        except Exception as e:
            self.logger.error("Isolated environment failed", error=str(e))
            raise

        finally:
            self.logger.info("Restored original environment")

    def _get_vault_files(self, vault: VaultPath) -> set:
        """Get set of all files in vault (for tracking created files)."""
        vault_files = set()

        if vault.exists():
            for file_path in vault.rglob("*"):
                if file_path.is_file():
                    # Store relative path within vault
                    relative_path = file_path.relative_to(vault)
                    vault_files.add(str(relative_path))

        return vault_files

    def validate_workflow_available(self, workflow_name: str = "monty") -> bool:
        """Validate that the authoring engine is available."""
        try:
            import core.authoring.engine as engine

            return callable(engine.run_workflow)
        except (ImportError, AttributeError):
            return False

    def get_available_workflows(self) -> list[str]:
        """Get list of available workflow engines."""
        return ["monty"]
