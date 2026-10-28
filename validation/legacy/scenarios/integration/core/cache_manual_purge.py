"""
Integration scenario validating manual cache purge removes expired artifacts.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.authoring.cache import get_cache_artifact, upsert_cache_artifact
from core.runtime.state import get_runtime_context
from validation.support.base_scenario import BaseScenario


class CacheManualPurgeScenario(BaseScenario):
    """Validate the manual expired-cache purge endpoint."""

    async def test_scenario(self):
        vault = self.create_vault("CacheManualPurgeVault")

        await self.start_system()
        try:
            runtime = get_runtime_context()
            system_root = runtime.config.system_root
            now = datetime.now()
            expired_now = now - timedelta(days=2)
            active_now = now

            upsert_cache_artifact(
                owner_id=f"{vault.name}/chat/test-session",
                session_key="test-session",
                artifact_ref="scratch/expired",
                cache_mode="duration",
                ttl_seconds=30,
                raw_content="expired artifact",
                metadata={"type": "cache"},
                origin="validation",
                now=expired_now,
                week_start_day=0,
                system_root=system_root,
            )
            upsert_cache_artifact(
                owner_id=f"{vault.name}/chat/test-session",
                session_key="test-session",
                artifact_ref="scratch/active",
                cache_mode="duration",
                ttl_seconds=24 * 60 * 60,
                raw_content="active artifact",
                metadata={"type": "cache"},
                origin="validation",
                now=active_now,
                week_start_day=0,
                system_root=system_root,
            )

            before_active = get_cache_artifact(
                owner_id=f"{vault.name}/chat/test-session",
                session_key="test-session",
                artifact_ref="scratch/active",
                now=active_now,
                week_start_day=0,
                system_root=system_root,
            )
            self.soft_assert(
                before_active is not None, "Expected active cache artifact before purge"
            )

            response = self.call_api("/api/system/cache/purge-expired", method="POST")
            assert (
                response.status_code == 200
            ), "Manual cache purge endpoint should succeed"
            payload = response.json()
            self.soft_assert(
                payload.get("success") is True,
                "Manual cache purge should report success",
            )
            self.soft_assert_equal(
                payload.get("purged_count"),
                1,
                "Manual cache purge should remove exactly one expired artifact",
            )

            expired_after = get_cache_artifact(
                owner_id=f"{vault.name}/chat/test-session",
                session_key="test-session",
                artifact_ref="scratch/expired",
                now=active_now,
                week_start_day=0,
                system_root=system_root,
            )
            active_after = get_cache_artifact(
                owner_id=f"{vault.name}/chat/test-session",
                session_key="test-session",
                artifact_ref="scratch/active",
                now=active_now,
                week_start_day=0,
                system_root=system_root,
            )

            self.soft_assert(
                expired_after is None,
                "Expired cache artifact should be removed by manual purge",
            )
            self.soft_assert(
                active_after is not None,
                "Unexpired cache artifact should remain after manual purge",
            )
        finally:
            await self.stop_system()
            self.teardown_scenario()
