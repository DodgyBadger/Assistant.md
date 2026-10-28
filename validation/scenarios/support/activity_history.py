"""Explicit segment ages and sizes for the real retention pruning boundary."""

import os
from datetime import UTC, datetime, timedelta

from validation.scenarios.application import Application


def seed_prunable_segments(app: Application):
    prune_root = app.evidence.artifacts / "activity-prune"
    prune_root.mkdir(parents=True, exist_ok=True)
    prune_log = prune_root / "activity.log"
    prune_log.write_text("active\n", encoding="utf-8")
    expired = prune_root / "activity.log.2026-01-01"
    expired.write_text("expired\n", encoding="utf-8")
    recent_oldest = prune_root / "activity.log.2026-07-14"
    recent_oldest.write_text("a" * 64, encoding="utf-8")
    recent_newest = prune_root / "activity.log.2026-07-15"
    recent_newest.write_text("b" * 64, encoding="utf-8")
    now = datetime.now(UTC)
    os.utime(expired, ((now - timedelta(days=45)).timestamp(),) * 2)
    os.utime(recent_oldest, ((now - timedelta(days=2)).timestamp(),) * 2)
    os.utime(recent_newest, ((now - timedelta(days=1)).timestamp(),) * 2)
    return prune_log, expired, recent_oldest, recent_newest, now
