"""Diagnostic comparison of validation governance in two immutable Git trees.

This module does not grant or verify review approval. A trusted caller must
select and run trusted checker code and bind the base to its protected branch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

_CATALOG = "validation/governance/scenario-catalog.json"
_CHECKER = "validation/framework/governance_audit.py"


def _git(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repo), *args],
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Unable to read requested Git objects") from exc


def _commit(repo: Path, ref: str) -> str:
    return (
        _git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
        .decode("ascii")
        .strip()
    )


def _blob(repo: Path, commit: str, path: str) -> bytes | None:
    entry = _git(repo, "ls-tree", "-z", commit, "--", path)
    if not entry:
        return None
    metadata, name = entry.rstrip(b"\0").split(b"\t", 1)
    mode, kind, object_id = metadata.split(b" ")
    if (
        name.decode("utf-8") != path
        or mode not in {b"100644", b"100755"}
        or kind != b"blob"
    ):
        raise ValueError("Governance file is not a regular Git blob")
    return _git(repo, "cat-file", "blob", object_id.decode("ascii"))


def _catalog(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data)
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError
        contracts = value.get("contracts")
        scenarios = value.get("scenarios")
        profiles = value.get("profiles")
        if (
            not isinstance(contracts, list)
            or not isinstance(scenarios, list)
            or not isinstance(profiles, dict)
        ):
            raise ValueError
        ids: set[str] = set()
        for contract in contracts:
            if not isinstance(contract, dict) or not all(
                isinstance(contract.get(k), str) and contract[k]
                for k in ("id", "requirement", "proof_surface")
            ):
                raise ValueError
            if contract["id"] in ids:
                raise ValueError
            ids.add(contract["id"])
        selectors: set[str] = set()
        for scenario in scenarios:
            if (
                not isinstance(scenario, dict)
                or not all(
                    isinstance(scenario.get(k), str) and scenario[k]
                    for k in ("selector", "classification")
                )
                or not isinstance(scenario.get("contracts"), list)
                or not all(isinstance(item, str) for item in scenario["contracts"])
            ):
                raise ValueError
            if scenario["selector"] in selectors:
                raise ValueError
            selectors.add(scenario["selector"])
        if not all(isinstance(k, str) and k for k in profiles):
            raise ValueError
        return value
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ValueError("Malformed governance catalog") from exc


def _sensitive(path: str) -> bool:
    return path.startswith(
        ("validation/", ".github/", "scripts/", "docs/agent-guides/")
    ) or path in {
        "AGENTS.md",
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
    }


def audit_git(repo: Path, base_ref: str, candidate_ref: str = "HEAD") -> dict[str, Any]:
    """Compare Git trees and report changes needing human review."""
    base = _commit(repo, base_ref)
    candidate = _commit(repo, candidate_ref)
    reasons: list[dict[str, str]] = []

    def reason(kind: str, path: str, details: str) -> None:
        reasons.append({"kind": kind, "path": path, "details": details})

    bootstrap = False
    try:
        checker = _blob(repo, base, _CHECKER)
    except ValueError:
        checker = None
    if checker is None:
        bootstrap = True
        reason(
            "bootstrap_checker_missing",
            _CHECKER,
            "Trusted base has no regular audit checker",
        )
    catalogs: dict[str, dict[str, Any]] = {}
    blobs: dict[str, bytes] = {}
    for label, commit in (("base", base), ("candidate", candidate)):
        try:
            data = _blob(repo, commit, _CATALOG)
            if data is None:
                if label == "base":
                    bootstrap = True
                reason(
                    f"{label}_catalog_missing",
                    _CATALOG,
                    "Catalog is absent from Git tree",
                )
                continue
            blobs[label] = data
            catalogs[label] = _catalog(data)
        except ValueError:
            if label == "base":
                bootstrap = True
            reason(
                f"{label}_catalog_invalid",
                _CATALOG,
                "Catalog is malformed or is not a regular Git blob",
            )
    if "base" in catalogs and "candidate" in catalogs:
        before = catalogs["base"]
        after = catalogs["candidate"]
        old_contracts = {c["id"]: c for c in before["contracts"]}
        new_contracts = {c["id"]: c for c in after["contracts"]}
        for _contract_id in sorted(old_contracts.keys() - new_contracts.keys()):
            reason("contract_removed", _CATALOG, "A registered contract was removed")
        for contract_id in sorted(old_contracts.keys() & new_contracts.keys()):
            if (
                old_contracts[contract_id]["requirement"]
                != new_contracts[contract_id]["requirement"]
                or old_contracts[contract_id]["proof_surface"]
                != new_contracts[contract_id]["proof_surface"]
            ):
                reason(
                    "contract_proof_changed",
                    _CATALOG,
                    "A contract requirement or proof surface changed",
                )
        if blobs["base"] != blobs["candidate"]:
            reason("catalog_changed", _CATALOG, "Governance catalog changed")
    changed = _git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-renames",
        "--name-only",
        "-z",
        base,
        candidate,
        "--",
    )
    for raw_path in changed.split(b"\0"):
        if raw_path:
            path = raw_path.decode("utf-8", errors="replace")
            if _sensitive(path):
                reason(
                    "sensitive_path_changed",
                    path,
                    "Validation governance or proof surface changed",
                )
    return {
        "schema_version": 1,
        "base_commit": base,
        "candidate_commit": candidate,
        "review_required": bool(reasons),
        "bootstrap_review_required": bootstrap,
        "reasons": reasons,
        "approval_status": "not_evaluated",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--candidate-ref", default="HEAD")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = audit_git(args.repo, args.base_ref, args.candidate_ref)
    except (ValueError, UnicodeError, OSError):
        report = {
            "schema_version": 1,
            "review_required": True,
            "bootstrap_review_required": False,
            "approval_status": "not_evaluated",
            "reasons": [
                {
                    "kind": "audit_error",
                    "path": "",
                    "details": "Unable to inspect Git objects",
                }
            ],
        }
    print(json.dumps(report, indent=2 if args.json else None))
    return 2 if report["review_required"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
