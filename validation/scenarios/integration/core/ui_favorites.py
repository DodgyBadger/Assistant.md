"""Durable chat and vault bookmark contracts across API and file mutations."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario, with_local_user_authority


class UiFavoritesScenario(BaseScenario):
    """Bookmarks survive restarts and follow shared vault mutations."""

    @with_local_user_authority
    async def test_scenario(self):
        vault = self.create_vault("FavoritesVault")
        other = self.create_vault("FavoritesOtherVault")
        for name in ("a.md", "b.md", "nested/c.md"):
            self.create_file(vault, name, "bookmark needle\n")
        self.create_file(other, "b.md", "bookmark needle\n")
        await self.start_system()

        from core.runtime.state import get_runtime_context

        runtime = get_runtime_context()
        runtime.chat_session_access.ensure_session("favorite-chat", vault.name)
        runtime.chat_session_access.ensure_session("ordinary-chat", vault.name)
        before = runtime.chat_store.get_session_by_id("favorite-chat")
        assert before is not None
        revision = runtime.chat_store.get_session_history_revision(
            "favorite-chat", vault.name
        )
        url = "/api/chat/sessions/favorite-chat/favorite"
        for _ in range(2):
            response = self.call_api(
                url, method="PATCH", data={"vault_name": vault.name, "favorite": True}
            )
            assert response.status_code == 200, response.text
        rows = self.call_api(
            "/api/chat/sessions", params={"vault_name": vault.name}
        ).json()
        assert rows[0]["session_id"] == "favorite-chat"
        assert rows[0]["favorite"] is True
        after = runtime.chat_store.get_session_by_id("favorite-chat")
        assert after is not None and after.last_activity_at == before.last_activity_at
        assert (
            runtime.chat_store.get_session_history_revision("favorite-chat", vault.name)
            == revision
        )
        assert (
            self.call_api(
                url, method="PATCH", data={"vault_name": other.name, "favorite": False}
            ).status_code
            == 409
        )
        assert (
            self.call_api(
                "/api/chat/sessions/missing/favorite",
                method="PATCH",
                data={"vault_name": vault.name, "favorite": True},
            ).status_code
            == 404
        )

        runtime.chat_store.ensure_session(
            "hidden-chat", vault.name, owner_principal_id="another-user"
        )
        hidden = self.call_api(
            "/api/chat/sessions/hidden-chat/favorite",
            method="PATCH",
            data={"vault_name": vault.name, "favorite": True},
        )
        assert hidden.status_code == 404
        assert "hidden-chat" not in {row["session_id"] for row in rows}

        favorites_url = f"/api/vaults/{vault.name}/favorites"
        response = self.call_api(
            favorites_url,
            method="PATCH",
            data={"paths": ["b.md", "nested", "nested/c.md"], "favorite": True},
        )
        assert response.status_code == 200, response.text
        assert set(response.json()["paths"]) == {"b.md", "nested", "nested/c.md"}
        bad = self.call_api(
            favorites_url,
            method="PATCH",
            data={"paths": ["a.md", "../escape"], "favorite": True},
        )
        assert bad.status_code == 400
        assert set(self.call_api(favorites_url).json()["paths"]) == {
            "b.md",
            "nested",
            "nested/c.md",
        }
        assert (
            self.call_api(f"/api/vaults/{other.name}/favorites").json()["paths"] == []
        )
        from api.services.vault_files import get_vault_favorites, update_vault_favorites
        from core.identity import ExecutionAuthority, use_execution_authority

        with use_execution_authority(ExecutionAuthority("another-user")):
            assert get_vault_favorites(vault.name) == []
            update_vault_favorites(vault.name, ["a.md"], True)
            assert get_vault_favorites(vault.name) == ["a.md"]
        assert set(self.call_api(favorites_url).json()["paths"]) == {
            "b.md",
            "nested",
            "nested/c.md",
        }

        refs_url = f"/api/vaults/{vault.name}/file-refs"
        page = self.call_api(
            refs_url, params={"scope": "vault", "favorites_only": True, "limit": 1}
        ).json()
        assert len(page["items"]) == 1 and page["truncated"]
        all_refs = self.call_api(
            refs_url, params={"scope": "vault", "favorites_only": True}
        ).json()
        assert {item["path"] for item in all_refs["items"]} == {
            "b.md",
            "nested",
            "nested/c.md",
        }
        assert all(item["favorite"] for item in all_refs["items"])
        searched = self.call_api(
            refs_url, params={"scope": "vault", "favorites_only": True, "query": ".md"}
        ).json()
        assert {item["path"] for item in searched["items"]} == {"b.md", "nested/c.md"}
        content = self.call_api(
            f"/api/vaults/{vault.name}/content-search",
            params={"query": "needle", "favorites_only": True, "limit": 1},
        )
        assert content.status_code == 200, content.text
        assert content.json()["matches"][0]["path"] in {"b.md", "nested/c.md"}
        assert (
            self.call_api(
                f"/api/vaults/{other.name}/content-search",
                params={"query": "needle", "favorites_only": True},
            ).json()["matches"]
            == []
        )
        assert (
            self.call_api(
                f"/api/vaults/{vault.name}/files", params={"path": "b.md"}
            ).json()["favorite"]
            is True
        )

        from core.vault_state.file_mutations import (
            delete_vault_file,
            move_vault_directory,
            move_vault_file,
        )

        move_vault_file(vault_path=vault, path="b.md", destination="renamed.md")
        move_vault_directory(vault_path=vault, path="nested", destination="moved")
        assert set(self.call_api(favorites_url).json()["paths"]) == {
            "renamed.md",
            "moved",
            "moved/c.md",
        }
        delete_vault_file(vault_path=vault, path="renamed.md")
        assert set(self.call_api(favorites_url).json()["paths"]) == {
            "moved",
            "moved/c.md",
        }
        # Removing a folder bookmark does not remove explicitly bookmarked children.
        response = self.call_api(
            favorites_url, method="PATCH", data={"paths": ["moved"], "favorite": False}
        )
        assert response.json()["paths"] == ["moved/c.md"]
        # External missing paths are hidden; no content is recreated by bookmarking.
        (vault / "moved/c.md").unlink()
        assert self.call_api(favorites_url).json()["paths"] == []
        assert (
            self.call_api(
                refs_url, params={"scope": "vault", "favorites_only": True}
            ).json()["items"]
            == []
        )
        self.create_file(vault, "moved/c.md", "bookmark needle\n")
        await self.stop_system()
        await self.start_system()
        assert self.call_api(favorites_url).json()["paths"] == ["moved/c.md"]
        rows = self.call_api(
            "/api/chat/sessions", params={"vault_name": vault.name}
        ).json()
        assert rows[0]["favorite"] is True
        response = self.call_api(
            url, method="PATCH", data={"vault_name": vault.name, "favorite": False}
        )
        assert response.status_code == 200
        assert not any(
            row["favorite"]
            for row in self.call_api(
                "/api/chat/sessions", params={"vault_name": vault.name}
            ).json()
        )
