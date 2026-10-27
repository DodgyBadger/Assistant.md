"""Install only the harness pinned by this trusted checkout."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path


def installation_requirement(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    source = project["tool"]["uv"]["sources"]["scenario-harness"]
    url = source.get("git")
    revision = source.get("rev")
    if not isinstance(url, str) or not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url
    ):
        raise ValueError("Harness source must be an HTTPS GitHub repository")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Harness source must pin an immutable Git commit")
    return f"scenario-harness @ git+{url}@{revision}"


def main() -> None:
    requirement = installation_requirement(Path(__file__).resolve().parents[2])
    subprocess.run(
        [sys.executable, "-I", "-m", "pip", "install", "--no-deps", requirement],
        check=True,
    )


if __name__ == "__main__":
    main()
