#!/usr/bin/env python3
"""AssistantMD entry point for the shared scenario harness."""

import importlib
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    importlib.import_module("validation")
    from scenario_harness.cli import main

    from validation.support.project import load_project

    raise SystemExit(main(project=load_project()))
