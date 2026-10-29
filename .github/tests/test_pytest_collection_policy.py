"""Exercise the staging policy in real, isolated pytest subprocesses."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parents[2]


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    validation = tmp_path / "validation"
    stage = validation / "scenarios"
    core = stage / "integration/core/test_behavior.py"
    fault = stage / "experiments/test_fault.py"
    core.parent.mkdir(parents=True)
    fault.parent.mkdir(parents=True)
    core.write_text("def test_first(): pass\ndef test_second(): pass\n")
    fault.write_text("def test_fault(): raise RuntimeError('intentional fault')\n")
    config = stage / "pytest.ini"
    config.write_text(
        "[pytest]\ntestpaths = missing-relative-default\naddopts = -p validation.support.pytest_policy\n"
    )
    registry = validation / "registry"
    registry.mkdir()
    entries = []
    for path, kind, tests in [
        (core, "behavioral", ["test_first", "test_second"]),
        (fault, "behavioral", ["test_fault"]),
        (config, "support", None),
    ]:
        entry: dict[str, object] = {
            "path": path.relative_to(validation).as_posix(),
            "kind": kind,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if tests is not None:
            entry["tests"] = tests
        entries.append(entry)
    (registry / "pytest-admission.json").write_text(
        json.dumps({"schema_version": 1, "files": entries})
    )
    return config, core, fault


def _run(config: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONPATH"] = str(_REPOSITORY)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(config),
            "--collect-only",
            "-q",
            *args,
        ],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize("from_staging", [False, True])
def test_default_collection_is_core_only(tmp_path: Path, from_staging: bool) -> None:
    config, _, _ = _project(tmp_path)
    # A repository-wide fallback would import this candidate and raise.
    (tmp_path / "test_unrelated.py").write_text(
        "raise RuntimeError('unexpected repository import')\n"
    )
    result = _run(config, config.parent if from_staging else tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 tests collected" in result.stdout
    assert "test_fault" not in result.stdout


@pytest.mark.parametrize("target", ["file", "node"])
def test_explicit_fault_selection_is_allowed(tmp_path: Path, target: str) -> None:
    config, _, fault = _project(tmp_path)
    selection = str(fault) + ("::test_fault" if target == "node" else "")
    result = _run(config, tmp_path, selection)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 test collected" in result.stdout


def test_fault_directory_selection_is_rejected(tmp_path: Path) -> None:
    config, _, fault = _project(tmp_path)
    result = _run(config, tmp_path, str(fault.parent))
    assert result.returncode == 4
    assert "not an admitted core directory" in result.stderr


@pytest.mark.parametrize("selection", ["file", "directory"])
def test_explicit_external_target_is_rejected_before_import(
    tmp_path: Path, selection: str
) -> None:
    config, _, _ = _project(tmp_path)
    outside = tmp_path / "outside/test_external.py"
    outside.parent.mkdir()
    outside.write_text("raise RuntimeError('must never import')\n")
    result = _run(
        config, tmp_path, str(outside if selection == "file" else outside.parent)
    )
    assert result.returncode == 4
    assert "not an admitted" in result.stderr
    assert "must never import" not in result.stdout + result.stderr


def test_source_change_requires_readmission(tmp_path: Path) -> None:
    config, core, _ = _project(tmp_path)
    core.write_text(core.read_text() + "def test_extra(): pass\n")
    result = _run(config, tmp_path)
    assert result.returncode == 4
    assert "source changed since admission" in result.stderr


def test_matching_hash_does_not_admit_extra_collected_function(tmp_path: Path) -> None:
    config, core, _ = _project(tmp_path)
    core.write_text(core.read_text() + "def test_extra(): pass\n")
    manifest = config.parent.parent / "registry/pytest-admission.json"
    payload = json.loads(manifest.read_text())
    payload["files"][0]["sha256"] = hashlib.sha256(core.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(payload))
    result = _run(config, tmp_path)
    assert result.returncode == 4
    assert "unadmitted node" in result.stderr


def test_unknown_explicit_node_is_rejected(tmp_path: Path) -> None:
    config, core, _ = _project(tmp_path)
    result = _run(config, tmp_path, str(core) + "::test_not_admitted")
    assert result.returncode == 4
    assert "not an admitted test" in result.stderr


def test_generated_run_sources_are_not_collected(tmp_path: Path) -> None:
    validation = tmp_path / "validation"
    artifacts = validation / "runs/saved-evaluation"
    artifacts.mkdir(parents=True)
    (artifacts / "test_saved_source.py").write_text(
        "raise RuntimeError('generated evidence must not be imported')\n"
    )
    (validation / "test_application.py").write_text("def test_output(): pass\n")
    (validation / "conftest.py").write_text(
        (_REPOSITORY / "validation/conftest.py").read_text()
    )
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    result = _run(config, tmp_path, str(validation))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 test collected" in result.stdout
    assert (
        "generated evidence must not be imported" not in result.stdout + result.stderr
    )


def test_core_network_guard_blocks_real_sdk_outbound_and_allows_loopback(
    tmp_path: Path,
) -> None:
    script = r"""
import asyncio
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import httpx
import openai
from validation.support.pytest_policy import CoreNetworkDenied, install_core_network_guard

install_core_network_guard()
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({"id":"chatcmpl-local","object":"chat.completion","created":1,"model":"local","choices":[{"index":0,"message":{"role":"assistant","content":"local-ok"},"finish_reason":"stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, format, *args):
        pass

async def exercise():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            local = openai.AsyncOpenAI(api_key="synthetic-test-key", base_url=f"http://127.0.0.1:{server.server_port}/v1", http_client=client, max_retries=0)
            result = await local.chat.completions.create(model="local", messages=[{"role":"user","content":"probe"}])
            assert result.choices[0].message.content == "local-ok"
            remote = openai.AsyncOpenAI(api_key="synthetic-test-key", base_url="http://1.1.1.1:9/v1", http_client=client, max_retries=0)
            try:
                await remote.chat.completions.create(model="local", messages=[{"role":"user","content":"probe"}])
            except Exception as error:
                seen = set()
                pending = [error]
                while pending:
                    current = pending.pop()
                    if id(current) in seen:
                        continue
                    seen.add(id(current))
                    if isinstance(current, CoreNetworkDenied):
                        break
                    if current.__cause__ is not None:
                        pending.append(current.__cause__)
                    if current.__context__ is not None:
                        pending.append(current.__context__)
                else:
                    raise AssertionError("SDK error did not preserve network-policy denial") from error
            else:
                raise AssertionError("outbound SDK request was not denied")
        try:
            socket.getaddrinfo("example.invalid", 443)
        except CoreNetworkDenied:
            pass
        else:
            raise AssertionError("external hostname resolution was not denied")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()

asyncio.run(exercise())
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(_REPOSITORY)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_guard_precedes_import_and_explicit_experiment_remains_unrestricted(
    tmp_path: Path,
) -> None:
    config, core, experiment = _project(tmp_path)
    core.write_text(
        "import sys\n"
        "from validation.support.pytest_policy import CoreNetworkDenied\n"
        "try:\n"
        "    sys.audit('socket.getaddrinfo', 'example.invalid', 443, 0, 0, 0)\n"
        "except CoreNetworkDenied:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('core guard missing before import')\n"
        "def test_first(): pass\ndef test_second(): pass\n"
    )
    experiment.write_text(
        "import sys\n"
        "sys.audit('socket.getaddrinfo', 'example.invalid', 443, 0, 0, 0)\n"
        "def test_fault(): pass\n"
    )
    manifest_path = config.parent.parent / "registry/pytest-admission.json"
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        if entry["path"] == core.relative_to(config.parent.parent).as_posix():
            entry["sha256"] = hashlib.sha256(core.read_bytes()).hexdigest()
        if entry["path"] == experiment.relative_to(config.parent.parent).as_posix():
            entry["sha256"] = hashlib.sha256(experiment.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    core_result = _run(config, tmp_path, str(core))
    assert core_result.returncode == 0, core_result.stdout + core_result.stderr
    experiment_result = _run(config, tmp_path, str(experiment))
    assert experiment_result.returncode == 0, (
        experiment_result.stdout + experiment_result.stderr
    )


def test_core_subprocess_guard_denies_undeclared_and_accepts_exact_fixture_command(
    tmp_path: Path,
) -> None:
    script = r"""
import subprocess, sys
from pathlib import Path
from validation.support.core_processes import (
    CoreProcessDenied, declare_core_command, install_core_process_guard, reset_core_commands,
)
root = Path(sys.argv[1])
reset_core_commands(root)
install_core_process_guard()
marker = root / 'launched.txt'
command = [sys.executable, '-c', 'from pathlib import Path;import sys;Path(sys.argv[1]).write_text("real child")', str(marker)]
try:
    subprocess.run(command, check=True)
except CoreProcessDenied:
    pass
else:
    raise AssertionError('undeclared child launched')
assert not marker.exists()
declare_core_command(command)
subprocess.run(command, check=True, timeout=5)
assert marker.read_text() == 'real child'
for kwargs, args in [({'executable': '/bin/sh'}, command), ({'shell': True}, 'echo forbidden'), ({}, ['curl','https://example.invalid'])]:
    try:
        subprocess.run(args, check=True, **kwargs)
    except CoreProcessDenied:
        pass
    else:
        raise AssertionError('undeclared launch was admitted')
reset_core_commands()
try:
    subprocess.run(command, check=True)
except CoreProcessDenied:
    pass
else:
    raise AssertionError('previous case declaration leaked')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=_REPOSITORY,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_and_experiment_targets_require_separate_invocations(
    tmp_path: Path,
) -> None:
    config, core, experiment = _project(tmp_path)
    result = _run(config, tmp_path, str(core), str(experiment))
    assert result.returncode == 4
    assert "require separate pytest invocations" in result.stdout + result.stderr
