# `web_extract`

## Purpose

Extract readable content from known URLs using the extraction strategy selected in System settings. Extraction is transient and does not import content into the vault.

This tool accepts readable web pages, not PDFs or other downloadable binary files. Use `content_import` when a source should become durable Markdown in the vault, including PDF and document URLs. Binary responses are rejected with a per-URL failure rather than decoded into tool context.

## Arguments

- `urls`: one URL or a list of up to ten URLs
- `include_images`: request image metadata from strategies that support it; defaults to `false`. A selected strategy that cannot honor the option fails clearly rather than ignoring it.

## Example

```python
web_extract(
    urls=[
        "https://example.com/docs/a",
        "https://example.com/docs/b",
    ]
)
```

Successful content is grouped by URL and wrapped as untrusted web data. Partial provider failures remain visible beside successful results. If every URL fails, the tool returns a structured failure identifying the configured strategy.

Use `browser` explicitly for dynamic pages that require Chromium. The tool does not launch a browser or change extraction strategies automatically.
