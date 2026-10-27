"""Configurable filesystem roots for the validation harness."""

from __future__ import annotations

import os
from pathlib import Path

VALIDATION_APP_ROOT_ENV = "VALIDATION_APP_ROOT"
VALIDATION_ROOT_ENV = "VALIDATION_ROOT"


def resolve_validation_app_root() -> Path:
    """Return the application checkout used by validation utilities."""
    configured = os.environ.get(VALIDATION_APP_ROOT_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def resolve_validation_root() -> Path:
    """Return the application validation root for scenarios, fixtures and evidence."""
    configured = os.environ.get(VALIDATION_ROOT_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return resolve_validation_app_root() / "validation"


def resolve_validation_data_root() -> Path:
    """Return the bootstrap data root for the validation CLI."""
    configured = os.environ.get("CONTAINER_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return resolve_validation_app_root() / "data"


def resolve_validation_system_root() -> Path:
    """Return the bootstrap system root for the validation CLI."""
    configured = os.environ.get("CONTAINER_SYSTEM_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return resolve_validation_app_root() / "system"
