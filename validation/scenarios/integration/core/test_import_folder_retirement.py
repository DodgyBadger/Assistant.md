"""Intentional imports honor destinations and preserve source documents."""

from __future__ import annotations

import json

import pytest

from validation.support.application import Application
from validation.support.integrations.external_web import external_web
from validation.support.runtime.ingestion_inputs import pdf_source

pytestmark = pytest.mark.scenario


async def test_url_import_defaults_to_vault_root(
    app: Application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = app.vault("ImportFolderStartupVault")
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.tools.content_import import ContentImport

    # Only remote DNS/curl responses are controlled; URL admission, extraction,
    # rendering, queued processing, file mutations and output paths remain real.
    with (
        external_web(monkeypatch, app) as remote,
        use_execution_authority(LOCAL_USER_AUTHORITY),
    ):
        url = "https://documents.example/intentional"
        remote.serve(
            url,
            b"<html><title>Intentional</title><body><p>Selected import content.</p></body></html>",
        )
        result = await ContentImport.get_tool(vault_path=str(vault)).function(
            operation="submit", sources=url
        )
        item = json.loads(str(result.return_value))["items"][0]
        assert item["status"] == "completed", item
        output = vault / item["outputs"][0]
        assert output.parent == vault
        assert "Selected import content." in output.read_text()


async def test_root_and_explicit_import_destinations_preserve_existing_folders(
    app: Application,
) -> None:
    vault = app.vault("ImportDestinationRetirementVault")
    sentinels = {
        "AssistantMD/Import/keep.md": "old inbox content",
        "Imported/keep.md": "old output content",
        "AssistantMD/Imported/keep.md": "user folder content",
    }
    for path, content in sentinels.items():
        app.write(vault, path, content)
    source = pdf_source(vault, "Sources/source.pdf", "Root destination content")
    original = source.read_bytes()
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.tools.content_import import ContentImport

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        tool = ContentImport.get_tool(vault_path=str(vault))
        for destination in (None, "Library/Selected"):
            options = {"strategies": ["pdf_text"]}
            if destination is not None:
                options["destination"] = destination
            result = await tool.function(
                operation="submit", sources="Sources/source.pdf", options=options
            )
            item = json.loads(str(result.return_value))["items"][0]
            assert item["status"] == "completed", item
            output = vault / item["outputs"][0]
            assert output.parent == (
                vault if destination is None else vault / destination
            )
            assert "Root destination content" in output.read_text()
            assert source.read_bytes() == original
    for path, content in sentinels.items():
        assert (vault / path).read_text() == content
