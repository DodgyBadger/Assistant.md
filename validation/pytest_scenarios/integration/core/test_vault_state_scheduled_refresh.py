"""Scheduled vault refresh detects external edits and respects settings."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.vault_mutation_inputs import (
    preload_scan_interval,
)

pytestmark = pytest.mark.scenario


async def test_scheduled_refresh_registration_and_external_edits(
    app: Application,
) -> None:
    vault = app.vault("VaultStateScheduledRefreshVault")
    app.write(vault, "notes/external.md", "External v1\n")
    preload_scan_interval(app, 60)
    await app.start()
    from core.runtime.state import get_runtime_context
    from core.scheduling.system_jobs import (
        INGESTION_WORKER_JOB_ID,
        VAULT_STATE_REFRESH_JOB_ID,
        run_scheduled_ingestion_worker,
        run_scheduled_vault_state_refresh,
    )
    from core.vault_state import VaultStateService

    scheduler = get_runtime_context().scheduler
    job = scheduler.get_job(VAULT_STATE_REFRESH_JOB_ID)
    assert (
        job is not None and job.name == "Vault state refresh" and job.max_instances == 1
    )
    ingestion = scheduler.get_job(INGESTION_WORKER_JOB_ID)
    assert ingestion is not None and ingestion.func == run_scheduled_ingestion_worker
    await app.controller.trigger_vault_rescan()
    assert scheduler.get_job(VAULT_STATE_REFRESH_JOB_ID) is not None
    ingestion = scheduler.get_job(INGESTION_WORKER_JOB_ID)
    assert ingestion is not None and ingestion.func == run_scheduled_ingestion_worker
    service = VaultStateService()
    initial = service.changes_since(0)
    checkpoint = max((event.sequence for event in initial), default=0)
    log = app.root / "system/activity.log"

    def activity() -> str:
        return log.read_text(encoding="utf-8") if log.exists() else ""

    before = activity().count('"tag": "vault-state"')
    run_scheduled_vault_state_refresh(app.controller.test_data_root)
    assert activity().count('"tag": "vault-state"') == before
    before = activity().count('"event": "vault_state_scheduled_refresh_completed"')
    (vault / "notes/external.md").write_text("External v2\n", encoding="utf-8")
    run_scheduled_vault_state_refresh(app.controller.test_data_root)
    assert (
        activity().count('"event": "vault_state_scheduled_refresh_completed"')
        == before + 1
    )
    assert any(
        event.path == "notes/external.md" and event.event_type == "changed"
        for event in service.changes_since(checkpoint)
    )
    disabled = app.request(
        "/api/system/settings/general/vault_scan_interval_seconds",
        "PUT",
        {"value": "0"},
    )
    assert disabled.status_code == 200
    assert scheduler.get_job(VAULT_STATE_REFRESH_JOB_ID) is None
    assert scheduler.get_job(INGESTION_WORKER_JOB_ID) is not None
