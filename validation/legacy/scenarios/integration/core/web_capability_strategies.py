"""Validate explicit web strategy dispatch and shared ingestion primitives."""

from __future__ import annotations

import sys
from inspect import signature
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.ingestion.models import RawDocument, SourceKind
from core.ingestion.strategies.html_raw import extract_html_markdownify
from core.settings.upgrades import upgrade_settings_mapping
from core.tools.web_extract import WebExtract
from core.web.errors import WebUrlPolicyError
from core.web.fetchers.curl import _fetch_once, _map_curl_error
from core.web.html import html_to_markdown
from core.web.models import (
    WebExtractionItem,
    WebExtractionResult,
    WebFetchResult,
    WebSearchItem,
    WebSearchResult,
)
from core.web.registry import WebStrategyRegistry, WebStrategySpec
from core.web.security import (
    resolve_public_url,
    sanitize_url_for_log,
    sanitize_urls_in_text_for_log,
)
from core.web.service import WebCapabilityService
from core.web.strategies.extract_curl import extract_with_curl
from validation.support.base_scenario import BaseScenario


class WebCapabilityStrategiesScenario(BaseScenario):
    """Prove strategies are explicit and ingestion reuses conversion policy."""

    async def test_scenario(self) -> None:
        calls: list[str] = []
        registry = WebStrategyRegistry()

        async def selected_search(*, query: str, max_results: int) -> WebSearchResult:
            calls.append("selected")
            return WebSearchResult(
                strategy="selected",
                query=query,
                items=[
                    WebSearchItem(
                        title="Result",
                        url="https://example.com",
                        snippet=str(max_results),
                    )
                ],
            )

        async def fallback_search(**_kwargs) -> WebSearchResult:
            calls.append("fallback")
            raise AssertionError("Unselected strategy must not run")

        registry.register(WebStrategySpec("web_search", "selected", selected_search))
        registry.register(WebStrategySpec("web_search", "fallback", fallback_search))
        service = WebCapabilityService(registry)
        result = await service.search(
            query="assistantmd", max_results=4, strategy="selected"
        )
        self.soft_assert_equal(
            result.strategy, "selected", "Configured search strategy should be used"
        )
        self.soft_assert_equal(
            calls, ["selected"], "Dispatch should not invoke a fallback strategy"
        )

        extract_parameters = signature(WebExtract.get_tool().function).parameters
        self.soft_assert(
            "include_images" in extract_parameters,
            "web_extract should expose the provider-neutral include_images contract",
        )

        extracted_urls: list[str] = []

        async def selected_extract(
            *, urls: list[str], include_images: bool
        ) -> WebExtractionResult:
            del include_images
            extracted_urls.extend(urls)
            return WebExtractionResult(
                strategy="selected",
                items=[
                    WebExtractionItem(
                        source_url=url,
                        effective_url=url,
                        content="content",
                    )
                    for url in urls
                ],
            )

        def resolve_test_url(url: str) -> tuple[str, tuple[str, ...]]:
            if "private" in url:
                raise WebUrlPolicyError("private target")
            return "example.com", ("93.184.216.34",)

        registry.register(WebStrategySpec("web_extract", "selected", selected_extract))
        with patch(
            "core.web.service.resolve_public_url",
            side_effect=resolve_test_url,
        ):
            mixed = await service.extract(
                urls=[
                    "https://example.com/public",
                    "http://127.0.0.1/private",
                ],
                strategy="selected",
            )
        self.soft_assert_equal(
            extracted_urls,
            ["https://example.com/public"],
            "Extraction should dispatch only URLs accepted by public-target policy",
        )
        self.soft_assert_equal(
            len(mixed.items),
            1,
            "A rejected URL should not discard another URL's successful extraction",
        )
        self.soft_assert_equal(
            [failure.error_type for failure in mixed.failures],
            ["WebUrlPolicyError"],
            "Rejected URLs should remain visible as typed per-item failures",
        )

        html = (
            "<html><head><title>Shared</title></head><body><h1>Heading</h1>"
            "<script>ignored()</script></body></html>"
        )
        shared_markdown = html_to_markdown(html, clean_html=True)
        ingestion_markdown = extract_html_markdownify(
            RawDocument(
                source_uri="https://example.com/article",
                kind=SourceKind.URL,
                mime="text/html",
                payload=html,
            ),
            {"clean_html": True},
        ).plain_text
        self.soft_assert_equal(
            ingestion_markdown,
            shared_markdown,
            "URL ingestion should use the shared HTML conversion contract",
        )
        self.soft_assert(
            "ignored" not in ingestion_markdown,
            "Shared clean_html policy should remove script content",
        )

        pdf_url = "https://example.com/report.pdf"
        with patch(
            "core.web.strategies.extract_curl.fetch_url_with_curl",
            return_value=WebFetchResult(
                source_url=pdf_url,
                effective_url=pdf_url,
                status_code=200,
                headers={"content-type": "application/pdf"},
                body=b"%PDF-1.7 binary content",
            ),
        ):
            binary_result = await extract_with_curl(
                urls=[pdf_url], include_images=False
            )
        self.soft_assert_equal(
            binary_result.items,
            [],
            "web_extract should not decode binary document responses as readable text",
        )
        self.soft_assert_equal(
            len(binary_result.failures),
            1,
            "A binary response should remain visible as a per-URL extraction failure",
        )
        self.soft_assert(
            "content_import" in binary_result.failures[0].error,
            "Binary extraction failures should direct callers to durable content import",
        )

        try:
            resolve_public_url("http://127.0.0.1/private")
        except WebUrlPolicyError:
            pass
        else:
            self.soft_assert(False, "Private URL targets should be rejected")
        self.soft_assert_equal(
            sanitize_url_for_log(
                "https://user:password@example.com/article?token=secret#fragment"
            ),
            "https://example.com/article",
            "URL logs should omit credentials, query strings, and fragments",
        )
        self.soft_assert_equal(
            sanitize_url_for_log("data:text/html,secret-content"),
            "data:[redacted]",
            "Non-network test URLs should not expose embedded payloads in diagnostics",
        )
        self.soft_assert_equal(
            sanitize_urls_in_text_for_log(
                "request failed for https://example.com/page?token=secret#fragment"
            ),
            "request failed for https://example.com/page",
            "URLs embedded in provider diagnostics should use shared sanitization",
        )
        self.soft_assert_equal(
            str(
                _map_curl_error(
                    63,
                    "curl write limit",
                    "https://example.com/report.pdf",
                    20,
                    5 * 1024 * 1024,
                )
            ),
            "Response exceeded 5 MB limit",
            "URL response-limit failures should use the configured setting unit",
        )

        captured_command: list[str] = []

        def fake_run(command: list[str], **_kwargs):
            from subprocess import CompletedProcess

            captured_command.extend(command)
            headers_path = Path(command[command.index("--dump-header") + 1])
            body_path = Path(command[command.index("--output") + 1])
            headers_path.write_text("HTTP/1.1 200 OK\r\n\r\n", encoding="utf-8")
            body_path.write_bytes(b"ok")
            return CompletedProcess(
                command,
                0,
                "__CURL_META__200|https://example.com/|93.184.216.34|0.01",
                "",
            )

        with patch("core.web.fetchers.curl.subprocess.run", side_effect=fake_run):
            _fetch_once(
                "https://example.com/",
                hostname="example.com",
                pinned_address="93.184.216.34",
                connect_timeout_seconds=1,
                read_timeout_seconds=1,
                max_bytes=1024,
                headers={},
            )
        self.soft_assert(
            "--noproxy" in captured_command
            and captured_command[captured_command.index("--noproxy") + 1] == "*",
            "DNS-pinned curl fetches should not inherit a proxy that can re-resolve targets",
        )

        upgraded = upgrade_settings_mapping(
            {
                "settings": {
                    "enabled_tools": {
                        "value": [
                            "web_search_tavily",
                            "tavily_extract",
                            "custom_tool",
                        ]
                    },
                    "ingestion_url_fetch_backend": {"value": "curl"},
                },
                "tools": {"custom_tool": {"user_editable": True}},
            },
            {
                "settings": {
                    "disabled_tools": {"value": []},
                    "web_search_strategy": {"value": "duckduckgo"},
                    "ingestion_url_fetch_strategy": {"value": "curl"},
                },
                "tools": {
                    "web_search": {},
                    "web_extract": {},
                    "web_crawl": {},
                    "file_read": {},
                },
            },
        )
        upgraded_settings = upgraded["settings"]
        self.soft_assert_equal(
            upgraded_settings["disabled_tools"]["value"],
            ["web_crawl", "file_read"],
            "Settings upgrade should preserve each capability excluded by the old allowlist",
        )
        self.soft_assert(
            "enabled_tools" not in upgraded_settings,
            "Settings upgrade should remove the retired enabled_tools setting",
        )
        self.soft_assert_equal(
            upgraded_settings["web_search_strategy"]["value"],
            "tavily",
            "A single legacy search provider should preserve its strategy",
        )
        self.soft_assert_equal(
            upgraded_settings["ingestion_url_fetch_strategy"]["value"],
            "curl",
            "Ingestion fetch strategy should retain its configured value",
        )
        web_disabled = upgrade_settings_mapping(
            {"settings": {"enabled_tools": {"value": ["file_read"]}}},
            {
                "settings": {"disabled_tools": {"value": []}},
                "tools": {"web_search": {}, "file_read": {}},
            },
        )
        self.soft_assert_equal(
            web_disabled["settings"]["disabled_tools"]["value"],
            ["web_search"],
            "Settings upgrade should preserve tools excluded by the old allowlist",
        )
        malformed_allowlist = upgrade_settings_mapping(
            {"settings": {"enabled_tools": {"value": "not-a-list"}}},
            {
                "settings": {"disabled_tools": {"value": []}},
                "tools": {"web_search": {}, "file_read": {}},
            },
        )
        self.soft_assert_equal(
            malformed_allowlist["settings"]["disabled_tools"]["value"],
            ["web_search", "file_read"],
            "Malformed legacy allowlists should remain fail-closed during repair",
        )

        self.teardown_scenario()
        self.assert_no_failures()
