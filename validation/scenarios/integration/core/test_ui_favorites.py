"""Durable chat and vault bookmark contracts across API and file mutations."""

import pytest

from validation.support.application import Application

pytestmark = pytest.mark.scenario


async def test_bookmarks_persist_and_follow_vault_mutations(app: Application) -> None:
    vault = app.vault("FavoritesVault")
    other = app.vault("FavoritesOtherVault")
    for name in ("a.md", "b.md", "nested/c.md"):
        app.write(vault, name, "bookmark needle\n")
    app.write(other, "b.md", "bookmark needle\n")
    await app.start()

    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    from core.identity import LOCAL_USER_PRINCIPAL_ID

    for session_id in ("favorite-chat", "ordinary-chat"):
        runtime.chat_store.ensure_session(
            session_id, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
    before = runtime.chat_store.get_session_by_id("favorite-chat")
    assert before is not None
    revision = runtime.chat_store.get_session_history_revision(
        "favorite-chat", vault.name
    )
    url = "/api/chat/sessions/favorite-chat/favorite"
    for _ in range(2):
        response = app.request(
            url, method="PATCH", data={"vault_name": vault.name, "favorite": True}
        )
        assert response.status_code == 200, response.text
    rows = app.request("/api/chat/sessions", params={"vault_name": vault.name}).json()
    assert rows[0]["session_id"] == "favorite-chat"
    assert rows[0]["favorite"] is True
    after = runtime.chat_store.get_session_by_id("favorite-chat")
    assert after is not None and after.last_activity_at == before.last_activity_at
    assert (
        runtime.chat_store.get_session_history_revision("favorite-chat", vault.name)
        == revision
    )
    assert (
        app.request(
            url, method="PATCH", data={"vault_name": other.name, "favorite": False}
        ).status_code
        == 409
    )
    assert (
        app.request(
            "/api/chat/sessions/missing/favorite",
            method="PATCH",
            data={"vault_name": vault.name, "favorite": True},
        ).status_code
        == 404
    )

    runtime.chat_store.ensure_session(
        "hidden-chat", vault.name, owner_principal_id="another-user"
    )
    hidden = app.request(
        "/api/chat/sessions/hidden-chat/favorite",
        method="PATCH",
        data={"vault_name": vault.name, "favorite": True},
    )
    assert hidden.status_code == 404
    assert "hidden-chat" not in {row["session_id"] for row in rows}

    favorites_url = f"/api/vaults/{vault.name}/favorites"
    response = app.request(
        favorites_url,
        method="PATCH",
        data={"paths": ["b.md", "nested", "nested/c.md"], "favorite": True},
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["paths"]) == {"b.md", "nested", "nested/c.md"}
    bad = app.request(
        favorites_url,
        method="PATCH",
        data={"paths": ["a.md", "../escape"], "favorite": True},
    )
    assert bad.status_code == 400
    assert set(app.request(favorites_url).json()["paths"]) == {
        "b.md",
        "nested",
        "nested/c.md",
    }
    assert app.request(f"/api/vaults/{other.name}/favorites").json()["paths"] == []
    from api.services.vault_files import get_vault_favorites, update_vault_favorites
    from core.identity import ExecutionAuthority, use_execution_authority

    with use_execution_authority(ExecutionAuthority("another-user")):
        assert get_vault_favorites(vault.name) == []
        update_vault_favorites(vault.name, ["a.md"], True)
        assert get_vault_favorites(vault.name) == ["a.md"]
    assert set(app.request(favorites_url).json()["paths"]) == {
        "b.md",
        "nested",
        "nested/c.md",
    }

    refs_url = f"/api/vaults/{vault.name}/file-refs"
    page = app.request(
        refs_url, params={"scope": "vault", "favorites_only": True, "limit": 1}
    ).json()
    assert len(page["items"]) == 1 and page["truncated"]
    all_refs = app.request(
        refs_url, params={"scope": "vault", "favorites_only": True}
    ).json()
    assert {item["path"] for item in all_refs["items"]} == {
        "b.md",
        "nested",
        "nested/c.md",
    }
    assert all(item["favorite"] for item in all_refs["items"])
    searched = app.request(
        refs_url, params={"scope": "vault", "favorites_only": True, "query": ".md"}
    ).json()
    assert {item["path"] for item in searched["items"]} == {"b.md", "nested/c.md"}
    content = app.request(
        f"/api/vaults/{vault.name}/content-search",
        params={"query": "needle", "favorites_only": True, "limit": 1},
    )
    assert content.status_code == 200, content.text
    assert content.json()["matches"][0]["path"] in {"b.md", "nested/c.md"}
    assert (
        app.request(
            f"/api/vaults/{other.name}/content-search",
            params={"query": "needle", "favorites_only": True},
        ).json()["matches"]
        == []
    )
    assert (
        app.request(f"/api/vaults/{vault.name}/files", params={"path": "b.md"}).json()[
            "favorite"
        ]
        is True
    )

    from core.identity import LOCAL_USER_AUTHORITY
    from core.vault_state.file_mutations import (
        delete_vault_file,
        move_vault_directory,
        move_vault_file,
    )

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        move_vault_file(vault_path=vault, path="b.md", destination="renamed.md")
        move_vault_directory(vault_path=vault, path="nested", destination="moved")
    assert set(app.request(favorites_url).json()["paths"]) == {
        "renamed.md",
        "moved",
        "moved/c.md",
    }
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        delete_vault_file(vault_path=vault, path="renamed.md")
    assert set(app.request(favorites_url).json()["paths"]) == {
        "moved",
        "moved/c.md",
    }
    # Removing a folder bookmark does not remove explicitly bookmarked children.
    response = app.request(
        favorites_url, method="PATCH", data={"paths": ["moved"], "favorite": False}
    )
    assert response.json()["paths"] == ["moved/c.md"]
    # External missing paths are hidden; no content is recreated by bookmarking.
    (vault / "moved/c.md").unlink()
    assert app.request(favorites_url).json()["paths"] == []
    assert (
        app.request(refs_url, params={"scope": "vault", "favorites_only": True}).json()[
            "items"
        ]
        == []
    )
    app.write(vault, "moved/c.md", "bookmark needle\n")
    await app.controller.stop_system()
    await app.start()
    assert app.request(favorites_url).json()["paths"] == ["moved/c.md"]
    rows = app.request("/api/chat/sessions", params={"vault_name": vault.name}).json()
    assert rows[0]["favorite"] is True
    response = app.request(
        url, method="PATCH", data={"vault_name": vault.name, "favorite": False}
    )
    assert response.status_code == 200
    assert not any(
        row["favorite"]
        for row in app.request(
            "/api/chat/sessions", params={"vault_name": vault.name}
        ).json()
    )
