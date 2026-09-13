# 0027 - Expose Stable Web Capabilities With Explicit Strategies

## Status

Accepted.

Complements
[0007 - Use Settings Backed Model And Tool Binding](0007-settings-backed-model-and-tool-binding.md)
and
[0011 - Split Ingestion Source Loading From Extraction Strategy](0011-ingestion-source-strategy-split.md).

## Context

AssistantMD exposed provider identity through tool names for web search,
extraction, and crawling. Changing providers therefore changed model prompts,
authoring scripts, cache references, and validation contracts. The default
tool set also left static page extraction unavailable without optional Tavily
tools, encouraging repeated Chromium calls on memory-constrained deployments.

URL ingestion separately implemented bounded curl retrieval and HTML conversion,
creating drift risk with any built-in agent extraction path.

## Decision

Expose stable model-facing `web_search`, `web_extract`, and `web_crawl`
capabilities. Select exactly one implementation for each through string settings
and capability-specific strategy registries under `core/web/`.

The configured strategy is authoritative. Infrastructure does not silently
invoke another provider or launch Chromium after a failure. Strategy metadata
declares secret requirements so shared tool binding and configuration health can
report an unavailable selected strategy without substituting another one.

Own normalized web result models, public-network URL policy, bounded transport,
provider adapters, and reusable HTML-to-Markdown conversion in `core/web/`.
Model-facing tools remain thin formatting and structured-failure adapters.

URL ingestion reuses the shared curl transport and HTML conversion but retains
its own `RawDocument`/`ExtractedDocument` adapters, durable jobs, rendering,
storage, and vault-mutation policy. `web_extract` is transient and never creates
ingestion jobs or vault artifacts.

Keep `browser` as an explicit dynamic-page capability. Serialize Chromium
sessions by default, bound browser calls per execution task, and require cgroup
memory headroom before launch.

## Consequences

- Provider changes do not rename model-facing tools or direct Monty functions.
- Search, extract, and crawl can gain independent strategies without adding
  provider-named tools.
- Provider outages and missing credentials fail visibly with capability and
  strategy identity; behavior never silently degrades.
- Built-in curl extraction and URL ingestion share security and conversion
  behavior without sharing persistence orchestration.
- URLs to local/private networks, including redirects, are rejected by the
  shared transport policy.
- Browser-capable deployments have a documented 2 GB baseline; lightweight
  deployments disable browser and retain static `web_extract`.
- The durable `content_import` capability accepts URLs and vault files through
  ingestion without calling or extending the transient `web_extract` tool.

## Evidence

- `core/web/`
- `core/tools/web_search.py`
- `core/tools/web_extract.py`
- `core/tools/web_crawl.py`
- `core/tools/content_import.py`
- `core/ingestion/sources/web.py`
- `core/ingestion/strategies/html_raw.py`
- `validation/scenarios/integration/core/web_capability_strategies.py`
- `validation/scenarios/integration/core/web_strategy_binding.py`
- `validation/scenarios/integration/core/browser_resource_policy.py`
- Implementation plan: `web-capability-strategy-refactor-plan.md`
