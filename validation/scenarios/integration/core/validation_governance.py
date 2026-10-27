"""Exercise governance accounting and review detection against real fixtures."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess

from validation.support.base_scenario import BaseScenario


class ValidationGovernanceScenario(BaseScenario):
    async def test_scenario(self) -> None:
        from scenario_harness.governance import (
            contract_fingerprint,
            validate_catalog,
        )

        from validation.support.ci.audit import audit_git

        root = self.artifacts_dir / "governance-validation"
        source_dir = root / "scenarios" / "integration" / "core"
        source_dir.mkdir(parents=True)
        marker = root / "imported"
        pilot = source_dir / "pilot.py"
        legacy = source_dir / "legacy.py"
        pilot.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        legacy.write_text("# legacy fixture\n")
        sources = {"integration/core/pilot": pilot, "integration/core/legacy": legacy}
        catalog_path = root / "registry" / "scenario-catalog.json"
        catalog_path.parent.mkdir()
        catalog = {
            "schema_version": 1,
            "contracts": [
                {
                    "id": "required",
                    "requirement": "Fixture behavior",
                    "proof_surface": "pilot",
                    "review_reference": "fixture",
                    "review_evidence": ["reviews/pilot-review.json"],
                }
            ],
            "scenarios": [
                {
                    "selector": "integration/core/pilot",
                    "classification": "pilot",
                    "contracts": ["required"],
                },
                {
                    "selector": "integration/core/legacy",
                    "classification": "legacy",
                    "sha256": hashlib.sha256(legacy.read_bytes()).hexdigest(),
                    "contracts": [],
                },
            ],
            "profiles": {
                "integration/core": {
                    "selectors": ["integration/core"],
                    "required_contracts": ["required"],
                }
            },
        }
        review_path = catalog_path.parent / "reviews/pilot-review.json"
        review_path.parent.mkdir()
        review = {
            "schema_version": 1,
            "contract_id": "required",
            "contract_fingerprint": contract_fingerprint(catalog["contracts"][0]),
            "scenario": "integration/core/pilot",
            "scenario_sha256": hashlib.sha256(pilot.read_bytes()).hexdigest(),
            "source_revision": "1" * 40,
            "proof_summary": ["The fixture checks a persisted outcome."],
            "exclusions": ["This fixture is metadata, not semantic admission."],
            "baseline": {
                "observation": {
                    "status": "passed",
                    "scenario_name": "integration/core/pilot",
                }
            },
            "negative_examples": [
                {
                    "fault": "An intentionally wrong result",
                    "assertion": "The scenario rejects the wrong result",
                    "observation": {
                        "status": "failed",
                        "execution_phase": "execution",
                        "scenario_name": "integration/core/pilot",
                        "error_message": "AssertionError: wrong outcome",
                    },
                }
            ],
        }
        review_path.write_text(json.dumps(review))

        def check(value: dict) -> dict:
            catalog_path.write_text(json.dumps(value))
            return validate_catalog(root, sources, registry_root=root / "registry")

        report = check(catalog)
        assert report["issues"] == [], report["issues"]
        assert report["contract_matrix"]["required"] == ["integration/core/pilot"]
        assert report["legacy_scenarios"] == 1 and report["pilot_scenarios"] == 1
        assert report["required_contracts"] == 1
        assert not marker.exists(), "Governance must not import scenario sources"
        review_path.unlink()
        assert check(catalog)["issues"], "Missing review evidence must fail"
        review_path.write_text(json.dumps(review))
        invalid = copy.deepcopy(catalog)
        invalid["contracts"][0]["requirement"] = "A different behavior"
        assert check(invalid)["issues"], "A changed requirement needs renewed evidence"
        invalid = copy.deepcopy(catalog)
        invalid["contracts"][0]["proof_surface"] = "A different proof boundary"
        assert check(invalid)[
            "issues"
        ], "A changed proof surface needs renewed evidence"
        original_pilot = pilot.read_text()
        pilot.write_text(original_pilot + "# changed oracle\n")
        assert check(catalog)["issues"], "A changed oracle needs renewed evidence"
        pilot.write_text(original_pilot)
        for mutate in (
            "no_negative",
            "setup_failure",
            "passing_negative",
            "stale_hash",
            "wrong_identity",
            "unclean_baseline",
        ):
            invalid_review = copy.deepcopy(review)
            if mutate == "no_negative":
                invalid_review["negative_examples"] = []
            elif mutate == "setup_failure":
                invalid_review["negative_examples"][0]["observation"][
                    "execution_phase"
                ] = "setup"
            elif mutate == "passing_negative":
                invalid_review["negative_examples"][0]["observation"][
                    "status"
                ] = "passed"
            elif mutate == "stale_hash":
                invalid_review["scenario_sha256"] = "0" * 64
            elif mutate == "unclean_baseline":
                invalid_review["baseline"]["observation"]["timed_out"] = True
            else:
                invalid_review["contract_id"] = "another.contract"
            # A branch-authored approval claim cannot override invalid evidence.
            invalid_review["approved"] = True
            review_path.write_text(json.dumps(invalid_review))
            assert check(catalog)["issues"], (
                mutate,
                "Invalid review evidence must fail",
            )
        review_path.write_text(json.dumps(review))

        invalid = copy.deepcopy(catalog)
        invalid["scenarios"][0]["contracts"] = ["unknown"]
        assert check(invalid)["issues"], "Unknown contract must fail"
        invalid = copy.deepcopy(catalog)
        invalid["contracts"].append(copy.deepcopy(invalid["contracts"][0]))
        assert check(invalid)["issues"], "Duplicate contract must fail"
        invalid = copy.deepcopy(catalog)
        invalid["scenarios"].append(copy.deepcopy(invalid["scenarios"][0]))
        assert check(invalid)["issues"], "Duplicate selector must fail"
        invalid = copy.deepcopy(catalog)
        invalid["scenarios"][0]["contracts"] = []
        invalid["scenarios"][1]["contracts"] = ["required"]
        assert check(invalid)[
            "issues"
        ], "Legacy coverage cannot satisfy a required contract"
        invalid = copy.deepcopy(catalog)
        invalid["profiles"]["integration/core"]["selectors"] = ["integration/other"]
        assert check(invalid)["issues"], "Profile escape must fail"
        invalid = copy.deepcopy(catalog)
        invalid["scenarios"] = invalid["scenarios"][1:]
        removed = sources.pop("integration/core/pilot")
        assert check(invalid)["issues"], "Deleting the last proof must fail"
        sources["integration/core/pilot"] = removed
        extra = source_dir / "extra.py"
        extra.write_text("# unregistered\n")
        sources["integration/core/extra"] = extra
        assert check(catalog)["issues"], "Unregistered source must fail"
        del sources["integration/core/extra"]
        legacy.write_text("# changed legacy fixture\n")
        assert check(catalog)["issues"], "Changed legacy source must fail"

        repo = self.artifacts_dir / "governance-git"
        repo.mkdir()

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(repo), *args],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        def write(path: str, contents: str) -> None:
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents)

        def commit(message: str) -> str:
            git("add", "-A")
            git("commit", "-qm", message)
            return git("rev-parse", "HEAD")

        git("init", "-q")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        write("app.py", "initial\n")
        empty_base = commit("before governance")
        write("validation/support/ci/audit.py", "# fixture checker\n")
        write("validation/registry/scenario-catalog.json", json.dumps(catalog))
        base = commit("governance baseline")
        assert audit_git(repo, empty_base, base)["bootstrap_review_required"]
        write("app.py", "changed\n")
        production = commit("production change")
        assert not audit_git(repo, base, production)["review_required"]

        def review_change(path: str, contents: str) -> None:
            git("reset", "--hard", base)
            write(path, contents)
            candidate = commit("review fixture")
            result = audit_git(repo, base, candidate)
            assert result["review_required"], (path, result)
            assert result["reasons"]

        removed = copy.deepcopy(catalog)
        removed["contracts"] = []
        review_change("validation/registry/scenario-catalog.json", json.dumps(removed))
        changed = copy.deepcopy(catalog)
        changed["contracts"][0]["proof_surface"] = "changed"
        review_change("validation/registry/scenario-catalog.json", json.dumps(changed))
        excluded = copy.deepcopy(catalog)
        excluded["scenarios"].append(
            {
                "selector": "integration/core/excluded",
                "classification": "legacy",
                "sha256": "0" * 64,
                "contracts": [],
            }
        )
        review_change("validation/registry/scenario-catalog.json", json.dumps(excluded))
        review_change("validation/support/ci/audit.py", "# changed checker\n")
        review_change(".github/workflows/validation.yml", "name: fixture\n")
        review_change(".github/actions/gate/action.yml", "name: fixture\n")
        review_change("scripts/audit-npm.cjs", "// changed gate script\n")
        review_change(
            "validation/scenarios/integration/core/runs/proof.py", "# changed proof\n"
        )
