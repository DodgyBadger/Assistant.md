"""Validate retained System Activity pagination, filtering, and pruning."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.activity_log import prune_activity_segments
from core.logger import UnifiedLogger
from validation.support.base_scenario import BaseScenario


class SystemActivityHistoryScenario(BaseScenario):
    """Prove retained diagnostics remain queryable beyond the active byte tail."""

    async def test_scenario(self):
        self.create_vault("ActivityHistoryVault")
        await self.start_system()

        system_root = self._get_system_controller()._system_root
        log_path = system_root / "activity.log"
        probe_logger = UnifiedLogger("activity-history-probe")
        try:
            for sequence in range(3):
                probe_logger.info(
                    f"Retained activity probe {sequence}",
                    data={
                        "event": "retained_activity_probe",
                        "sequence": sequence,
                        "search_marker": "history-query-marker",
                    },
                )

            first = self.call_api(
                "/api/system/activity-log?limit=2"
                "&tag=activity-history-probe&search=history-query-marker"
            )
            assert first.status_code == 200, "Structured activity query should succeed"
            first_payload = first.json()
            assert (
                len(first_payload["entries"]) == 2
            ), "First activity page should honor its limit"
            assert first_payload[
                "next_cursor"
            ], "First activity page should expose an older cursor"
            assert (
                first_payload["total_matching"] == 3
            ), "Server-side filters should match retained entries"
            assert (
                "activity-history-probe" in first_payload["available_tags"]
            ), "Retained tag options should come from the server"
            assert first_payload[
                "earliest_retained_timestamp"
            ], "Activity query should expose the earliest retained timestamp"

            invalid_cursor = self.call_api(
                "/api/system/activity-log?cursor=not-a-cursor"
            )
            assert (
                invalid_cursor.status_code == 400
            ), "Malformed activity cursors should be rejected"

            cursor = quote(first_payload["next_cursor"], safe="")
            second = self.call_api(
                "/api/system/activity-log?limit=2"
                f"&cursor={cursor}&tag=activity-history-probe&search=history-query-marker"
            )
            assert second.status_code == 200, "Older activity page should load"
            second_payload = second.json()
            assert (
                len(second_payload["entries"]) == 1
            ), "Older page should return the remaining match"
            first_ids = {entry["id"] for entry in first_payload["entries"]}
            second_ids = {entry["id"] for entry in second_payload["entries"]}
            assert first_ids.isdisjoint(second_ids), "Cursor pages should not overlap"

            export = self.call_api("/api/system/activity-log/export")
            assert (
                export.status_code == 200
            ), "Raw retained activity export should succeed"
            assert (
                "history-query-marker" in export.text
            ), "Raw export should include retained entries"

            before_size = log_path.stat().st_size
            UnifiedLogger(
                "validation-only-probe",
                default_sinks=["validation"],
            ).info("This record must not enter System Activity")
            assert (
                log_path.stat().st_size == before_size
            ), "Validation-only logging should not append to System Activity"

            scheduler_records = []
            for line in log_path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if (record.get("data") or {}).get(
                    "event"
                ) == "workflow_scheduler_sync_completed":
                    scheduler_records.append(record)
            assert scheduler_records, "Startup should emit a scheduler sync summary"
            scheduler_data = scheduler_records[-1]["data"]
            assert (
                "loaded_workflows" not in scheduler_data
            ), "System Activity scheduler sync should omit workflow detail arrays"
            assert (
                "scheduled_workflows" not in scheduler_data
            ), "System Activity scheduler sync should remain compact"

            prune_root = self.artifacts_dir / "activity-prune"
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

            prune_result = prune_activity_segments(
                prune_log,
                retention_days=30,
                max_total_bytes=80,
                now=now,
            )
            assert (
                prune_result.removed_expired == 1
            ), "Expired segments should be removed"
            assert (
                prune_result.removed_for_size == 1
            ), "Size pressure should remove the oldest segment"
            assert not expired.exists(), "Expired segment should be deleted"
            assert (
                not recent_oldest.exists()
            ), "Oldest retained segment should be deleted first"
            assert (
                recent_newest.exists()
            ), "Newest retained segment should survive size pruning"
        finally:
            await self.stop_system()
            self.teardown_scenario()
