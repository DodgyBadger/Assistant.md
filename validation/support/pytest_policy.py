"""Unprivileged collection admission for the canonical application profile."""

from __future__ import annotations

import ipaddress
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from validation.support.pytest_admission import load_admissions

_ADMITTED: pytest.StashKey[dict[Path, set[str]]] = pytest.StashKey()


class CoreNetworkDenied(PermissionError):
    """A deterministic core scenario attempted an outbound Python socket."""


_network_guard_installed = False


def _loopback_host(host: object) -> bool:
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str):
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _core_network_audit(event: str, args: tuple[object, ...]) -> None:
    if event == "socket.getaddrinfo":
        if not _loopback_host(args[0]):
            raise CoreNetworkDenied(
                "Core scenario network policy permits only loopback or Unix sockets."
            )
    elif event == "socket.connect":
        sock, address = args
        if isinstance(sock, socket.socket) and sock.family == socket.AF_UNIX:
            return
        if (
            not isinstance(address, tuple)
            or not address
            or not _loopback_host(address[0])
        ):
            raise CoreNetworkDenied(
                "Core scenario network policy permits only loopback or Unix sockets."
            )


def install_core_network_guard() -> None:
    """Install an irreversible, process-wide guard before core scenario imports."""
    global _network_guard_installed
    if not _network_guard_installed:
        sys.addaudithook(_core_network_audit)
        _network_guard_installed = True


def _admissions(root: Path) -> dict[Path, set[str]]:
    try:
        admitted: dict[Path, set[str]] = {}
        for entry in load_admissions(root):
            if not entry.hash_matches:
                raise ValueError(f"source changed since admission: {entry.relative}")
            if entry.kind == "behavioral":
                admitted[entry.source] = set(entry.tests)
        return admitted
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise pytest.UsageError(f"Canonical pytest admission: {error}") from error


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "scenario_profile", "core or browser application profile", default="core"
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.inipath is None:
        raise pytest.UsageError(
            "Canonical pytest policy requires a project configuration"
        )
    if config.inipath.name == "pyproject.toml":
        staging = config.inipath.parent / "validation/scenarios"
    elif (
        config.inipath.parent.name == "browser"
        and config.inipath.parent.parent.name == "scenarios"
    ):
        staging = config.inipath.parent.parent.resolve()
    elif config.inipath.parent.name == "scenarios":
        staging = config.inipath.parent.resolve()
    else:
        raise pytest.UsageError(
            "Canonical pytest policy requires the application configuration"
        )
    if not config.getoption("scenario_root", default=None) and not hasattr(
        config, "workerinput"
    ):
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        config.option.scenario_root = str(
            staging.parent / "runs" / f"pytest_{stamp}_{uuid4().hex[:10]}"
        )
    core = staging / "integration/core"
    browser = staging / "browser"
    profile = config.getini("scenario_profile")
    if profile not in {"core", "browser"}:
        raise pytest.UsageError("scenario_profile must be core or browser")
    selected_root = browser if profile == "browser" else core
    if profile == "browser":
        worker_input = getattr(config, "workerinput", {})
        evidence_root = worker_input.get("scenario_evidence_root") or config.getoption(
            "scenario_root"
        )
        config.option.output = str(
            Path(evidence_root)
            / "playwright-output"
            / worker_input.get("workerid", "main")
        )
        if not worker_input and config.getoption("numprocesses", default=0) not in {
            0,
            1,
        }:
            raise pytest.UsageError(
                "The browser pilot requires one local worker; parallel browser ownership is not yet verified"
            )
    admitted = _admissions(staging.parent)
    config.stash[_ADMITTED] = admitted
    # Pytest interprets testpaths relative to invocation cwd. Normalize defaults
    # before collection so a staging config cannot fall back to the repository.
    if config.args_source != pytest.Config.ArgsSource.ARGS:
        config.args[:] = [str(selected_root)]
    for argument in config.args:
        raw_path, *selection = argument.split("::")
        path = (config.invocation_params.dir / raw_path).resolve()
        if path.is_dir():
            if not path.is_relative_to(selected_root) or not any(
                source.is_relative_to(path) for source in admitted
            ):
                raise pytest.UsageError(
                    f"Canonical pytest target is not an admitted {profile} directory: {argument}"
                )
        elif path not in admitted:
            raise pytest.UsageError(
                f"Canonical pytest target is not an admitted behavioral file: {argument}"
            )
        elif selection and "::".join(selection).split("[", 1)[0] not in admitted[path]:
            raise pytest.UsageError(
                f"Canonical pytest target is not an admitted test: {argument}"
            )
    selected_paths = [
        (config.invocation_params.dir / arg.split("::", 1)[0]).resolve()
        for arg in config.args
    ]
    if profile == "browser" and any(
        not p.is_relative_to(browser) for p in selected_paths
    ):
        raise pytest.UsageError(
            "Browser scenarios require a separate browser-profile invocation"
        )
    if profile != "browser" and any(p.is_relative_to(browser) for p in selected_paths):
        raise pytest.UsageError(
            "Browser scenarios require their browser-profile configuration"
        )
    if any(p.is_relative_to(core) for p in selected_paths) and any(
        not p.is_relative_to(core) for p in selected_paths
    ):
        raise pytest.UsageError(
            "Core and external/experiment profiles require separate pytest invocations"
        )
    if any(
        (config.invocation_params.dir / argument.split("::", 1)[0])
        .resolve()
        .is_relative_to(selected_root)
        for argument in config.args
    ):
        install_core_network_guard()
        # The controller must still create its xdist workers; app execution is
        # confined to workers (or the explicitly selected in-process run).
        if hasattr(config, "workerinput") or not config.getoption(
            "numprocesses", default=0
        ):
            from validation.support.core_processes import install_core_process_guard

            install_core_process_guard()


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    admitted = config.stash[_ADMITTED]
    for item in items:
        identity = "::".join(item.nodeid.split("::")[1:]).split("[", 1)[0]
        if identity not in admitted.get(item.path.resolve(), set()):
            raise pytest.UsageError(
                f"Canonical pytest collected an unadmitted node: {item.nodeid}"
            )
