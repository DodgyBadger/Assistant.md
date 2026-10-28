"""Persist deliberately invalid legacy model records for real reload recovery."""

from typing import Any

from validation.scenarios.application import Application


def install_legacy_models(app: Application, snapshot: dict[str, Any]) -> None:
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.reload_service import reload_configuration
    from validation.support.configuration import install_configuration

    install_configuration(app.root / "system", snapshot)
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        reload_configuration()
    app.evidence.step("legacy_model_records_installed")
