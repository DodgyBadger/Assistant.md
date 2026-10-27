"""Trusted manual-review gate for a Git-tree governance audit."""

from __future__ import annotations

import argparse
import json
import os
import re
import runpy
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote

_CONTEXT = "validation-governance"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def _failure_reason(error: Exception) -> str:
    # Policy and API helpers raise controlled messages, never response bodies.
    if isinstance(error, ValueError | RuntimeError):
        return str(error)
    return f"Gate operation failed ({type(error).__name__})"


class GitHubAPI:
    """Minimal API client; never expose response bodies or credentials on errors."""

    def __init__(self, api_url: str, token: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        request = urllib.request.Request(
            self.api_url + path,
            data=None if payload is None else json.dumps(payload).encode(),
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except (OSError, ValueError) as exc:
            raise RuntimeError("GitHub API request failed") from exc

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload)


def _validate(repository: str, pr_number: int, base_sha: str, head_sha: str) -> None:
    if (
        not _REPOSITORY.fullmatch(repository)
        or pr_number <= 0
        or not _SHA.fullmatch(base_sha)
        or not _SHA.fullmatch(head_sha)
    ):
        raise ValueError("Invalid repository, pull request, or commit identifier")


def _status(api: GitHubAPI, repository: str, head_sha: str, state: str) -> None:
    payload: dict[str, Any] = {
        "state": state,
        "context": _CONTEXT,
        "description": "Governance review " + state,
    }
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if run_id.isdigit():
        payload["target_url"] = f"{server}/{repository}/actions/runs/{run_id}"
    api.post(f"/repos/{repository}/statuses/{head_sha}", payload)


def _verify_policy(
    api: GitHubAPI, repository: str, pr_number: int, base_sha: str, head_sha: str
) -> None:
    # This file is read from the trusted base checkout alongside this script.
    # Candidate policy changes are audited as data and cannot grant approval.
    review_policy = json.loads(
        (Path(__file__).resolve().parents[1] / "governance/ci-policy.json").read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(review_policy, dict) or review_policy.get("schema_version") != 1:
        raise ValueError("Invalid trusted review policy")
    expected_reviewer = review_policy.get("reviewer")
    required_contexts = review_policy.get("required_status_contexts")
    if (
        not isinstance(expected_reviewer, dict)
        or type(expected_reviewer.get("id")) is not int
        or expected_reviewer["id"] <= 0
        or not isinstance(expected_reviewer.get("login"), str)
        or not expected_reviewer["login"]
        or not isinstance(required_contexts, list)
        or not all(
            isinstance(context, str) and context for context in required_contexts
        )
        or _CONTEXT not in required_contexts
    ):
        raise ValueError("Invalid trusted review policy")
    pr = api.get(f"/repos/{repository}/pulls/{pr_number}")
    if not isinstance(pr, dict) or pr.get("state") != "open":
        raise ValueError("Pull request is not open")
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    if (
        base.get("ref") != "main"
        or base.get("sha") != base_sha
        or head.get("sha") != head_sha
        or (base.get("repo") or {}).get("full_name", "").lower() != repository.lower()
    ):
        raise ValueError("Pull request identity changed")
    environment = api.get(
        f"/repos/{repository}/environments/{quote(_CONTEXT, safe='')}"
    )
    if not isinstance(environment, dict):
        raise ValueError("Invalid environment configuration")
    rules = environment.get("protection_rules")
    if not isinstance(rules, list):
        raise ValueError("Missing environment protection")
    reviews = [
        rule
        for rule in rules
        if isinstance(rule, dict) and rule.get("type") == "required_reviewers"
    ]
    if len(reviews) != 1 or reviews[0].get("prevent_self_review") is not False:
        raise ValueError("Invalid environment review policy")
    reviewers = reviews[0].get("reviewers")
    if not isinstance(reviewers, list) or len(reviewers) != 1:
        raise ValueError("Invalid environment reviewers")
    reviewer = reviewers[0]
    if (
        not isinstance(reviewer, dict)
        or reviewer.get("type") != "User"
        or not isinstance(reviewer.get("reviewer"), dict)
        or reviewer["reviewer"].get("id") != expected_reviewer["id"]
        or reviewer["reviewer"].get("login", "").lower()
        != expected_reviewer["login"].lower()
    ):
        raise ValueError("Invalid environment reviewer")
    policy = environment.get("deployment_branch_policy")
    if (
        not isinstance(policy, dict)
        or policy.get("protected_branches") is not True
        or policy.get("custom_branch_policies") is not False
    ):
        raise ValueError("Invalid environment branch policy")
    owner, name = repository.split("/", 1)
    response = api.post(
        "/graphql",
        {
            "query": 'query($owner:String!, $name:String!) { repository(owner:$owner, name:$name) { ref(qualifiedName:"refs/heads/main") { branchProtectionRule { requiresStrictStatusChecks isAdminEnforced requiredStatusCheckContexts } } } }',
            "variables": {"owner": owner, "name": name},
        },
    )
    if not isinstance(response, dict) or response.get("errors"):
        raise ValueError("Unable to read branch protection")
    data = response.get("data")
    if not isinstance(data, dict):
        raise ValueError("Missing branch protection data")
    repo_data = data.get("repository")
    ref = repo_data.get("ref") if isinstance(repo_data, dict) else None
    protection = ref.get("branchProtectionRule") if isinstance(ref, dict) else None
    if (
        not isinstance(protection, dict)
        or protection.get("requiresStrictStatusChecks") is not True
        or protection.get("isAdminEnforced") is not True
        or not isinstance(protection.get("requiredStatusCheckContexts"), list)
        or not all(
            context in protection["requiredStatusCheckContexts"]
            for context in required_contexts
        )
    ):
        raise ValueError("Invalid main branch protection")


def _write_action_file(name: str, value: str) -> None:
    path = os.environ.get(name)
    if path:
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(value)


def run_audit(
    api: GitHubAPI,
    repo_path: Path,
    repository: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> dict[str, Any]:
    _validate(repository, pr_number, base_sha, head_sha)
    try:
        _status(api, repository, head_sha, "pending")
        _verify_policy(api, repository, pr_number, base_sha, head_sha)
        namespace = runpy.run_path(str(Path(__file__).with_name("governance_audit.py")))
        report = namespace["audit_git"](repo_path, base_sha, head_sha)
        if (
            not isinstance(report, dict)
            or report.get("base_commit") != base_sha
            or report.get("candidate_commit") != head_sha
            or type(report.get("review_required")) is not bool
            or report.get("approval_status") != "not_evaluated"
        ):
            raise ValueError("Invalid audit report")
        (repo_path / "governance-audit.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        required = "true" if report["review_required"] else "false"
        _write_action_file("GITHUB_OUTPUT", f"review_required={required}\n")
        reasons = json.dumps(report.get("reasons"), indent=2).replace("`", "\\u0060")
        _write_action_file(
            "GITHUB_STEP_SUMMARY",
            f"Governance audit: review required = {required}. Base `{base_sha}`, head `{head_sha}`.\n\n```json\n{reasons}\n```\n",
        )
        return report
    except Exception as error:
        try:
            _status(api, repository, head_sha, "failure")
        except Exception:
            pass
        raise RuntimeError(
            f"Governance audit failed: {_failure_reason(error)}"
        ) from None


def finalize(
    api: GitHubAPI,
    repository: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    audit_result: str,
    approval_result: str,
    review_required: str,
) -> dict[str, Any]:
    _validate(repository, pr_number, base_sha, head_sha)
    success = False
    reason = "Authorized for the current PR head and base"
    try:
        if (
            audit_result != "success"
            or review_required not in {"true", "false"}
            or (review_required == "true" and approval_result != "success")
            or (review_required == "false" and approval_result != "skipped")
        ):
            raise ValueError("Audit or approval did not satisfy policy")
        _verify_policy(api, repository, pr_number, base_sha, head_sha)
        success = True
    except Exception as error:
        success = False
        reason = _failure_reason(error)
    _status(api, repository, head_sha, "success" if success else "failure")
    return {"success": success, "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("audit", "finalize"))
    parser.add_argument("--repo-path", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--audit-result", default="")
    parser.add_argument("--approval-result", default="")
    parser.add_argument("--review-required", default="")
    args = parser.parse_args()
    try:
        token = os.environ["GH_TOKEN"]
        if not token:
            raise ValueError("Missing token")
        api = GitHubAPI(
            os.environ.get("GITHUB_API_URL", "https://api.github.com"), token
        )
        if args.action == "audit":
            run_audit(
                api,
                args.repo_path,
                args.repository,
                args.pr_number,
                args.base_sha,
                args.head_sha,
            )
            return 0
        result = finalize(
            api,
            args.repository,
            args.pr_number,
            args.base_sha,
            args.head_sha,
            args.audit_result,
            args.approval_result,
            args.review_required,
        )
        if not result["success"]:
            print(result["reason"])
        return 0 if result["success"] else 1
    except Exception as error:
        print(f"Governance gate failed: {_failure_reason(error)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
