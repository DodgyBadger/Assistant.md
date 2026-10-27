"""AssistantMD validation integration and application-owned scenarios."""

import sys
from pathlib import Path

# Temporary source wiring until the harness is installed as a development
# dependency from its own repository. The reusable package does not know this path.
_harness_source = Path(__file__).resolve().parents[1] / "packages/scenario-harness/src"
if (_harness_source / "scenario_harness/__init__.py").is_file():
    sys.path.insert(0, str(_harness_source))

__all__: list[str] = []
__version__ = "2.0.0"
