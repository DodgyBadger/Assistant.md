"""Application version from the metadata shipped with the application."""

import tomllib
from pathlib import Path

_PROJECT_METADATA = tomllib.loads(
    (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
)
APPLICATION_VERSION: str = _PROJECT_METADATA["project"]["version"]
