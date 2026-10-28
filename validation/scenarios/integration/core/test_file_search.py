"""Existing file-operation behavior through real application boundaries."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.file_boundaries import search

pytestmark = pytest.mark.scenario


async def test_search_all_text_files_with_literal_queries_and_caps(
    app: Application,
) -> None:
    vault = app.vault("FileReadSearchTextFilesVault")
    app.write(vault, "notes/alpha.md", "needle in markdown\n")
    app.write(vault, "notes/bravo.txt", "needle in text\n")
    app.write(vault, "notes/charlie.json", '{"value": "needle in json"}\n')
    app.write(vault, "notes/.hidden.txt", "needle hidden\n")
    app.write(vault, "notes/nomatch.txt", "nothing here\n")
    app.write(
        vault, "notes/literal.txt", "literal a.*b and --hidden option-like text\n"
    )
    app.write(vault, "notes/many.txt", "\n".join(["many hit"] * 5))
    await app.start()

    broad = search(app, vault, path="notes", search_term="needle")
    broad_matches = set(broad.metadata.get("matches") or [])
    assert (
        broad.metadata.get("status") == "completed"
    ), "Search should complete across a directory of text files"
    assert any(
        match.startswith("notes/alpha.md:") for match in broad_matches
    ), "Search should include markdown matches"
    assert any(
        match.startswith("notes/bravo.txt:") for match in broad_matches
    ), "Search should include non-markdown text matches"
    assert any(
        match.startswith("notes/charlie.json:") for match in broad_matches
    ), "Search should include JSON text matches"
    assert not any(
        ".hidden.txt" in match for match in broad_matches
    ), "Search should continue to exclude hidden files by default"

    explicit_file = search(app, vault, path="notes/bravo.txt", search_term="needle")
    assert any(
        match.startswith("notes/bravo.txt:")
        for match in explicit_file.metadata.get("matches") or []
    ), "Search should work when scoped to an explicit non-markdown file"

    explicit_glob = search(app, vault, path="notes/*.txt", search_term="needle")
    glob_matches = set(explicit_glob.metadata.get("matches") or [])
    assert any(
        match.startswith("notes/bravo.txt:") for match in glob_matches
    ), "Explicit glob search should keep matching non-markdown text files"
    assert not any(
        match.startswith("notes/alpha.md:") for match in glob_matches
    ), "Explicit glob search should respect the caller's file pattern"

    for literal_query in ("a.*b", "--hidden"):
        directory_literal = search(app, vault, path="notes", search_term=literal_query)
        file_literal = search(
            app, vault, path="notes/literal.txt", search_term=literal_query
        )
        glob_literal = search(app, vault, path="notes/*.txt", search_term=literal_query)
        assert all(
            any(
                match.startswith("notes/literal.txt:")
                for match in result.metadata.get("matches") or []
            )
            for result in (directory_literal, file_literal, glob_literal)
        ), f"Directory, file, and glob search should treat {literal_query!r} literally"

    response = app.request(
        "/api/system/settings/general/file_list_max_results",
        method="PUT",
        data={"value": "2"},
    )
    assert response.status_code == 200, response.text

    bounded_file = search(app, vault, path="notes/many.txt", search_term="many hit")

    bounded_glob = search(app, vault, path="notes/many*.txt", search_term="many hit")
    for result in (bounded_file, bounded_glob):
        assert (
            result.metadata.get("match_count") == 2
        ), "Explicit file and glob search should enforce the global result cap"
        assert (
            result.metadata.get("truncated") is True
        ), "Explicit file and glob search should report truncation"

    miss = search(app, vault, path="notes", search_term="absent")
    assert (
        miss.return_value == "No matches found for 'absent' in text files"
    ), "No-match message should describe the all-text search scope"
