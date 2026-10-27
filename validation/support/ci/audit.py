"""Apply trusted repository policy to the generic Git governance audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import scenario_harness.governance_audit as harness_audit


def _policy() -> harness_audit.AuditPolicy:
    path = Path(__file__).with_name("policy.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Invalid trusted audit policy")
    audit = data.get("audit")
    if not isinstance(audit, dict):
        raise ValueError("Invalid trusted audit policy")
    for key in ("catalog_path", "checker_path"):
        value = audit.get(key)
        if (
            not isinstance(value, str)
            or not value
            or value.startswith("/")
            or ".." in Path(value).parts
        ):
            raise ValueError("Invalid trusted audit policy")
    for key in ("sensitive_prefixes", "sensitive_files"):
        value = audit.get(key)
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise ValueError("Invalid trusted audit policy")
    return harness_audit.AuditPolicy(
        catalog_path=audit["catalog_path"],
        checker_path=audit["checker_path"],
        sensitive_prefixes=tuple(audit["sensitive_prefixes"]),
        sensitive_files=tuple(audit["sensitive_files"]),
    )


def audit_git(repo: Path, base_ref: str, candidate_ref: str = "HEAD") -> dict[str, Any]:
    return harness_audit.audit_git(repo, base_ref, candidate_ref, policy=_policy())


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
