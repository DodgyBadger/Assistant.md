"""Run standalone harness checks without application imports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from checks_lifecycle import check as check_lifecycle
from checks_process import check as check_process


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "check", nargs="?", default="all", choices=("lifecycle", "process", "all")
    )
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    for name, check in (("lifecycle", check_lifecycle), ("process", check_process)):
        if args.check in (name, "all"):
            root = args.output_root / name
            root.mkdir()
            check(root)
            print(f"{name}: passed", flush=True)


if __name__ == "__main__":
    main()
