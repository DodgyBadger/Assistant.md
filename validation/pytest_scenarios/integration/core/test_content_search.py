"""Existing vault behavior through real application boundaries."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_literal_content_search_bounds_and_scope(app: Application) -> None:
    from validation.pytest_scenarios.support.vault_state import search_content

    vault = app.vault("VaultContentSearchVault")
    app.write(
        vault,
        "Notes/name:with:colons.md",
        "First line\nLiteral [query] appears here\nLiteral [query] again\n",
    )
    app.write(vault, "Outside.md", "Literal [query] outside\n")
    app.write(vault, "Notes/.hidden/secret.md", "Literal [query] hidden\n")
    (vault / "Notes/binary.bin").write_bytes(b"\x00Literal [query] binary\x00")
    app.write(vault, "Unicode.md", "é needle and --hidden\n")
    await app.start()

    response = app.request(
        f"/api/vaults/{vault.name}/content-search",
        params={"query": "[QUERY]", "path": "Notes", "limit": 1},
    )
    assert response.status_code == 200, "Content search should succeed"
    payload = response.json()
    assert payload.get("path") == "Notes", "Response preserves scope"
    assert payload.get("query") == "[QUERY]", "Response preserves the literal query"
    assert payload.get("truncated") is True, "A global result limit reports truncation"
    matches = payload.get("matches", [])
    assert len(matches) == 1, "The requested match limit is enforced"
    assert (
        matches[0].get("path") == "Notes/name:with:colons.md"
    ), "Structured paths support filenames containing colons"
    assert matches[0].get("line") == 2, "Line numbers are structured"
    assert "Outside.md" not in {
        match.get("path") for match in matches
    }, "Folder scope excludes matches elsewhere in the vault"

    complete_response = app.request(
        f"/api/vaults/{vault.name}/content-search",
        params={"query": "[QUERY]", "path": "Notes", "limit": 100},
    )
    complete_paths = {
        match.get("path") for match in complete_response.json().get("matches", [])
    }
    assert all(
        ".hidden" not in path for path in complete_paths if path
    ), "Hidden files are excluded"
    assert "Notes/binary.bin" not in complete_paths, "Binary files are excluded"

    tool_result = search_content(app, vault, path="Notes", search_term="[QUERY]")
    assert any(
        match.startswith("Notes/name:with:colons.md:2:")
        for match in tool_result.metadata.get("matches", [])
    ), "The file tool adapter uses the same literal structured search service"
    missing_query = app.request(
        f"/api/vaults/{vault.name}/content-search", params={"query": "   "}
    )
    assert missing_query.status_code == 400, "Blank content queries are rejected"
    assert (
        missing_query.json().get("details", {}).get("code") == "missing_query"
    ), "Blank query rejection has a stable code"
    invalid_query = app.request(
        f"/api/vaults/{vault.name}/content-search", params={"query": "x" * 501}
    )
    assert invalid_query.status_code == 400, "Oversized content queries are rejected"
    assert (
        invalid_query.json().get("details", {}).get("code") == "invalid_query"
    ), "Invalid query rejection has a stable code"
    for query, column in (("needle", 3), ("--hidden", 14)):
        literal = app.request(
            f"/api/vaults/{vault.name}/content-search", params={"query": query}
        )
        assert literal.status_code == 200
        assert literal.json()["matches"] == [
            {
                "path": "Unicode.md",
                "line": 1,
                "column": column,
                "snippet": "é needle and --hidden",
            }
        ]
    for query in ("needle\x00suffix", "x" * 501):
        rejected = app.request(
            f"/api/vaults/{vault.name}/content-search", params={"query": query}
        )
        assert rejected.status_code == 400
        assert rejected.json().get("details", {}).get("code") == "invalid_query"
    outside_scope = app.request(
        f"/api/vaults/{vault.name}/content-search",
        params={"query": "needle", "path": "../outside"},
    )
    assert outside_scope.status_code == 400
    assert outside_scope.json().get("details", {}).get("code") == "invalid_scope"
