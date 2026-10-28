"""Web strategy configuration, public URL policy and shared HTML conversion."""

from __future__ import annotations

from inspect import signature

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_builtin_web_strategy_selection_and_url_policy(app: Application) -> None:
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.tools.web_extract import WebExtract
    from core.web.errors import WebStrategyConfigurationError, WebUrlPolicyError
    from core.web.security import (
        resolve_public_url,
        sanitize_url_for_log,
        sanitize_urls_in_text_for_log,
    )
    from core.web.service import WebCapabilityService

    service = WebCapabilityService()
    assert "include_images" in signature(WebExtract.get_tool().function).parameters
    assert service.strategy_requirements("web_search", "duckduckgo") == (
        "duckduckgo",
        (),
    )
    assert service.strategy_requirements("web_extract", "curl") == ("curl", ())
    with pytest.raises(WebStrategyConfigurationError):
        service.strategy_requirements("web_search", "unknown-validation-strategy")
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        with pytest.raises(WebStrategyConfigurationError):
            await service.search(query="assistantmd", max_results=4, strategy="tavily")
        rejected = await service.extract(
            urls=["http://127.0.0.1/private"], strategy="curl"
        )
    assert rejected.strategy == "curl" and rejected.items == []
    assert [failure.error_type for failure in rejected.failures] == [
        "WebUrlPolicyError"
    ]
    with pytest.raises(WebUrlPolicyError):
        resolve_public_url("http://127.0.0.1/private")
    assert (
        sanitize_url_for_log(
            "https://user:password@example.com/article?token=secret#fragment"
        )
        == "https://example.com/article"
    )
    assert sanitize_url_for_log("data:text/html,secret-content") == "data:[redacted]"
    assert (
        sanitize_urls_in_text_for_log(
            "request failed for https://example.com/page?token=secret#fragment"
        )
        == "request failed for https://example.com/page"
    )


async def test_shared_html_conversion_and_legacy_strategy_migration(
    app: Application,
) -> None:
    await app.start()
    from core.ingestion.models import RawDocument, SourceKind
    from core.ingestion.strategies.html_raw import extract_html_markdownify
    from core.settings.upgrades import upgrade_settings_mapping
    from core.web.fetchers.curl import _map_curl_error
    from core.web.html import html_to_markdown

    html = "<html><head><title>Shared</title></head><body><h1>Heading</h1><script>ignored()</script></body></html>"
    shared = html_to_markdown(html, clean_html=True)
    ingestion = extract_html_markdownify(
        RawDocument(
            source_uri="https://example.com/article",
            kind=SourceKind.URL,
            mime="text/html",
            payload=html,
        ),
        {"clean_html": True},
    ).plain_text
    assert ingestion == shared and "ignored" not in ingestion
    assert (
        str(
            _map_curl_error(
                63,
                "curl write limit",
                "https://example.com/report.pdf",
                20,
                5 * 1024 * 1024,
            )
        )
        == "Response exceeded 5 MB limit"
    )
    upgraded = upgrade_settings_mapping(
        {
            "settings": {
                "enabled_tools": {
                    "value": ["web_search_tavily", "tavily_extract", "custom_tool"]
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
    settings = upgraded["settings"]
    assert settings["disabled_tools"]["value"] == ["web_crawl", "file_read"]
    assert "enabled_tools" not in settings
    assert settings["web_search_strategy"]["value"] == "tavily"
    assert settings["ingestion_url_fetch_strategy"]["value"] == "curl"
    template = {
        "settings": {"disabled_tools": {"value": []}},
        "tools": {"web_search": {}, "file_read": {}},
    }
    disabled = upgrade_settings_mapping(
        {"settings": {"enabled_tools": {"value": ["file_read"]}}}, template
    )
    assert disabled["settings"]["disabled_tools"]["value"] == ["web_search"]
    malformed = upgrade_settings_mapping(
        {"settings": {"enabled_tools": {"value": "not-a-list"}}}, template
    )
    assert malformed["settings"]["disabled_tools"]["value"] == [
        "web_search",
        "file_read",
    ]


async def test_registered_search_dispatch_runs_only_selected_http_workload(
    app: Application,
) -> None:
    await app.start()
    from core.web.registry import WebStrategyRegistry, WebStrategySpec
    from core.web.service import WebCapabilityService
    from validation.pytest_scenarios.support.web_strategy_loopback import (
        endpoint,
        search_handler,
    )

    with endpoint() as selected, endpoint() as fallback:
        registry = WebStrategyRegistry()
        registry.register(
            WebStrategySpec(
                "web_search", "selected", search_handler(selected, "selected")
            )
        )
        registry.register(
            WebStrategySpec(
                "web_search", "fallback", search_handler(fallback, "fallback")
            )
        )
        result = await WebCapabilityService(registry).search(
            query="assistantmd", max_results=4, strategy="selected"
        )
        assert result.strategy == "selected" and result.query == "assistantmd"
        assert [(item.title, item.url, item.snippet) for item in result.items] == [
            ("Loopback result", "https://example.com/result", "observed response")
        ]
        assert (
            selected.paths == ["/search?q=assistantmd&limit=4"] and fallback.paths == []
        )


async def test_pinned_raw_curl_fetch_bypasses_configured_proxy(
    app: Application,
) -> None:
    await app.start()
    from core.web.fetchers.curl import _fetch_once
    from validation.pytest_scenarios.support.web_strategy_loopback import (
        endpoint,
        proxy_environment,
    )

    # This low-level transport probe is not public URL-policy acceptance.
    with endpoint() as target, endpoint() as proxy, proxy_environment(proxy):
        url = f"http://pinned.example:{target.server_port}/resource"
        result = _fetch_once(
            url,
            hostname="pinned.example",
            pinned_address="127.0.0.1",
            connect_timeout_seconds=2,
            read_timeout_seconds=5,
            max_bytes=4096,
            headers={},
        )
        assert result.status_code == 200 and result.remote_ip == "127.0.0.1"
        assert b"Loopback result" in result.body
        assert target.paths == ["/resource"] and proxy.paths == []
