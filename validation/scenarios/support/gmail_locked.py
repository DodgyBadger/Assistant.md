"""A separately started real locked installation exercises the Gmail tool boundary."""

from validation.scenarios.application import Application


def exercise_locked_gmail(app: Application) -> None:
    from validation.scenarios.support.security_migrations import (
        isolated_process,
        prepare_locked_database,
        register_synthetic_secrets,
    )

    register_synthetic_secrets(app)
    root = app.root / "locked-gmail"
    prepare_locked_database(root / "system", "missing-key")
    isolated_process(
        app,
        """
import asyncio, json, sys
from pathlib import Path
from core.runtime.paths import set_bootstrap_roots
root = Path(sys.argv[1])
set_bootstrap_roots(root / "bootstrap-data", root / "bootstrap-system")
import core.runtime.bootstrap
from core.runtime.bootstrap import bootstrap_runtime
from core.runtime.config import RuntimeConfig
from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
from core.tools.gmail import Gmail
from validation.support.configuration import seed_configuration, read_configuration, install_configuration
set_bootstrap_roots(root / "data", root / "system")
async def run():
    config = RuntimeConfig.for_validation(root, root / "data")
    seed_configuration(config.system_root)
    snapshot = read_configuration(config.system_root)
    snapshot["settings"]["default_model"] = {"value": "test", "source": "custom"}
    install_configuration(config.system_root, snapshot)
    runtime = await bootstrap_runtime(config)
    try:
        assert runtime.gmail is None
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            result = await Gmail.get_tool().function(operation="status")
        assert result.metadata["failure_kind"] == "configuration"
        assert "Unlock encrypted connections" in str(result.return_value)
        print(json.dumps({"metadata": result.metadata, "result": result.return_value}))
    finally:
        await runtime.shutdown()
asyncio.run(run())
""",
        str(root),
        key="",
    )
