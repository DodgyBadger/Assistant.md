"""Legacy model identity migrations preserve custom bindings and backups."""

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.legacy_model_catalog import (
    import_legacy_catalog,
    legacy_catalogs,
)
from validation.pytest_scenarios.support.model_configuration import (
    install_legacy_models,
)

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize(
    "case",
    [
        "unused",
        "referenced",
        "customized",
        "custom-provider",
        "custom-unused",
        "customized-unused",
        "aliases",
    ],
)
async def test_legacy_catalog_import_preserves_identity(
    app: Application, case: str
) -> None:
    await app.start()
    legacy_config = legacy_catalogs()[case]
    imported = import_legacy_catalog(app, case, legacy_config)
    assert imported["backups"] == [imported["original"]]
    effective = imported["effective"]
    persisted_models = imported["persisted_models"]
    persisted_providers = imported["persisted_providers"]
    if case == "unused":
        assert (
            "jev" not in effective["models"] and "typesafe" not in persisted_providers
        )
    elif case == "referenced":
        assert "jev" not in persisted_models
        assert all(
            persisted_models["my-decision"][key] == value
            for key, value in legacy_config["models"]["my-decision"].items()
        )
        assert persisted_models["my-decision"]["managed_id"] is None
        assert persisted_providers["typesafe"]["user_editable"] is True
    elif case in {"customized", "custom-provider"}:
        assert all(
            persisted_models["jev"][key] == value
            for key, value in legacy_config["models"]["jev"].items()
        )
        assert persisted_models["jev"]["managed_id"] is None
        assert persisted_providers["typesafe"]["user_editable"] is True
        assert (
            persisted_providers["typesafe"]["base_url"]
            == legacy_config["providers"]["typesafe"]["base_url"]
        )
    elif case in {"custom-unused", "customized-unused"}:
        assert persisted_models == {}
        assert persisted_providers["typesafe"]["user_editable"] is True
        assert (
            persisted_providers["typesafe"]["base_url"]
            == legacy_config["providers"]["typesafe"]["base_url"]
        )
    else:
        for old, primary in (
            ("gpt", "sol"),
            ("gpt-mini", "terra"),
            ("gpt-nano", "luna"),
        ):
            assert old not in persisted_models
            model = effective["models"][primary]
            assert model["managed_id"] == f"builtin-{old}"
            assert set(model["alternate_aliases"]) == {
                old,
                f"historic-{old}",
            }
        assert persisted_models["sol"]["alternate_aliases"] == ["historic-gpt"]


async def test_retired_managed_catalog_entry_is_removed_and_logged(
    app: Application,
) -> None:
    await app.start()
    retired = app.configuration()
    retired["models"]["jev"] = {
        "managed_id": "builtin-jev",
        "provider": "typesafe",
        "model_string": "jev-latest",
        "capabilities": ["decision"],
        "user_editable": False,
    }
    install_legacy_models(app, retired)
    assert "jev" not in app.configuration()["models"]
    response = app.request("/api/system/activity-log", params={"limit": 1000})
    assert response.status_code == 200, response.text
    assert any(
        row["data"].get("event") == "managed_models_updated"
        and "jev" in row["data"].get("removed", [])
        for row in response.json()["entries"]
    )
