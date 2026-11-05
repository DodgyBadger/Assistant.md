"""Optional library failures are scope-correlated without retaining prompt contents."""

import json

import pytest

from validation.support.application import Application

pytestmark = pytest.mark.scenario


async def test_prompt_failures_retain_distinct_scope_and_reason_without_content(
    app: Application,
):
    vault = app.vault("PromptDiagnostics")
    for workspace in ("One", "Two"):
        app.write(vault, f"{workspace}/prompts.md", "")
        (vault / workspace / "prompts.md").write_bytes(b"\xff")
    await app.start()
    url = f"/api/vaults/{vault.name}/saved-prompts"
    for workspace in ("One", "Two", "One"):
        assert app.request(url, params={"workspace_path": workspace}).status_code == 422
    app.write(
        vault,
        "One/prompts.md",
        "---\nPRIVATE_LIBRARY_SENTINEL\n---\n## Example\nPrivate contents",
    )
    assert app.request(url, params={"workspace_path": "One"}).status_code == 422
    app.write(vault, "One/prompts.md", "## Example\nPRIVATE_LIBRARY_SENTINEL")
    assert (
        app.request(url, params={"workspace_path": "One"}).json()["status"]
        == "available"
    )
    activity = app.request("/api/system/activity-log", params={"limit": 1000})
    assert activity.status_code == 200
    rows = activity.json()["entries"]
    failures = [
        row["data"]
        for row in rows
        if (row.get("data") or {}).get("event") == "saved_prompt_discovery_failed"
    ]
    assert {(row["workspace_path"], row["code"]) for row in failures} == {
        ("One", "invalid_encoding"),
        ("Two", "invalid_encoding"),
        ("One", "invalid_format"),
    }
    assert len(failures) == 3
    assert len({row["issue"] for row in failures}) == 3
    assert all(
        row["status"] == "failed"
        and row["error_type"] == "PromptLibraryError"
        and row["error"]
        for row in failures
    )
    assert "PRIVATE_LIBRARY_SENTINEL" not in json.dumps(rows)
    assert not any(
        (row.get("data") or {}).get("event")
        in {"saved_prompt_discovery_started", "saved_prompt_discovery_completed"}
        for row in rows
    )
    app.events.require(
        "saved_prompt_discovery_completed",
        vault_name=vault.name,
        workspace_path="One",
        status="completed",
        library_status="available",
        prompt_count=1,
    )
    app.evidence.save_json("saved-prompt-system-activity.json", failures)
