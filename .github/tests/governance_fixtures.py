"""Actual Git objects and a local GitHub API for manual approval checks."""

import json
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any


@contextmanager
def governance_git_fixture(root):
    from validation.support.ci.governance import GitHubAPI, finalize, run_audit

    repo = root / "governance-ci-git"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def write(path: str, content: str) -> None:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(message: str) -> str:
        git("add", "-A")
        git("commit", "-qm", message)
        return git("rev-parse", "HEAD")

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    catalog = {
        "schema_version": 1,
        "contracts": [
            {
                "id": "fixture",
                "requirement": "behavior",
                "proof_surface": "scenario",
                "review_reference": "fixture",
            }
        ],
        "scenarios": [],
        "profiles": {},
    }
    write("validation/support/ci/audit.py", "# baseline checker\n")
    write("validation/registry/scenario-catalog.json", json.dumps(catalog))
    write("app.py", "initial\n")
    base = commit("baseline")
    write("app.py", "production change\n")
    production = commit("production")
    marker = repo / "imported"
    write(
        "validation/scenarios/integration/core/sentinel.py",
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
    )
    sensitive = commit("validation change")
    state: dict[str, Any] = {
        "base": base,
        "head": production,
        "environment": True,
        "protected": True,
        "contexts": ["validation", "validation-governance"],
        "strict": True,
        "admin": True,
        "reviewer": "DodgyBadger",
        "prevent_self_review": False,
        "http_failure": False,
        "events": [],
    }
    repository = "fixture/project"

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def respond(self, code: int, value: Any) -> None:
            payload = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            state["events"].append(("POST", self.path, body))
            if self.path == "/graphql":
                self.respond(
                    200,
                    {
                        "data": {
                            "repository": {
                                "ref": {
                                    "branchProtectionRule": {
                                        "requiresStrictStatusChecks": state["strict"],
                                        "isAdminEnforced": state["admin"],
                                        "requiredStatusCheckContexts": state[
                                            "contexts"
                                        ],
                                    }
                                }
                            }
                        }
                    },
                )
            else:
                self.respond(201, body)

        def do_GET(self) -> None:
            state["events"].append(("GET", self.path, None))
            if state["http_failure"]:
                self.respond(500, {"message": "fixture failure"})
            elif self.path.endswith("/pulls/1"):
                self.respond(
                    200,
                    {
                        "state": "open",
                        "base": {
                            "ref": "main",
                            "sha": state["base"],
                            "repo": {"full_name": repository},
                        },
                        "head": {"sha": state["head"]},
                    },
                )
            elif self.path.endswith("/environments/validation-governance"):
                if not state["environment"]:
                    self.respond(404, {"message": "Not Found"})
                else:
                    self.respond(
                        200,
                        {
                            "protection_rules": [
                                {
                                    "type": "required_reviewers",
                                    "prevent_self_review": state["prevent_self_review"],
                                    "reviewers": [
                                        {
                                            "type": "User",
                                            "reviewer": {
                                                "id": 4453217,
                                                "login": state["reviewer"],
                                            },
                                        }
                                    ],
                                }
                            ],
                            "deployment_branch_policy": {
                                "protected_branches": state["protected"],
                                "custom_branch_policies": False,
                            },
                        },
                    )
            else:
                self.respond(404, {"message": "Unexpected path"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        api = GitHubAPI(f"http://127.0.0.1:{server.server_port}", "fixture-token")

        def audit(head: str) -> dict:
            state["head"] = head
            state["events"].clear()
            result = run_audit(api, repo, repository, 1, base, head)
            events = state["events"]
            assert events and events[0][0] == "POST", events
            assert events[0][2]["state"] == "pending", events
            assert events[0][2]["context"] == "validation-governance"
            assert events[0][1].endswith(f"/statuses/{head}"), events
            return result

        def finish(
            head: str, approval: str, review: str, audit_result: str = "success"
        ) -> str:
            finalize(api, repository, 1, base, head, audit_result, approval, review)
            posts = [
                event
                for event in state["events"]
                if event[0] == "POST" and "/statuses/" in event[1]
            ]
            assert posts[-1][1].endswith(f"/statuses/{head}"), posts
            assert posts[-1][2]["context"] == "validation-governance"
            return posts[-1][2]["state"]

        yield SimpleNamespace(
            state=state,
            production=production,
            sensitive=sensitive,
            base=base,
            marker=marker,
            audit=audit,
            finish=finish,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
