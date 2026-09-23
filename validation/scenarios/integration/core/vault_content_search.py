"""Validate bounded structured content search through the Vault Explorer API."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.vault_state.file_operations import search_vault_files_operation
from validation.core.base_scenario import BaseScenario


class VaultContentSearchScenario(BaseScenario):
    """Prove literal matching, folder scope, and global result limits."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("VaultContentSearchVault")
        self.create_file(
            vault,
            "Notes/name:with:colons.md",
            "First line\nLiteral [query] appears here\nLiteral [query] again\n",
        )
        self.create_file(vault, "Outside.md", "Literal [query] outside\n")
        self.create_file(vault, "Notes/.hidden/secret.md", "Literal [query] hidden\n")
        (vault / "Notes/binary.bin").write_bytes(b"\x00Literal [query] binary\x00")
        await self.start_system()

        response = self.call_api(
            f"/api/vaults/{vault.name}/content-search",
            params={"query": "[QUERY]", "path": "Notes", "limit": 1},
        )
        self.soft_assert_equal(
            response.status_code, 200, "Content search should succeed"
        )
        payload = response.json()
        self.soft_assert_equal(payload.get("path"), "Notes", "Response preserves scope")
        self.soft_assert_equal(
            payload.get("query"), "[QUERY]", "Response preserves the literal query"
        )
        self.soft_assert_equal(
            payload.get("truncated"), True, "A global result limit reports truncation"
        )
        matches = payload.get("matches", [])
        self.soft_assert_equal(len(matches), 1, "The requested match limit is enforced")
        self.soft_assert_equal(
            matches[0].get("path"),
            "Notes/name:with:colons.md",
            "Structured paths support filenames containing colons",
        )
        self.soft_assert_equal(matches[0].get("line"), 2, "Line numbers are structured")
        self.soft_assert(
            "Outside.md" not in {match.get("path") for match in matches},
            "Folder scope excludes matches elsewhere in the vault",
        )
        complete_response = self.call_api(
            f"/api/vaults/{vault.name}/content-search",
            params={"query": "[QUERY]", "path": "Notes", "limit": 100},
        )
        complete_paths = {
            match.get("path") for match in complete_response.json().get("matches", [])
        }
        self.soft_assert(
            all(".hidden" not in path for path in complete_paths if path),
            "Hidden files are excluded",
        )
        self.soft_assert(
            "Notes/binary.bin" not in complete_paths,
            "Binary files are excluded",
        )
        tool_result = search_vault_files_operation(
            vault_path=vault,
            path="Notes",
            search_term="[QUERY]",
        )
        self.soft_assert(
            any(
                match.startswith("Notes/name:with:colons.md:2:")
                for match in tool_result.metadata.get("matches", [])
            ),
            "The file tool adapter uses the same literal structured search service",
        )

        missing_query = self.call_api(
            f"/api/vaults/{vault.name}/content-search",
            params={"query": "   "},
        )
        self.soft_assert_equal(
            missing_query.status_code, 400, "Blank content queries are rejected"
        )
        self.soft_assert_equal(
            missing_query.json().get("details", {}).get("code"),
            "missing_query",
            "Blank query rejection has a stable code",
        )
