#!/usr/bin/env python3
"""Seed the encrypted secrets database from CI environment variables."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_SYSTEM_ROOT = "system"
DEFAULT_NAMESPACE = "configuration"
CI_SECRET_KEYS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GROK_API_KEY",
    "MISTRAL_API_KEY",
    "TAVILY_API_KEY",
    "LIBRECHAT_API_KEY",
    "LOGFIRE_TOKEN",
]


def collect_secrets(keys: list[str]) -> dict[str, str]:
    """Return mapping of provided keys to non-empty environment values."""
    secrets: dict[str, str] = {}
    for key in keys:
        value = os.environ.get(key)
        if value:
            secrets[key] = value.strip()
    return secrets


def write_encrypted(system_root: str, data: dict[str, str]) -> int:
    """Write principal-owned encrypted values using the installation keyring."""
    from core.identity import LOCAL_USER_AUTHORITY, SYSTEM_AUTHORITY
    from core.secrets import EncryptedSecretsService, SecretKeyring, SecretWrite

    service = EncryptedSecretsService(
        system_root=system_root,
        keyring=SecretKeyring.from_environment(),
    )
    writes = [
        SecretWrite(
            authority=(
                SYSTEM_AUTHORITY if name == "LOGFIRE_TOKEN" else LOCAL_USER_AUTHORITY
            ),
            namespace=DEFAULT_NAMESPACE,
            name=name,
            value=value,
        )
        for name, value in data.items()
    ]
    return service.set_many_for_authorities(writes)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the encrypted secrets database from environment variables."
    )
    parser.add_argument(
        "--system-root",
        default=DEFAULT_SYSTEM_ROOT,
        help=f"System root containing access.db (default: {DEFAULT_SYSTEM_ROOT}).",
    )
    parser.add_argument(
        "--keys",
        nargs="*",
        default=CI_SECRET_KEYS,
        help="Optional override for the list of env var names to capture.",
    )
    args = parser.parse_args()

    secrets = collect_secrets(args.keys)
    if not secrets:
        print("No CI secrets provided; skipping encrypted secret seeding.")
        return

    seeded_count = write_encrypted(args.system_root, secrets)
    keys = ", ".join(sorted(secrets.keys()))
    print(f"Seeded {seeded_count} encrypted secrets ({keys})")


if __name__ == "__main__":
    main()
