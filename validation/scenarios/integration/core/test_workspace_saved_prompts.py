"""Workspace libraries are explicit, bounded user input, independent of context."""

import pytest

from validation.support.application import Application

pytestmark = pytest.mark.scenario


async def test_workspace_library_preserves_selected_content_and_reads_current_file(
    app: Application,
) -> None:
    vault = app.vault("PromptVault")
    other = app.vault("OtherPromptVault")
    body = "Use the review skill.\n\n### Focus\nKeep this section.\n\n```markdown\n## Not a prompt\n```"
    app.write(
        vault,
        "Project/prompts.md",
        f"# Prompts\n\n## Review\n{body}\n\n## Review\nSecond body\n\n## Empty\n\n# End\nOutside body\n",
    )
    app.write(vault, "Elsewhere/prompts.md", "## Other\nOther workspace")
    app.write(other, "Project/prompts.md", "## Other\nOther vault")
    await app.start()
    url = f"/api/vaults/{vault.name}/saved-prompts"
    response = app.request(url, params={"workspace_path": "Project"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "available"
    # Exact bodies are the selected user input contract, not UI copy.
    assert [p["body"] for p in payload["prompts"]] == [body, "Second body"]
    assert len({p["id"] for p in payload["prompts"]}) == 2
    assert payload["source_path"] == "Project/prompts.md"
    assert app.request(url).json()["status"] == "no_workspace"
    assert (
        app.request(url, params={"workspace_path": "missing"}).json()["status"]
        == "workspace_missing"
    )
    app.write(vault, "Project/prompts.md", "## Changed\nUpdated body")
    assert (
        app.request(url, params={"workspace_path": "Project"}).json()["prompts"][0][
            "body"
        ]
        == "Updated body"
    )
    assert (
        app.request(url, params={"workspace_path": "Elsewhere"}).json()["prompts"][0][
            "body"
        ]
        == "Other workspace"
    )
    assert (
        app.request(
            f"/api/vaults/{other.name}/saved-prompts",
            params={"workspace_path": "Project"},
        ).json()["prompts"][0]["body"]
        == "Other vault"
    )
    (vault / "Project/prompts.md").unlink()
    assert (
        app.request(url, params={"workspace_path": "Project"}).json()["status"]
        == "file_missing"
    )
    app.write(vault, "Project/PROMPTS.MD", "No entries")
    assert (
        app.request(url, params={"workspace_path": "Project"}).json()["status"]
        == "empty"
    )
    assert (
        app.request("/api/chat/sessions", params={"vault_name": vault.name}).json()
        == []
    )


async def test_workspace_library_rejects_escape_ambiguous_and_unbounded_input(
    app: Application,
) -> None:
    vault = app.vault("PromptBoundaries")
    app.write(vault, "Project/placeholder.md", "")
    app.write(vault, "Outside.md", "## Hidden\nMust not read")
    await app.start()
    url = f"/api/vaults/{vault.name}/saved-prompts"

    def read():
        return app.request(url, params={"workspace_path": "Project"})

    assert app.request(url, params={"workspace_path": "../escape"}).status_code == 400
    path = vault / "Project/prompts.md"
    path.symlink_to(vault / "Outside.md")
    assert read().status_code == 400
    path.unlink()
    path.write_bytes(b"\xff")
    assert read().status_code == 422
    path.write_text("x" * (256 * 1024 + 1))
    assert read().status_code == 413
    path.write_text("".join(f"## Prompt {i}\nBody\n" for i in range(101)))
    assert read().status_code == 413
    path.unlink()
    app.write(vault, "Project/PROMPTS.md", "## One\nOne")
    app.write(vault, "Project/Prompts.md", "## Two\nTwo")
    assert read().status_code == 409
    app.write(vault, "Project/prompts.md", "## Exact\nExact")
    assert read().json()["prompts"][0]["body"] == "Exact"
    path.unlink()
    path.mkdir()
    assert read().status_code == 422


async def test_saved_prompt_filename_is_validated_and_persisted(
    app: Application,
) -> None:
    vault = app.vault("PromptSettings")
    app.write(vault, "Project/shortcuts.md", "## Custom\nCustom prompt")
    await app.start()
    setting = "/api/system/settings/general/workspace_prompts_filename"
    response = app.request(setting, "PUT", {"value": "shortcuts.md"})
    assert response.status_code == 200, response.text
    for invalid in (
        "../prompts.md",
        "/tmp/prompts.md",
        "folder/prompts.md",
        "*.md",
        "",
        "prompts.txt",
        "folder\\prompts.md",
    ):
        assert app.request(setting, "PUT", {"value": invalid}).status_code == 400
    await app.controller.stop_system()
    await app.start()
    rows = app.request("/api/system/settings/general").json()
    assert (
        next(r for r in rows if r["key"] == "workspace_prompts_filename")["value"]
        == "shortcuts.md"
    )
    response = app.request(
        f"/api/vaults/{vault.name}/saved-prompts", params={"workspace_path": "Project"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["prompts"][0]["body"] == "Custom prompt"
