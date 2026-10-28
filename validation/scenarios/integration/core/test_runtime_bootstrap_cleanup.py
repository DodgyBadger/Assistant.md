"""Invalid startup configuration preserves its error and permits a clean restart."""

from copy import deepcopy

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_invalid_runtime_configuration_then_clean_restart(
    app: Application,
) -> None:
    app.vault("RuntimeBootstrapCleanupVault")
    await app.start()
    from core.runtime.config import RuntimeConfigError
    from core.runtime.state import has_runtime_context
    from validation.support.configuration import install_configuration

    original = app.configuration()
    invalid = deepcopy(original)
    invalid["settings"]["default_model"]["value"] = "missing-bootstrap-model"
    app.install_configuration(invalid)
    await app.controller.stop_system()
    assert not has_runtime_context()
    with pytest.raises(RuntimeConfigError) as error:
        await app.start()
    assert "setting:default_model" in str(error.value)
    assert "missing-bootstrap-model" in str(error.value)
    assert not has_runtime_context()
    assert app.controller.cleanup_failure is None
    install_configuration(app.root / "system", original)
    await app.start()
    assert has_runtime_context()
    status = app.request("/api/status")
    assert status.status_code == 200
    assert not any(
        issue["name"] == "setting:default_model" and issue["severity"] == "error"
        for issue in status.json()["configuration_status"]["issues"]
    )
