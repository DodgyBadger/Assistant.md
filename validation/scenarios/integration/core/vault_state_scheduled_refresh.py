"""Integration scenario for scheduled vault-state refresh registration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.scheduling.system_jobs import (
    INGESTION_WORKER_JOB_ID,
    VAULT_STATE_REFRESH_JOB_ID,
    run_scheduled_ingestion_worker,
    run_scheduled_vault_state_refresh,
)
from validation.core.base_scenario import BaseScenario


class VaultStateScheduledRefreshScenario(BaseScenario):
    """Validate scheduled whole-vault observation system job behavior."""

    async def test_scenario(self):
        vault = self.create_vault("VaultStateScheduledRefreshVault")
        self.create_file(vault, "notes/external.md", "External v1\n")
        self._write_vault_scan_interval(60)

        await self.start_system()

        from core.vault_state import VaultStateService

        controller = self._get_system_controller()
        scheduler = controller._runtime.scheduler
        job = scheduler.get_job(VAULT_STATE_REFRESH_JOB_ID)
        ingestion_job = scheduler.get_job(INGESTION_WORKER_JOB_ID)
        self.soft_assert(
            job is not None,
            "Vault-state refresh system job should be registered",
        )
        if job is not None:
            self.soft_assert_equal(
                job.name,
                "Vault state refresh",
                "System job should have a clear display name",
            )
            self.soft_assert_equal(
                job.max_instances,
                1,
                "Vault-state refresh job should not overlap",
            )
        self.soft_assert(
            ingestion_job is not None,
            "Ingestion worker system job should be registered",
        )
        if ingestion_job is not None:
            self.soft_assert_equal(
                ingestion_job.func,
                run_scheduled_ingestion_worker,
                "Ingestion worker job should use a serializable module-level function",
            )

        await controller.trigger_vault_rescan()
        self.soft_assert(
            scheduler.get_job(VAULT_STATE_REFRESH_JOB_ID) is not None,
            "Workflow reload should preserve the vault-state system job",
        )
        self.soft_assert(
            scheduler.get_job(INGESTION_WORKER_JOB_ID) is not None,
            "Workflow reload should preserve the ingestion system job",
        )
        ingestion_job = scheduler.get_job(INGESTION_WORKER_JOB_ID)
        if ingestion_job is not None:
            self.soft_assert_equal(
                ingestion_job.func,
                run_scheduled_ingestion_worker,
                "Workflow reload should keep ingestion worker job serializable",
            )

        service = VaultStateService()
        initial_events = service.changes_since(0)
        checkpoint = max((event.sequence for event in initial_events), default=0)

        vault_state_activity_count = self._vault_state_activity_count()
        run_scheduled_vault_state_refresh(controller.test_data_root)
        self.soft_assert_equal(
            self._vault_state_activity_count(),
            vault_state_activity_count,
            "No-op scheduled refresh should keep all vault-state events out of System Activity",
        )

        scheduled_activity_count = self._scheduled_refresh_activity_count()
        (vault / "notes" / "external.md").write_text("External v2\n", encoding="utf-8")
        run_scheduled_vault_state_refresh(controller.test_data_root)
        self.soft_assert_equal(
            self._scheduled_refresh_activity_count(),
            scheduled_activity_count + 1,
            "Scheduled refresh with detected file changes should log to System Activity",
        )

        changes = service.changes_since(checkpoint)
        self.soft_assert(
            any(
                event.path == "notes/external.md" and event.event_type == "changed"
                for event in changes
            ),
            "Scheduled refresh callable should observe external file edits",
        )

        update_setting = self.call_api(
            "/api/system/settings/general/vault_scan_interval_seconds",
            method="PUT",
            data={"value": "0"},
        )
        self.soft_assert_equal(
            update_setting.status_code,
            200,
            "Disabling vault scan interval should be accepted",
        )
        self.soft_assert(
            scheduler.get_job(VAULT_STATE_REFRESH_JOB_ID) is None,
            "Settings reload should remove the persisted vault-state refresh job",
        )
        self.soft_assert(
            scheduler.get_job(INGESTION_WORKER_JOB_ID) is not None,
            "Disabling vault-state refresh should not remove ingestion worker",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    def _write_vault_scan_interval(self, interval_seconds: int) -> None:
        raw = self.configuration_snapshot()
        raw["settings"]["vault_scan_interval_seconds"]["value"] = interval_seconds
        self.install_configuration_fixture(raw)

    def _activity_log_text(self) -> str:
        activity_log = self._get_system_controller()._system_root / "activity.log"
        if not activity_log.exists():
            return ""
        return activity_log.read_text(encoding="utf-8")

    def _vault_state_activity_count(self) -> int:
        return self._activity_log_text().count('"tag": "vault-state"')

    def _scheduled_refresh_activity_count(self) -> int:
        return self._activity_log_text().count(
            '"event": "vault_state_scheduled_refresh_completed"'
        )
