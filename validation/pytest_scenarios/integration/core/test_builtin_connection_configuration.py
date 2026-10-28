"""Principal-owned Google metadata and Gmail policy survive real mutations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.google_connections import (
    reject_secret_deletion,
)

pytestmark = pytest.mark.scenario


async def test_google_connection_configuration_and_atomic_cleanup(
    app: Application,
) -> None:
    app.evidence.register_secret("atomic-fixture")
    await app.start()
    from core.connections import (
        GmailPreferences,
        GoogleConnectionCreate,
        GoogleConnectionUpdate,
    )
    from core.identity import ExecutionAuthority
    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    service, google = runtime.built_in_connections, runtime.google_connection
    assert google is not None
    owner, other = ExecutionAuthority("google-owner"), ExecutionAuthority(
        "google-other"
    )
    created = service.create_google_connection_for_authority(
        owner,
        GoogleConnectionCreate(
            display_name="Google", client_id="owner.apps.googleusercontent.com"
        ),
    )
    assert (
        created.gmail.search_default_results,
        created.gmail.search_max_results,
        created.gmail.message_max_characters,
        created.gmail.thread_max_messages,
        created.gmail.attachment_download_enabled,
        created.gmail.attachment_max_mb,
        created.gmail.draft_creation_enabled,
        created.gmail.draft_max_characters,
        created.config_version,
        created.oauth_generation,
        created.slug,
        created.is_default,
    ) == (20, 100, 50_000, 25, False, 25, False, 50_000, 1, 1, "google", True)
    assert service.get_google_connection_for_authority(other) is None
    preferences = GmailPreferences(
        search_default_results=10,
        search_max_results=40,
        message_max_characters=75_000,
        thread_max_messages=30,
        attachment_download_enabled=True,
        attachment_max_mb=40,
        draft_creation_enabled=True,
        draft_max_characters=80_000,
    )
    updated = google.update_connection(
        owner,
        created.connection_id,
        GoogleConnectionUpdate(
            client_id="replacement.apps.googleusercontent.com", gmail=preferences
        ),
    )
    assert (updated.gmail, updated.config_version, updated.oauth_generation) == (
        preferences,
        2,
        2,
    )
    preferences_only = google.update_connection(
        owner,
        created.connection_id,
        GoogleConnectionUpdate(
            client_id=updated.client_id,
            gmail=GmailPreferences(
                search_default_results=12,
                search_max_results=40,
                message_max_characters=75_000,
                thread_max_messages=30,
                attachment_download_enabled=True,
                attachment_max_mb=60,
                draft_creation_enabled=True,
                draft_max_characters=90_000,
            ),
        ),
    )
    assert (preferences_only.config_version, preferences_only.oauth_generation) == (
        3,
        2,
    )
    secondary = service.create_google_connection_for_authority(
        owner,
        GoogleConnectionCreate(
            display_name="Work Gmail", client_id="work.apps.googleusercontent.com"
        ),
    )
    assert (
        secondary.slug,
        secondary.is_default,
        len(service.list_google_connections_for_authority(owner)),
    ) == ("work-gmail", False, 2)
    with pytest.raises(ValueError):
        service.create_google_connection_for_authority(
            owner,
            GoogleConnectionCreate(
                display_name="work gmail",
                client_id="duplicate.apps.googleusercontent.com",
            ),
        )
    promoted = google.update_connection(
        owner,
        secondary.connection_id,
        GoogleConnectionUpdate(
            display_name=secondary.display_name,
            client_id=secondary.client_id,
            is_default=True,
            gmail=secondary.gmail,
        ),
    )
    assert promoted.is_default
    assert (
        service.get_google_connection_for_authority(owner).connection_id
        == secondary.connection_id
    )
    with pytest.raises(ValueError):
        google.delete_connection(owner, secondary.connection_id)
    assert google.delete_connection(
        owner, secondary.connection_id, replacement_default_id=created.connection_id
    )
    recreated = service.create_google_connection_for_authority(
        owner,
        GoogleConnectionCreate(
            display_name="Work Gmail", client_id="work.apps.googleusercontent.com"
        ),
    )
    assert recreated.slug != secondary.slug
    google.set_client_secret(owner, "atomic-fixture", created.connection_id)
    before = service.get_google_connection_for_authority(owner, created.connection_id)
    with reject_secret_deletion(app), pytest.raises(ValueError):
        google.update_connection(
            owner,
            created.connection_id,
            GoogleConnectionUpdate(client_id="replacement-client"),
        )
    assert (
        service.get_google_connection_for_authority(owner, created.connection_id)
        == before
    )
    assert (
        google.resolve_client_secret(owner, created.connection_id) == "atomic-fixture"
    )
    assert google.delete_connection(owner, recreated.connection_id)
    assert google.delete_connection(owner, created.connection_id)
    assert service.get_google_connection_for_authority(owner) is None

    concurrent_owner = ExecutionAuthority("google-concurrent-owner")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                service.create_google_connection_for_authority,
                concurrent_owner,
                GoogleConnectionCreate(display_name=name, client_id=name),
            )
            for name in ("Concurrent A", "Concurrent B")
        ]
        for future in futures:
            future.result(timeout=10)
    assert (
        sum(
            item.is_default
            for item in service.list_google_connections_for_authority(concurrent_owner)
        )
        == 1
    )


async def test_invalid_gmail_preferences_are_rejected(app: Application) -> None:
    await app.start()
    from core.connections import GmailPreferences

    for invalid in (
        {"search_default_results": 101, "search_max_results": 100},
        {"search_default_results": 1, "search_max_results": 501},
        {"message_max_characters": 250_001},
        {"thread_max_messages": 101},
        {"attachment_max_mb": 0},
        {"attachment_max_mb": 101},
        {"attachment_max_mb": True},
        {"attachment_download_enabled": 1},
        {"draft_creation_enabled": 1},
        {"draft_max_characters": 0},
        {"draft_max_characters": 250_001},
    ):
        with pytest.raises(ValueError):
            GmailPreferences(**invalid)
