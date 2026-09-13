"""
Live URL ingestion smoke scenario.

Validates /api/import/url using a real network fetch target.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from validation.core.base_scenario import BaseScenario


class ImportPipelineLiveUrlScenario(BaseScenario):
    """Validate URL ingest completion and rendered output creation."""

    async def test_scenario(self):
        vault = self.create_vault("ImportPipelineLiveUrlVault")

        await self.start_system()

        url_response = self.call_api(
            "/api/import/url",
            method="POST",
            data={
                "vault": vault.name,
                "url": "https://example.com",
                "clean_html": True,
            },
        )
        assert url_response.status_code == 200, "URL ingest should return 200"
        url_payload = url_response.json()
        assert (
            url_payload.get("status") == "completed"
        ), "URL ingest job should complete"
        url_outputs = url_payload.get("outputs") or []
        assert len(url_outputs) > 0, "URL ingest should return at least one output path"

        for rel_path in url_outputs:
            output_path = vault / rel_path
            assert output_path.exists(), f"Expected {rel_path} to be created"
            output_content = output_path.read_text()
            assert "example" in output_content
            assert "mime: text/html" in output_content

        await self.stop_system()
        self.teardown_scenario()
