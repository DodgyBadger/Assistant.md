"""Exercise an unpackaged harness from a separate consumer project."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def check(artifacts_dir: Path) -> None:
    package = artifacts_dir / "independent_package"
    shutil.copytree(Path(__file__).resolve().parents[1] / "src", package)
    application = artifacts_dir / "unrelated_application"
    scenarios = application / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "__init__.py").write_text("")
    (scenarios / "success.py").write_text(
        "import sys\n"
        "from scenario_harness.lifecycle import current_run_context\n"
        "def test_scenario():\n"
        "    assert not any(name.split('.')[0] in {'core', 'api', 'validation'} for name in sys.modules)\n"
        "    context = current_run_context()\n"
        "    assert context is not None\n"
        "    (context.root / 'consumer-output.txt').write_text('real output')\n"
        "    context.emit('consumer_output', value=42)\n"
    )
    (scenarios / "failure.py").write_text(
        "def test_scenario():\n    raise AssertionError('consumer assertion')\n"
    )
    (application / "adapter.py").write_text(
        "class Adapter:\n"
        "    async def setup(self, context):\n"
        "        (context.root / 'adapter-setup').write_text('setup')\n"
        "    async def teardown(self, context):\n"
        "        (context.root / 'adapter-teardown').write_text('teardown')\n"
    )
    config = application / "project.toml"
    config.write_text('schema_version = 1\nroot = "."\nsuite_root = "."\n')
    launcher = artifacts_dir / "launch.py"
    launcher.write_text(
        "import runpy, sys\n"
        f"sys.path.insert(0, {str(package)!r})\n"
        "runpy.run_module('scenario_harness', run_name='__main__')\n"
    )
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-S", str(launcher), "--project", str(config), *args],
            cwd=artifacts_dir,
            env=environment,
            capture_output=True,
            text=True,
            timeout=45,
        )

    listing = cli("list")
    assert listing.returncode == 0, listing.stderr
    assert "success" in listing.stdout and "failure" in listing.stdout
    inventory = cli("inventory", "--json")
    assert inventory.returncode == 0, inventory.stderr
    discovered = json.loads(inventory.stdout)
    assert {item["selector"] for item in discovered["scenarios"]} == {
        "success",
        "failure",
    }
    run = cli("run", "success", "success")
    assert run.returncode == 0, (run.stdout, run.stderr)
    report = json.loads((application / "runs/reports/latest.json").read_text())
    assert report["expanded_scenarios"] == ["success", "success"]
    assert report["passed_scenarios"] == 2
    evidence = [Path(item["evidence_path"]) for item in report["scenario_results"]]
    assert len(set(evidence)) == 2
    for root in evidence:
        assert (root / "consumer-output.txt").read_text() == "real output"
        events = [
            json.loads(line)
            for line in (root / "artifacts/events.jsonl").read_text().splitlines()
        ]
        assert any(
            event.get("event") == "consumer_output" and event.get("value") == 42
            for event in events
        )
    failed = cli("run", "failure")
    assert failed.returncode == 1
    failure_report = json.loads((application / "runs/reports/latest.json").read_text())
    assert failure_report["scenario_results"][0]["status"] == "failed"
    assert failure_report["scenario_results"][0]["execution_phase"] == "execution"
    unknown = cli("run", "missing")
    assert unknown.returncode == 1
    config.write_text(
        'schema_version = 1\nroot = "."\nsuite_root = "."\nadapter = "adapter:Adapter"\n'
    )
    custom = cli("run", "success")
    assert custom.returncode == 0, (custom.stdout, custom.stderr)
    custom_report = json.loads((application / "runs/reports/latest.json").read_text())
    custom_root = Path(custom_report["scenario_results"][0]["evidence_path"])
    assert (custom_root / "adapter-setup").read_text() == "setup"
    assert (custom_root / "adapter-teardown").read_text() == "teardown"
    shadow = application / "shadow"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("")
    (shadow / "success.py").write_text("def test_scenario():\n    pass\n")
    config.write_text(
        'schema_version = 1\nroot = "."\nsuite_root = "."\nscenario_module = "shadow"\n'
    )
    wrong_source = cli("run", "success")
    assert wrong_source.returncode != 0
    source_report = json.loads((application / "runs/reports/latest.json").read_text())
    assert source_report["scenario_results"][0]["status"] != "passed"
    assert "source differs" in source_report["scenario_results"][0]["error_message"]
    for malformed in (
        "schema_version = 9\n",
        'schema_version = 1\nunknown_field = "x"\n',
        "schema_version = 1\nroot = [1]\n",
    ):
        config.write_text(malformed)
        rejected = cli("inventory", "--json")
        assert rejected.returncode != 0
