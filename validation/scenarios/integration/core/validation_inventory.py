"""Framework self-check: exercise current-tree accounting and import-free collection."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from validation.support.base_scenario import BaseScenario


class ValidationInventoryScenario(BaseScenario):
    async def test_scenario(self) -> None:
        root = self.artifacts_dir / "fixture-validation"
        scenarios = root / "scenarios" / "integration" / "core"
        scenarios.mkdir(parents=True)
        marker = root / "imported-application"
        scenario = scenarios / "example.py"
        scenario.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('unexpected import')\n"
            "raise RuntimeError('must not execute during collection')\n"
            "from validation.support.base_scenario import BaseScenario\n"
            "class ExampleScenario(BaseScenario):\n"
            "    async def test_scenario(self):\n"
            "        pass\n"
        )
        original = scenario.read_text()
        result = self._inventory(root)
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["scenario_count"] == 1
        assert report["issues"] == []
        assert not marker.exists(), "Collection must not import application modules"
        assert not (root / "runs").exists(), "Inventory must not create execution state"

        scenario.write_text("class Broken(\n")
        invalid = self._inventory(root)
        assert invalid.returncode != 0
        assert any(
            "syntax" in issue.lower() for issue in json.loads(invalid.stdout)["issues"]
        )
        scenario.write_text(
            original
            + "\nclass OtherScenario(BaseScenario):\n    async def test_scenario(self):\n        pass\n"
        )
        ambiguous = self._inventory(root)
        assert ambiguous.returncode != 0
        assert any(
            "exactly one" in issue for issue in json.loads(ambiguous.stdout)["issues"]
        )
        scenario.write_text(original)
        hidden = scenarios / "_unregistered.py"
        hidden.write_text(original)
        assert self._inventory(root).returncode != 0
        hidden.unlink()

        image = root / "fixtures/files/test_image.jpg"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"fixture image data")
        assert self._inventory(root).returncode == 0
        for location in ("fixtures/test_escaped.py", "templates/test_escaped.py"):
            candidate = root / location
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text("def test_required_behavior():\n    assert True\n")
            escaped_fixture = self._inventory(root)
            assert escaped_fixture.returncode != 0
            assert any(
                "unaccounted" in issue.lower()
                for issue in json.loads(escaped_fixture.stdout)["issues"]
            )
            candidate.unlink()

        root_test = root / "test_escaped.py"
        root_test.write_text("def test_required_behavior():\n    assert True\n")
        escaped = self._inventory(root)
        assert escaped.returncode != 0
        assert any(
            "unaccounted" in issue.lower()
            for issue in json.loads(escaped.stdout)["issues"]
        )
        root_test.unlink()
        ledger_dir = root / "registry"
        ledger_dir.mkdir()
        ledger = ledger_dir / "legacy-ledger.json"
        ledger.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "files": [
                        {
                            "path": "validation/test_deleted.py",
                            "sha256": "0" * 64,
                            "groups": [],
                        }
                    ],
                }
            )
        )
        stale = self._inventory(root)
        assert stale.returncode != 0
        assert any("missing" in issue for issue in json.loads(stale.stdout)["issues"])
        root_test.write_text("def test_required_behavior():\n    assert True\n")
        entry = {
            "path": "validation/test_escaped.py",
            "sha256": hashlib.sha256(root_test.read_bytes()).hexdigest(),
            "groups": [
                {
                    "id": "fixture-contract",
                    "contract": "A recorded fixture behavior",
                    "rationale": "Exercise complete source accounting",
                    "proposed_destination": "integration/core/example",
                    "disposition": "gap",
                    "tests": ["test_required_behavior"],
                }
            ],
        }
        ledger.write_text(json.dumps({"schema_version": 1, "files": [entry]}))
        accounted = self._inventory(root)
        assert accounted.returncode == 0, accounted.stdout
        accounted_report = json.loads(accounted.stdout)
        assert accounted_report["legacy_named_tests"] == 1
        assert accounted_report["structural_inventory_complete"] is True
        assert accounted_report["behavioral_coverage_complete"] is False
        root_test.write_text("def test_required_behavior():\n    assert False\n")
        changed = self._inventory(root)
        assert changed.returncode != 0
        assert any("changed" in issue for issue in json.loads(changed.stdout)["issues"])
        entry["sha256"] = hashlib.sha256(root_test.read_bytes()).hexdigest()
        entry["groups"][0]["tests"] = ["test_wrong_name"]
        ledger.write_text(json.dumps({"schema_version": 1, "files": [entry]}))
        mismatched = self._inventory(root)
        assert mismatched.returncode != 0
        assert any(
            "names" in issue for issue in json.loads(mismatched.stdout)["issues"]
        )
        root_test.write_text(
            "class TestUnsupported:\n    def test_missed(self):\n        pass\n"
        )
        unsupported = self._inventory(root)
        assert unsupported.returncode != 0
        assert any(
            "unsupported" in issue for issue in json.loads(unsupported.stdout)["issues"]
        )
        root_test.unlink()
        ledger.unlink()
        node_test = root / "test_unsupported.cjs"
        node_test.write_text("test.skip('missed', () => {});\n")
        entry["path"] = "validation/test_unsupported.cjs"
        entry["sha256"] = hashlib.sha256(node_test.read_bytes()).hexdigest()
        ledger.write_text(json.dumps({"schema_version": 1, "files": [entry]}))
        unsupported_node = self._inventory(root)
        assert unsupported_node.returncode != 0
        assert any(
            "unsupported" in issue
            for issue in json.loads(unsupported_node.stdout)["issues"]
        )
        node_test.unlink()
        ledger.unlink()
        # Moving a suite must not let it disappear from structural accounting.
        entries = []
        for category in ("legacy/browser", "legacy/pending", "infrastructure"):
            directory = root / category
            directory.mkdir(parents=True, exist_ok=True)
            candidate = directory / "test_pending.py"
            candidate.write_text("def test_pending_contract():\n    assert True\n")
            unregistered = self._inventory(root)
            assert unregistered.returncode != 0
            assert any(
                "unaccounted" in issue.lower()
                for issue in json.loads(unregistered.stdout)["issues"]
            )
            entries.append(
                {
                    "path": f"validation/{category}/test_pending.py",
                    "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    "groups": [
                        {
                            "id": f"{category}-contract",
                            "contract": "An unresolved behavioral contract",
                            "rationale": "Relocation must preserve accounting",
                            "proposed_destination": "integration/core/example",
                            "disposition": "gap",
                            "tests": ["test_pending_contract"],
                        }
                    ],
                }
            )
            ledger.write_text(json.dumps({"schema_version": 1, "files": entries}))
            registered = self._inventory(root)
            assert registered.returncode == 0, registered.stdout
        report = json.loads(registered.stdout)
        assert report["legacy_locations"] == {
            "legacy/browser": 1,
            "legacy/pending": 1,
            "infrastructure": 1,
        }
        assert report["behavioral_coverage_complete"] is False
        final = self._inventory(root)
        assert final.returncode == 0, final.stderr
        assert not marker.exists()
        self.teardown_scenario()

    def _inventory(self, root: Path) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[4] / "validation/run_validation.py"),
            "inventory",
            "--root",
            str(root),
            "--json",
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=30, check=False
        )
        with (self.artifacts_dir / "inventory-checks.jsonl").open("a") as evidence:
            evidence.write(
                json.dumps(
                    {
                        "command": command,
                        "exit_code": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }
                )
                + "\n"
            )
        return result
