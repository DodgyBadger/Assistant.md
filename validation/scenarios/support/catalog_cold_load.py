"""Concurrent fresh processes import the same real legacy configuration."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import yaml

from validation.support.core_processes import declare_core_command

SCRIPT = """
import json, sys, time
from pathlib import Path
from core.runtime.paths import set_bootstrap_roots
root=Path(sys.argv[1]); number=sys.argv[2]
set_bootstrap_roots(root/'data', root)
(root/('ready-'+number)).touch()
deadline=time.monotonic()+15
while not (root/'go').exists():
    assert time.monotonic()<deadline, 'cold-load start barrier expired'
    time.sleep(.01)
from core.settings.store import load_settings, refresh_settings_cache
first=load_settings().model_dump(mode='json')
refresh_settings_cache()
assert load_settings().model_dump(mode='json') == first
(root/('result-'+number+'.json')).write_text(json.dumps(first))
"""


async def concurrent_catalog_load(app):
    root = app.root / "concurrent-catalog"
    root.mkdir()
    original = yaml.safe_dump(
        {
            "models": {
                "gpt": {
                    "provider": "openai",
                    "model_string": "gpt-4-turbo-2024-04-09",
                    "user_editable": True,
                    "capabilities": ["text"],
                    "alternate_aliases": ["historic-cold"],
                }
            }
        }
    ).encode()
    (root / "settings.yaml").write_bytes(original)
    processes = []
    try:
        for number in range(3):
            processes.append(
                subprocess.Popen(
                    declare_core_command(
                        [sys.executable, "-c", SCRIPT, str(root), str(number)]
                    ),
                    cwd=Path(__file__).resolve().parents[3],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        async with asyncio.timeout(20):
            while len(list(root.glob("ready-*"))) != 3:
                assert all(process.poll() is None for process in processes)
                await asyncio.sleep(0.01)
        assert not (root / "settings.db").exists()
        (root / "go").touch()
        for number, process in enumerate(processes):
            stdout, stderr = await asyncio.to_thread(process.communicate, timeout=30)
            app.evidence.append(
                "concurrent-cold-load.jsonl",
                {
                    "process": number,
                    "returncode": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                },
            )
            assert process.returncode == 0, stderr
        results = [
            json.loads(path.read_text()) for path in sorted(root.glob("result-*.json"))
        ]
        backups = [
            path.read_bytes()
            for path in (root / "migration_backups").glob("settings.yaml.bak*")
        ]
        app.evidence.save_json("concurrent-catalog-results.json", results)
        return results, backups, original
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
