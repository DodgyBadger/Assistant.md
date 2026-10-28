"""Validate multipart chat upload limits at the API boundary."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.support.base_scenario import BaseScenario


class ChatMultipartUploadLimitsScenario(BaseScenario):
    """Validate oversized multipart image uploads fail before task creation."""

    async def test_scenario(self):
        vault = self.create_vault("ChatMultipartUploadLimitsVault")
        await self.start_system()

        update_limit = self.call_api(
            "/api/system/settings/general/chunking_max_image_mb_per_image",
            method="PUT",
            data={"value": "1"},
        )
        self.soft_assert_equal(
            update_limit.status_code,
            200,
            "Per-image upload limit setting should update",
        )

        before_tasks = self.call_api(
            "/api/tasks",
            params={"kind": "chat"},
        )
        before_count = len(before_tasks.json().get("tasks", []))

        response = self._get_api_client()._client.post(  # noqa: SLF001
            "/api/chat/tasks",
            data={
                "vault_name": vault.name,
                "prompt": "Describe this oversized image.",
                "tools": "[]",
                "model": "test",
            },
            files={
                "images": (
                    "too-large.png",
                    b"x" * ((1024 * 1024) + 1),
                    "image/png",
                ),
            },
        )
        self.soft_assert_equal(
            response.status_code,
            413,
            "Oversized multipart image upload should be rejected at the API boundary",
        )
        self.soft_assert(
            "ChatImageUploadTooLarge" in response.text,
            "Oversized multipart image response should identify the upload limit",
        )

        after_tasks = self.call_api(
            "/api/tasks",
            params={"kind": "chat"},
        )
        after_count = len(after_tasks.json().get("tasks", []))
        self.soft_assert_equal(
            after_count,
            before_count,
            "Rejected multipart upload should not create an execution task",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()
