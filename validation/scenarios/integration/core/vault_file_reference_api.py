"""Integration scenario for chat-native vault file reference API contracts."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario


class VaultFileReferenceApiScenario(BaseScenario):
    """Validate vault file reference listing, inline read/write, and mutation audit."""

    async def test_scenario(self):
        vault = self.create_vault("VaultFileReferenceApiVault")
        self.create_file(vault, "Projects/Alpha/README.md", "# Alpha\n\nStart here.\n")
        self.create_file(vault, "Projects/Alpha/notes.txt", "plain notes\n")
        self.create_file(vault, "Projects/Alpha/Nested/plan.md", "# Plan\n")
        self.create_file(vault, "README.md", "# Vault root\n")
        self.create_file(vault, "root-note.md", "root note\n")
        self.create_file(vault, "script.py", "print('editable')\n")
        self.create_file(vault, "revision-restore.md", "first revision\n")
        self.create_file(vault, "Pagination/a.md", "a\n")
        self.create_file(vault, "Pagination/b.md", "b\n")
        self.create_file(vault, "Pagination/c.md", "c\n")
        self.create_file(vault, "Spanish/lesson.md", "hola\n")
        self.create_file(vault, "Spanish-vocabulary.md", "hola\n")
        self.create_file(vault, ".hidden/secret.md", "hidden\n")
        (vault / "binary.docx").write_bytes(b"PK\x03\x04\x00\x00not plain text")

        await self.start_system()

        from api.services import resolve_vault_root

        assert resolve_vault_root(vault.name) == vault.resolve()
        for invalid_vault_name in (".", "..", "nested/vault"):
            try:
                resolve_vault_root(invalid_vault_name)
            except Exception as exc:
                assert getattr(exc, "error_type", "") == "InvalidVaultName"
            else:
                raise AssertionError(
                    f"Invalid vault name should be rejected: {invalid_vault_name}"
                )

        escaping_vault = vault.parent / "EscapingVault"
        escaping_vault.symlink_to(self.run_path / "artifacts", target_is_directory=True)
        try:
            resolve_vault_root(escaping_vault.name)
        except Exception as exc:
            assert getattr(exc, "error_type", "") == "VaultRootEscapesDataRoot"
        else:
            raise AssertionError(
                "A symlinked vault root must not escape the configured data root"
            )

        refs = self.call_api(
            f"/api/vaults/{vault.name}/file-refs",
            params={"workspace_path": "Projects", "scope": "workspace", "limit": 20},
        )
        assert refs.status_code == 200, "File reference listing should succeed"
        refs_payload = refs.json()
        paths = {item["path"] for item in refs_payload.get("items", [])}
        assert (
            "Projects/Alpha" in paths
        ), "Workspace listing should include project folders"
        assert (
            ".hidden/secret.md" not in paths
        ), "Workspace listing should not expose hidden paths"

        first_page = self.call_api(
            f"/api/vaults/{vault.name}/file-refs",
            params={"path": "Pagination", "scope": "vault", "limit": 2},
        )
        assert first_page.status_code == 200
        assert [item["path"] for item in first_page.json()["items"]] == [
            "Pagination/a.md",
            "Pagination/b.md",
        ]
        assert first_page.json()["truncated"] is True
        assert first_page.json()["next_offset"] == 2
        second_page = self.call_api(
            f"/api/vaults/{vault.name}/file-refs",
            params={"path": "Pagination", "scope": "vault", "limit": 2, "offset": 2},
        )
        assert [item["path"] for item in second_page.json()["items"]] == [
            "Pagination/c.md"
        ]
        assert second_page.json()["truncated"] is False

        search = self.call_api(
            f"/api/vaults/{vault.name}/file-refs",
            params={
                "workspace_path": "Projects",
                "scope": "vault",
                "query": "readme",
                "limit": 20,
            },
        )
        assert (
            search.status_code == 200
        ), "Vault-wide file reference search should succeed"
        search_paths = {item["path"] for item in search.json().get("items", [])}
        assert (
            "Projects/Alpha/README.md" in search_paths
        ), "Search should find markdown files"
        assert (
            ".hidden/secret.md" not in search_paths
        ), "Search should not expose hidden files"

        basename_search = self.call_api(
            f"/api/vaults/{vault.name}/file-refs",
            params={"scope": "vault", "query": "spanish", "limit": 20},
        )
        assert basename_search.status_code == 200
        basename_paths = {item["path"] for item in basename_search.json()["items"]}
        assert "Spanish" in basename_paths
        assert "Spanish-vocabulary.md" in basename_paths
        assert (
            "Spanish/lesson.md" not in basename_paths
        ), "Name search should match item basenames, not ancestor folder names"

        stale_search = self.call_api(
            f"/api/vaults/{vault.name}/file-refs",
            params={"path": "Missing", "scope": "vault", "query": "readme"},
        )
        assert (
            stale_search.status_code == 404
        ), "A missing search folder should not silently widen to the vault root"

        resolved = self.call_api(
            f"/api/vaults/{vault.name}/file-refs/resolve",
            method="POST",
            data={
                "workspace_path": "Projects/Alpha",
                "paths": [
                    "@README.md",
                    "root-note.md",
                    "Projects/Alpha/README.md",
                    "Projects/Alpha",
                    "Nested/plan.md",
                    "missing.md",
                    ".hidden/secret.md",
                    "README.md",
                ],
            },
        )
        assert resolved.status_code == 200, "Candidate path resolution should succeed"
        resolutions = {
            item["requested_path"]: item for item in resolved.json().get("items", [])
        }
        assert resolutions["README.md"] == {
            "requested_path": "README.md",
            "path": "Projects/Alpha/README.md",
            "kind": "file",
            "source": "workspace",
        }, "Workspace-root basename matches should beat vault-root matches"
        assert resolutions["root-note.md"]["path"] == "root-note.md"
        assert resolutions["root-note.md"]["source"] == "vault"
        assert resolutions["Projects/Alpha/README.md"]["source"] == "vault"
        assert resolutions["Projects/Alpha"]["kind"] == "directory"
        assert (
            resolutions["Nested/plan.md"]["kind"] == "missing"
        ), "Slash-containing references must not fall back to workspace-relative paths"
        assert resolutions["missing.md"]["kind"] == "missing"
        assert resolutions[".hidden/secret.md"]["kind"] == "missing"
        assert (
            len(resolutions) == 7
        ), "Duplicate normalized candidates should resolve once"

        invalid_resolution = self.call_api(
            f"/api/vaults/{vault.name}/file-refs/resolve",
            method="POST",
            data={"paths": ["../outside.md"]},
        )
        assert invalid_resolution.status_code == 400
        assert invalid_resolution.json().get("error") == "InvalidVaultReferencePath"

        read = self.call_api(
            f"/api/vaults/{vault.name}/files",
            params={"path": "Projects/Alpha/README.md"},
        )
        assert read.status_code == 200, "Vault file read should succeed"
        read_payload = read.json()
        assert (
            read_payload["content"] == "# Alpha\n\nStart here.\n"
        ), "Read returns exact content"
        assert read_payload["sha256"], "Read returns a content hash"

        source_read = self.call_api(
            f"/api/vaults/{vault.name}/files",
            params={"path": "script.py"},
        )
        assert source_read.status_code == 200, "UTF-8 source files should be editable"
        assert source_read.json()["content"] == "print('editable')\n"

        binary_read = self.call_api(
            f"/api/vaults/{vault.name}/files",
            params={"path": "binary.docx"},
        )
        assert (
            binary_read.status_code == 415
        ), "Binary files should not open in the text editor"
        assert binary_read.json().get("error") == "VaultFileNotText"

        traversal = self.call_api(
            f"/api/vaults/{vault.name}/files",
            params={"path": "../outside.md"},
        )
        assert traversal.status_code == 400, "Traversal path should be rejected"
        assert (
            traversal.json().get("error") == "InvalidVaultFilePath"
        ), "Traversal rejection should use stable API error type"

        stale = self.call_api(
            f"/api/vaults/{vault.name}/files",
            method="PUT",
            params={"path": "Projects/Alpha/README.md"},
            data={"content": "# Changed\n", "expected_sha256": "stale"},
        )
        assert stale.status_code == 409, "Stale hash should reject save"
        assert (
            stale.json().get("error") == "VaultFileConflict"
        ), "Stale hash should use conflict error type"
        assert (vault / "Projects/Alpha/README.md").read_text(encoding="utf-8") == (
            "# Alpha\n\nStart here.\n"
        ), "Stale save must not modify the file"

        mutation_checkpoint = self.event_checkpoint()
        update = self.call_api(
            f"/api/vaults/{vault.name}/files",
            method="PUT",
            params={"path": "Projects/Alpha/README.md"},
            data={
                "content": "# Alpha\n\nUpdated.\n",
                "expected_sha256": read_payload["sha256"],
            },
        )
        assert update.status_code == 200, "Hash-matched save should succeed"
        update_hash = update.json()["sha256"]
        assert (
            update_hash != read_payload["sha256"]
        ), "Save should return the new content hash"
        assert (vault / "Projects/Alpha/README.md").read_text(encoding="utf-8") == (
            "# Alpha\n\nUpdated.\n"
        ), "Hash-matched save should update the file"
        update_events = self.events_since(mutation_checkpoint)
        self.assert_event_contains(
            update_events,
            name="vault_activity_completed",
            expected={
                "vault_name": vault.name,
                "kind": "explorer",
                "source": "api",
            },
        )
        activity = self.call_api(f"/api/vaults/{vault.name}/activity")
        assert activity.status_code == 200
        update_activity = next(
            group
            for group in activity.json()["groups"]
            if any(
                mutation["operation"] == "update_vault_file"
                and mutation["path"] == "Projects/Alpha/README.md"
                for mutation in group["mutations"]
            )
        )
        assert update_activity["activity_kind"] == "explorer"
        assert update_activity["status"] == "completed"
        assert update_activity["operation_count"] == 1
        revisions = self.call_api(
            f"/api/vaults/{vault.name}/files/revisions",
            params={"path": "Projects/Alpha/README.md"},
        )
        assert revisions.status_code == 200, "File revision history should respond"
        revision_rows = revisions.json().get("revisions") or []
        assert (
            len(revision_rows) == 1
        ), "Explorer save should retain one pre-edit revision"
        revision = revision_rows[0]
        assert revision["activity_kind"] == "explorer"
        assert revision["operation"] == "update_vault_file"
        assert revision["exists"] is True
        snapshot = self.call_api(
            f"/api/vault-state/snapshots/{revision['snapshot_id']}/content"
        )
        assert snapshot.status_code == 200
        assert (
            snapshot.text == "# Alpha\n\nStart here.\n"
        ), "Explorer revision should preserve the exact pre-edit content"

        restore_read = self.call_api(
            f"/api/vaults/{vault.name}/files",
            params={"path": "revision-restore.md"},
        )
        restore_update = self.call_api(
            f"/api/vaults/{vault.name}/files",
            method="PUT",
            params={"path": "revision-restore.md"},
            data={
                "content": "second revision\n",
                "expected_sha256": restore_read.json()["sha256"],
            },
        )
        assert restore_update.status_code == 200
        restore_history = self.call_api(
            f"/api/vaults/{vault.name}/files/revisions",
            params={"path": "revision-restore.md"},
        )
        restore_snapshot_id = restore_history.json()["revisions"][0]["snapshot_id"]
        restored = self.call_api(
            f"/api/vaults/{vault.name}/files/revisions/{restore_snapshot_id}/restore",
            method="POST",
            data={"expected_sha256": restore_update.json()["sha256"]},
        )
        assert restored.status_code == 200
        assert restored.json()["exists"] is True
        assert (vault / "revision-restore.md").read_text(encoding="utf-8") == (
            "first revision\n"
        )
        restored_history = self.call_api(
            f"/api/vaults/{vault.name}/files/revisions",
            params={"path": "revision-restore.md"},
        ).json()["revisions"]
        assert [row["operation"] for row in restored_history[:2]] == [
            "restore_revision",
            "update_vault_file",
        ]
        displaced_snapshot = self.call_api(
            f"/api/vault-state/snapshots/{restored_history[0]['snapshot_id']}/content"
        )
        assert (
            displaced_snapshot.text == "second revision\n"
        ), "Restore should retain the displaced current state as another revision"
        stale_restore = self.call_api(
            f"/api/vaults/{vault.name}/files/revisions/{restore_snapshot_id}/restore",
            method="POST",
            data={"expected_sha256": restore_update.json()["sha256"]},
        )
        assert stale_restore.status_code == 409
        assert stale_restore.json().get("error") == "VaultFileConflict"

        missing = self.call_api(
            f"/api/vaults/{vault.name}/files",
            params={"path": "Projects/README.md"},
        )
        assert (
            missing.status_code == 404
        ), "Missing referenced file should report not found"
        assert (
            missing.json().get("error") == "VaultFileNotFound"
        ), "Missing referenced file should use stable not-found error"

        create = self.call_api(
            f"/api/vaults/{vault.name}/files",
            method="PUT",
            params={"path": "Projects/README.md"},
            data={
                "content": "# Projects\n\nWorkspace landing page.\n",
                "create_if_missing": True,
            },
        )
        assert create.status_code == 200, "Create-if-missing save should succeed"
        assert (
            create.json()["content"] == "# Projects\n\nWorkspace landing page.\n"
        ), "Create response should return the created content"
        assert (vault / "Projects/README.md").read_text(encoding="utf-8") == (
            "# Projects\n\nWorkspace landing page.\n"
        ), "Create-if-missing should write the new vault file"
        create_revisions = self.call_api(
            f"/api/vaults/{vault.name}/files/revisions",
            params={"path": "Projects/README.md"},
        )
        assert create_revisions.status_code == 200
        create_revision_rows = create_revisions.json()["revisions"]
        assert len(create_revision_rows) == 1
        assert create_revision_rows[0]["exists"] is False
        assert create_revision_rows[0]["snapshot_available"] is False
        restore_absent = self.call_api(
            f"/api/vaults/{vault.name}/files/revisions/{create_revision_rows[0]['snapshot_id']}/restore",
            method="POST",
            data={"expected_sha256": create.json()["sha256"]},
        )
        assert restore_absent.status_code == 200
        assert restore_absent.json()["exists"] is False
        assert not (
            vault / "Projects/README.md"
        ).exists(), "Restoring a pre-create revision should restore file absence"

        vault_events = self._vault_file_events()
        event_hashes = {row["path"]: row["content_hash"] for row in vault_events}
        assert (
            event_hashes.get("Projects/Alpha/README.md") == update_hash
        ), "Inline file edit should refresh vault-state with the edited content hash"
        event_pairs = {(row["event_type"], row["path"]) for row in vault_events}
        assert (
            "created",
            "Projects/README.md",
        ) in event_pairs, "Create-if-missing should refresh vault-state creation events"

        create_directory = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={"operation": "create_directory", "path": "Explorer"},
        )
        assert create_directory.status_code == 200
        assert (vault / "Explorer").is_dir()

        create_file = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={
                "operation": "create_file",
                "path": "Explorer/draft.md",
                "content": "# Draft\n",
            },
        )
        assert create_file.status_code == 200
        assert (vault / "Explorer/draft.md").read_text(encoding="utf-8") == "# Draft\n"

        move_file = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={
                "operation": "move",
                "path": "Explorer/draft.md",
                "destination": "Explorer/renamed.md",
            },
        )
        assert move_file.status_code == 200
        assert not (vault / "Explorer/draft.md").exists()
        assert (vault / "Explorer/renamed.md").is_file()
        source_revisions = self.call_api(
            f"/api/vaults/{vault.name}/files/revisions",
            params={"path": "Explorer/draft.md"},
        )
        assert source_revisions.status_code == 200
        source_revision_rows = source_revisions.json()["revisions"]
        assert [row["exists"] for row in source_revision_rows] == [True, False]
        moved_source_snapshot = self.call_api(
            f"/api/vault-state/snapshots/{source_revision_rows[0]['snapshot_id']}/content"
        )
        assert moved_source_snapshot.status_code == 200
        assert moved_source_snapshot.text == "# Draft\n"

        (vault / "Explorer/Nested").mkdir()
        (vault / "Explorer/Nested/child.txt").write_text("nested\n", encoding="utf-8")
        (vault / "OccupiedDestination").mkdir()
        collision = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={
                "operation": "move",
                "path": "Explorer",
                "destination": "OccupiedDestination",
            },
        )
        assert collision.status_code == 409
        assert collision.json().get("error") == "destination_exists"
        assert (vault / "Explorer/Nested/child.txt").is_file()

        move_directory_checkpoint = self.event_checkpoint()
        move_directory = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={
                "operation": "move",
                "path": "Explorer",
                "destination": "MovedExplorer",
            },
        )
        assert move_directory.status_code == 200
        move_directory_payload = move_directory.json()
        assert move_directory_payload["kind"] == "directory"
        assert move_directory_payload["destination"] == "MovedExplorer"
        assert move_directory_payload["metadata"]["descendant_file_count"] == 2
        assert move_directory_payload["metadata"]["descendant_directory_count"] == 1
        assert not (vault / "Explorer").exists()
        assert (vault / "MovedExplorer/renamed.md").is_file()
        assert (vault / "MovedExplorer/Nested/child.txt").read_text(
            encoding="utf-8"
        ) == "nested\n"
        self.assert_event_contains(
            self.events_since(move_directory_checkpoint),
            name="vault_directory_move_completed",
            expected={
                "vault_name": vault.name,
                "source_path": "Explorer",
                "destination_path": "MovedExplorer",
                "descendant_file_count": 2,
                "descendant_directory_count": 1,
            },
        )
        activity = self.call_api(f"/api/vaults/{vault.name}/activity")
        assert all(
            group.get("operation_count", 0) > 0 for group in activity.json()["groups"]
        ), "Rejected Explorer commands should not create empty activity rows"
        directory_activity = next(
            group
            for group in activity.json()["groups"]
            if any(
                mutation["operation"] == "move"
                and mutation["path"] == "Explorer"
                and mutation["target_kind"] == "directory"
                for mutation in group["mutations"]
            )
        )
        assert directory_activity["activity_kind"] == "explorer"
        assert directory_activity["status"] == "completed"
        assert directory_activity["operation_count"] == 1

        descendant_move = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={
                "operation": "move",
                "path": "MovedExplorer",
                "destination": "MovedExplorer/Nested/Again",
            },
        )
        assert descendant_move.status_code == 400
        assert descendant_move.json().get("error") == "destination_inside_source"

        non_empty_delete = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={"operation": "delete", "path": "MovedExplorer"},
        )
        assert non_empty_delete.status_code == 409
        assert non_empty_delete.json().get("error") == "VaultDirectoryNotEmpty"

        delete_file = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={"operation": "delete", "path": "MovedExplorer/renamed.md"},
        )
        assert delete_file.status_code == 200
        assert not (vault / "MovedExplorer/renamed.md").exists()
        deleted_revisions = self.call_api(
            f"/api/vaults/{vault.name}/files/revisions",
            params={"path": "MovedExplorer/renamed.md"},
        )
        assert deleted_revisions.status_code == 200
        deleted_revision_rows = deleted_revisions.json()["revisions"]
        assert (
            len(deleted_revision_rows) == 1
        ), "Revision history should stay exact-path and not follow the directory move"
        deleted_snapshot = self.call_api(
            f"/api/vault-state/snapshots/{deleted_revision_rows[0]['snapshot_id']}/content"
        )
        assert deleted_snapshot.status_code == 200
        assert deleted_snapshot.text == "# Draft\n"

        delete_nested_file = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={"operation": "delete", "path": "MovedExplorer/Nested/child.txt"},
        )
        assert delete_nested_file.status_code == 200

        delete_nested_directory = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={"operation": "delete", "path": "MovedExplorer/Nested"},
        )
        assert delete_nested_directory.status_code == 200

        delete_directory = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={"operation": "delete", "path": "MovedExplorer"},
        )
        assert delete_directory.status_code == 200
        assert not (vault / "MovedExplorer").exists()

    def _vault_file_events(self) -> list[sqlite3.Row]:
        db_path = self.run_path / "system" / "vault_state.db"
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            return list(
                connection.execute(
                    """
                    SELECT event_type, path, content_hash
                    FROM vault_file_events
                    ORDER BY sequence
                    """
                )
            )
        finally:
            connection.close()
