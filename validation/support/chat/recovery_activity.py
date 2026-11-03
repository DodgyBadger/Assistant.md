"""Observe chat recovery decisions on their actual retained Activity channel."""

from __future__ import annotations


def require_recovery_activity(app, name, **fields):
    response = app.request("/api/system/activity-log", params={"limit": 1000})
    assert response.status_code == 200, response.text
    matches = [
        item["data"]
        for item in response.json()["entries"]
        if item.get("data", {}).get("event") == name
        and all(item["data"].get(key) == value for key, value in fields.items())
    ]
    assert matches, f"Expected retained Activity event {name!r} with {fields!r}"
    return matches[0]
