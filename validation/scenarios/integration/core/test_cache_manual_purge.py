"""Manual purge removes only expired cache artifacts."""

from datetime import datetime, timedelta

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_manual_cache_purge(app: Application) -> None:
    vault = app.vault("CacheManualPurgeVault")
    await app.start()
    from core.authoring.cache import get_cache_artifact, upsert_cache_artifact
    from core.runtime.state import get_runtime_context

    now = datetime.now()
    root = get_runtime_context().config.system_root
    owner = f"{vault.name}/chat/test-session"
    for ref, when, ttl, content in (
        ("scratch/expired", now - timedelta(days=2), 30, "expired artifact"),
        ("scratch/active", now, 86400, "active artifact"),
    ):
        upsert_cache_artifact(
            owner_id=owner,
            session_key="test-session",
            artifact_ref=ref,
            cache_mode="duration",
            ttl_seconds=ttl,
            raw_content=content,
            metadata={"type": "cache"},
            origin="validation",
            now=when,
            week_start_day=0,
            system_root=root,
        )

    def get(ref):
        return get_cache_artifact(
            owner_id=owner,
            session_key="test-session",
            artifact_ref=ref,
            now=now,
            week_start_day=0,
            system_root=root,
        )

    assert get("scratch/active") is not None
    response = app.request("/api/system/cache/purge-expired", method="POST")
    assert response.status_code == 200
    assert response.json()["success"] is True and response.json()["purged_count"] == 1
    assert get("scratch/expired") is None
    assert get("scratch/active") is not None
