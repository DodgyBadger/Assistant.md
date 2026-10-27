"""Run the standalone harness lifecycle checks as a validation scenario."""

import subprocess
import sys
from pathlib import Path

from validation.support.base_scenario import BaseScenario


class ValidationLifecycleScenario(BaseScenario):
    async def test_scenario(self) -> None:
        check = (
            Path(__file__).resolve().parents[4]
            / "packages/scenario-harness/tests/run_checks.py"
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                str(check),
                "lifecycle",
                str(self.artifacts_dir / "harness-checks"),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        self.teardown_scenario()
