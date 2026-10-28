"""Legacy model catalogs imported by a fresh production settings process."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from validation.scenarios.application import Application


def legacy_catalogs() -> dict[str, Any]:
    builtin_provider = {
        "api_key": "TYPESAFE_API_KEY",
        "base_url": None,
        "user_editable": False,
    }
    old_jev = {
        "provider": "typesafe",
        "model_string": "jev-latest",
        "capabilities": ["decision"],
        "user_editable": False,
    }
    cases = {
        "unused": {
            "models": {"jev": old_jev},
            "providers": {"typesafe": builtin_provider},
        },
        "referenced": {
            "models": {
                "jev": old_jev,
                "my-decision": {
                    **old_jev,
                    "model_string": "my-model",
                    "user_editable": True,
                },
            },
            "providers": {"typesafe": builtin_provider},
        },
        "customized": {
            "models": {"jev": {**old_jev, "model_string": "my-jev"}},
            "providers": {
                "typesafe": {
                    **builtin_provider,
                    "base_url": "https://custom.example.test",
                }
            },
        },
        "custom-provider": {
            "models": {"jev": {**old_jev, "managed_id": None, "user_editable": True}},
            "providers": {"typesafe": {**builtin_provider, "user_editable": True}},
        },
        "custom-unused": {
            "models": {},
            "providers": {"typesafe": {**builtin_provider, "user_editable": True}},
        },
        "customized-unused": {
            "models": {},
            "providers": {
                "typesafe": {
                    **builtin_provider,
                    "base_url": "https://custom.example.test",
                }
            },
        },
        "aliases": {
            "models": {
                name: {
                    "provider": "openai",
                    "model_string": binding,
                    "capabilities": ["text"],
                    "user_editable": True,
                    "alternate_aliases": [f"historic-{name}"],
                }
                for name, binding in (
                    ("gpt", "gpt-5.6-sol"),
                    ("gpt-mini", "gpt-5.6-terra"),
                    ("gpt-nano", "gpt-5.6-luna"),
                )
            },
            "providers": {},
        },
    }
    return cases


IMPORT_CATALOG = """
import json, sys
from pathlib import Path
from core.runtime.paths import set_bootstrap_roots
root = Path(sys.argv[1])
set_bootstrap_roots(root / 'data', root)
from core.settings.store import load_settings, refresh_settings_cache, save_settings
first = load_settings()
initial = first.model_dump(mode='json')
refresh_settings_cache()
again = load_settings()
assert again.model_dump(mode='json') == initial
if sys.argv[2] == 'aliases':
    again.models['sol'].alternate_aliases.remove('gpt')
    save_settings(again)
    refresh_settings_cache()
    assert 'gpt' not in load_settings().models['sol'].alternate_aliases
(root / 'effective.json').write_text(json.dumps(initial))
"""


def import_legacy_catalog(
    app: Application, case: str, legacy: dict[str, Any]
) -> dict[str, Any]:
    root = app.root / f"catalog-legacy-{case}"
    root.mkdir()
    original = yaml.safe_dump(legacy).encode()
    (root / "settings.yaml").write_bytes(original)
    process = subprocess.run(
        [sys.executable, "-c", IMPORT_CATALOG, str(root), case],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    app.evidence.append(
        "catalog-imports.jsonl",
        {
            "case": case,
            "returncode": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
        },
    )
    assert process.returncode == 0, process.stderr
    with sqlite3.connect(root / "settings.db") as connection:
        records = {
            kind: {
                name: json.loads(payload)
                for name, payload in connection.execute(
                    f"SELECT name, payload FROM configuration_{kind}"
                )
            }
            for kind in ("models", "providers")
        }
    result = {
        "effective": json.loads((root / "effective.json").read_text()),
        "persisted_models": records["models"],
        "persisted_providers": records["providers"],
        "backups": [
            p.read_bytes()
            for p in (root / "migration_backups").glob("settings.yaml.bak*")
        ],
        "original": original,
    }
    app.evidence.append(
        "catalog-results.jsonl",
        {"case": case, "effective": result["effective"], **records},
    )
    return result
