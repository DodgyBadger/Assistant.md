"""Integration scenario for file_read search across normal text files."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.vault_state.file_operations import search_vault_files_operation
from validation.core.base_scenario import BaseScenario


class FileReadSearchTextFilesScenario(BaseScenario):
    """Validate search is not limited to markdown after all-file listing support."""

    async def test_scenario(self):
        vault = self.create_vault("FileReadSearchTextFilesVault")
        self.create_file(vault, "notes/alpha.md", "needle in markdown\n")
        self.create_file(vault, "notes/bravo.txt", "needle in text\n")
        self.create_file(vault, "notes/charlie.json", '{"value": "needle in json"}\n')
        self.create_file(vault, "notes/.hidden.txt", "needle hidden\n")
        self.create_file(vault, "notes/nomatch.txt", "nothing here\n")
        self.create_file(
            vault,
            "notes/literal.txt",
            "literal a.*b and --hidden option-like text\n",
        )
        self.create_file(vault, "notes/many.txt", "\n".join(["many hit"] * 5))

        await self.start_system()

        broad = search_vault_files_operation(
            vault_path=str(vault),
            path="notes",
            search_term="needle",
        )
        broad_matches = set(broad.metadata.get("matches") or [])
        self.soft_assert_equal(
            broad.metadata.get("status"),
            "completed",
            "Search should complete across a directory of text files",
        )
        self.soft_assert(
            any(match.startswith("notes/alpha.md:") for match in broad_matches),
            "Search should include markdown matches",
        )
        self.soft_assert(
            any(match.startswith("notes/bravo.txt:") for match in broad_matches),
            "Search should include non-markdown text matches",
        )
        self.soft_assert(
            any(match.startswith("notes/charlie.json:") for match in broad_matches),
            "Search should include JSON text matches",
        )
        self.soft_assert(
            not any(".hidden.txt" in match for match in broad_matches),
            "Search should continue to exclude hidden files by default",
        )

        explicit_file = search_vault_files_operation(
            vault_path=str(vault),
            path="notes/bravo.txt",
            search_term="needle",
        )
        self.soft_assert(
            any(
                match.startswith("notes/bravo.txt:")
                for match in explicit_file.metadata.get("matches") or []
            ),
            "Search should work when scoped to an explicit non-markdown file",
        )

        explicit_glob = search_vault_files_operation(
            vault_path=str(vault),
            path="notes/*.txt",
            search_term="needle",
        )
        glob_matches = set(explicit_glob.metadata.get("matches") or [])
        self.soft_assert(
            any(match.startswith("notes/bravo.txt:") for match in glob_matches),
            "Explicit glob search should keep matching non-markdown text files",
        )
        self.soft_assert(
            not any(match.startswith("notes/alpha.md:") for match in glob_matches),
            "Explicit glob search should respect the caller's file pattern",
        )

        for literal_query in ("a.*b", "--hidden"):
            directory_literal = search_vault_files_operation(
                vault_path=str(vault),
                path="notes",
                search_term=literal_query,
            )
            file_literal = search_vault_files_operation(
                vault_path=str(vault),
                path="notes/literal.txt",
                search_term=literal_query,
            )
            glob_literal = search_vault_files_operation(
                vault_path=str(vault),
                path="notes/*.txt",
                search_term=literal_query,
            )
            self.soft_assert(
                all(
                    any(
                        match.startswith("notes/literal.txt:")
                        for match in result.metadata.get("matches") or []
                    )
                    for result in (directory_literal, file_literal, glob_literal)
                ),
                f"Directory, file, and glob search should treat {literal_query!r} literally",
            )

        with patch(
            "core.vault_state.file_operations._default_list_max_results",
            return_value=2,
        ):
            bounded_file = search_vault_files_operation(
                vault_path=str(vault),
                path="notes/many.txt",
                search_term="many hit",
            )
            bounded_glob = search_vault_files_operation(
                vault_path=str(vault),
                path="notes/many*.txt",
                search_term="many hit",
            )
        for result in (bounded_file, bounded_glob):
            self.soft_assert_equal(
                result.metadata.get("match_count"),
                2,
                "Explicit file and glob search should enforce the global result cap",
            )
            self.soft_assert_equal(
                result.metadata.get("truncated"),
                True,
                "Explicit file and glob search should report truncation",
            )

        miss = search_vault_files_operation(
            vault_path=str(vault),
            path="notes",
            search_term="absent",
        )
        self.soft_assert_equal(
            miss.return_value,
            "No matches found for 'absent' in text files",
            "No-match message should describe the all-text search scope",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()
