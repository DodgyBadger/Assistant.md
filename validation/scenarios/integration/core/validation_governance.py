"""Exercise governance accounting and review detection against real fixtures."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess

from validation.core.base_scenario import BaseScenario


class ValidationGovernanceScenario(BaseScenario):
    async def test_scenario(self) -> None:
        from validation.framework.governance import validate_catalog
        from validation.framework.governance_audit import audit_git

        root = self.artifacts_dir / "governance-validation"
        source_dir = root / "scenarios" / "integration" / "core"
        source_dir.mkdir(parents=True)
        marker = root / "imported"
        pilot = source_dir / "pilot.py"
        legacy = source_dir / "legacy.py"
        pilot.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        legacy.write_text("# legacy fixture\n")
        sources = {"integration/core/pilot": pilot, "integration/core/legacy": legacy}
        catalog_path = root / "governance" / "scenario-catalog.json"
        catalog_path.parent.mkdir()
        catalog = {
            "schema_version": 1,
            "contracts": [
                {
                    "id": "required",
                    "requirement": "Fixture behavior",
                    "proof_surface": "pilot",
                    "review_reference": "fixture",
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

        def check(value: dict) -> dict:
            catalog_path.write_text(json.dumps(value))
            return validate_catalog(root, sources)

        report = check(catalog)
        assert report["issues"] == [], report["issues"]
        assert report["contract_matrix"]["required"] == ["integration/core/pilot"]
        assert report["legacy_scenarios"] == 1 and report["pilot_scenarios"] == 1
        assert report["required_contracts"] == 1
        assert not marker.exists(), "Governance must not import scenario sources"

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
        write("validation/framework/governance_audit.py", "# fixture checker\n")
        write("validation/governance/scenario-catalog.json", json.dumps(catalog))
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
        review_change(
            "validation/governance/scenario-catalog.json", json.dumps(removed)
        )
        changed = copy.deepcopy(catalog)
        changed["contracts"][0]["proof_surface"] = "changed"
        review_change(
            "validation/governance/scenario-catalog.json", json.dumps(changed)
        )
        excluded = copy.deepcopy(catalog)
        excluded["scenarios"].append(
            {
                "selector": "integration/core/excluded",
                "classification": "legacy",
                "sha256": "0" * 64,
                "contracts": [],
            }
        )
        review_change(
            "validation/governance/scenario-catalog.json", json.dumps(excluded)
        )
        review_change("validation/framework/governance_audit.py", "# changed checker\n")
        review_change(".github/workflows/validation.yml", "name: fixture\n")
        review_change(".github/actions/gate/action.yml", "name: fixture\n")
        review_change("scripts/audit-npm.cjs", "// changed gate script\n")
        review_change(
            "validation/scenarios/integration/core/runs/proof.py", "# changed proof\n"
        )
