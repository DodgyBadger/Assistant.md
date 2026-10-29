"""A real concurrent settings commit maps a stale API edit to HTTP 409."""

from __future__ import annotations

import json
import sqlite3
from threading import Thread

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.settings_commit_barrier import (
    pause_next_settings_save,
)

pytestmark = pytest.mark.scenario


async def test_concurrent_settings_commit_rejects_stale_public_request(
    app: Application,
) -> None:
    await app.start()
    key = "chat_tool_calls_limit"
    endpoint = f"/api/system/settings/general/{key}"
    assert app.request(endpoint, "PUT", {"value": "1"}).status_code == 200
    current = app.configuration()["settings"][key]
    database = app.root / "system/settings.db"
    app.evidence.step("real_repository_save_profile_barrier")
    with pause_next_settings_save() as barrier:
        writer_errors = []

        def write_new_revision():
            try:
                if not barrier.entered.wait(5):
                    raise TimeoutError("Request did not reach real repository save")
                entry = {**current, "value": 2, "source": "custom"}
                with sqlite3.connect(database, timeout=5) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "INSERT INTO configuration_settings(name, payload) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET payload = excluded.payload",
                        (key, json.dumps(entry)),
                    )
                    connection.execute(
                        "UPDATE configuration_state SET revision = revision + 1 WHERE singleton = 1"
                    )
            except BaseException as exc:
                writer_errors.append(exc)
            finally:
                barrier.release.set()

        writer = Thread(target=write_new_revision, daemon=True)
        writer.start()
        try:
            rejected = await app.request_async(endpoint, "PUT", {"value": "3"})
        finally:
            barrier.release.set()
            writer.join(timeout=10)
        assert not writer.is_alive()
        assert not writer_errors, writer_errors
        assert barrier.entered.is_set()
    assert not barrier.timed_out.is_set()
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"] == "SettingsConflict"
    assert (
        rejected.json()["details"]["status"],
        rejected.json()["details"]["error_type"],
    ) == ("failed", "SettingsConflict")
    settings = app.request("/api/system/settings/general")
    assert settings.status_code == 200
    assert (
        int(next(item for item in settings.json() if item["key"] == key)["value"]) == 2
    )
    retry = app.request(endpoint, "PUT", {"value": "3"})
    assert retry.status_code == 200
    assert (
        int(
            next(
                item
                for item in app.request("/api/system/settings/general").json()
                if item["key"] == key
            )["value"]
        )
        == 3
    )
