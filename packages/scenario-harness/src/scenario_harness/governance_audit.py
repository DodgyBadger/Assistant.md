"""Diagnostic comparison of validation governance in two immutable Git trees.

This module does not grant or verify review approval. A trusted caller must
select and run trusted checker code and bind the base to its protected branch.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditPolicy:
    catalog_path: str
    checker_path: str
    sensitive_prefixes: tuple[str, ...]
    sensitive_files: tuple[str, ...]


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


def _sensitive(path: str, policy: AuditPolicy) -> bool:
    return path.startswith(policy.sensitive_prefixes) or path in policy.sensitive_files


def audit_git(
    repo: Path, base_ref: str, candidate_ref: str = "HEAD", *, policy: AuditPolicy
) -> dict[str, Any]:
    """Compare Git trees and report changes needing human review."""
    base = _commit(repo, base_ref)
    candidate = _commit(repo, candidate_ref)
    reasons: list[dict[str, str]] = []

    def reason(kind: str, path: str, details: str) -> None:
        reasons.append({"kind": kind, "path": path, "details": details})

    bootstrap = False
    try:
        checker = _blob(repo, base, policy.checker_path)
    except ValueError:
        checker = None
    if checker is None:
        bootstrap = True
        reason(
            "bootstrap_checker_missing",
            policy.checker_path,
            "Trusted base has no regular audit checker",
        )
    catalogs: dict[str, dict[str, Any]] = {}
    blobs: dict[str, bytes] = {}
    for label, commit in (("base", base), ("candidate", candidate)):
        try:
            data = _blob(repo, commit, policy.catalog_path)
            if data is None:
                if label == "base":
                    bootstrap = True
                reason(
                    f"{label}_catalog_missing",
                    policy.catalog_path,
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
                policy.catalog_path,
                "Catalog is malformed or is not a regular Git blob",
            )
    if "base" in catalogs and "candidate" in catalogs:
        before = catalogs["base"]
        after = catalogs["candidate"]
        old_contracts = {c["id"]: c for c in before["contracts"]}
        new_contracts = {c["id"]: c for c in after["contracts"]}
        for _contract_id in sorted(old_contracts.keys() - new_contracts.keys()):
            reason(
                "contract_removed",
                policy.catalog_path,
                "A registered contract was removed",
            )
        for contract_id in sorted(old_contracts.keys() & new_contracts.keys()):
            if (
                old_contracts[contract_id]["requirement"]
                != new_contracts[contract_id]["requirement"]
                or old_contracts[contract_id]["proof_surface"]
                != new_contracts[contract_id]["proof_surface"]
            ):
                reason(
                    "contract_proof_changed",
                    policy.catalog_path,
                    "A contract requirement or proof surface changed",
                )
        if blobs["base"] != blobs["candidate"]:
            reason("catalog_changed", policy.catalog_path, "Governance catalog changed")
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
            if _sensitive(path, policy):
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
