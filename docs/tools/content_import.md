# `content_import`

`content_import` imports URLs or vault files through the durable ingestion pipeline and reads the resulting job status. Imported content is written as vault artifacts under the configured ingestion output path. Markdown files land directly in the selected destination. Optional companion files are grouped under `assets/<import-name>/` within that destination.

## Submit

Use `operation="submit"` with one source or a list of sources:

```text
content_import(
  operation="submit",
  sources=["https://example.org/report.pdf", "Research/local.pdf"]
)
```

Sources may be public HTTP/HTTPS URLs or vault-relative file paths. Vault files are preserved after import. The call creates one durable ingestion job per source, processes each job immediately by default, and returns after every job reaches a terminal state. The returned JSON includes an `items` array with each job's ID, source, source kind, status, output paths or error, and extraction provenance.

Keep the default for routine imports so the imported Markdown is available in the current turn. For a large multi-file submission, set `queue_only=true` to return promptly with queued job records and let the background worker process them:

```text
content_import(
  operation="submit",
  sources=["Research/one.pdf", "Research/two.pdf"],
  queue_only=true
)
```

Retain the returned job IDs and use `status` to inspect queue-only submissions.

Optional `options`:

- `destination`: vault-relative output directory for this import; when omitted, the configured `ingestion_output_path_pattern` is used
- `pdf_mode`: `markdown` or `page_images`
- `strategies`: ordered extraction strategy names
- `pdf_strategies`: ordered extraction strategies applied only after a URL or vault file is classified as a PDF; useful for extensionless document URLs
- `capture_ocr_images`: save images extracted by Mistral under the import's assets directory and rewrite matching Markdown image links; increases the provider response size and vault storage
- `include_ocr_blocks`: retain labeled blocks in reading order, including their page bounding boxes; useful for scripted layout analysis, but unnecessary for ordinary Markdown imports
- `ocr_table_format`: `markdown` or `html`; additionally retain tables as separate structured values in OCR metadata. Use `html` for complex or merged cells; omit this option to keep tables inline in the page Markdown
- `extract_ocr_header`: remove detected page headers from the main Markdown and retain them separately in OCR metadata
- `extract_ocr_footer`: remove detected page footers from the main Markdown and retain them separately in OCR metadata
- `ocr_confidence`: `page` for compact page-level review scores or `word` for detailed per-word scores and substantially larger metadata
- `clean_html`: boolean

OCR enrichment options are opt-in. When requested structured data is returned, it is stored under `assets/<import-name>/ocr.json` and referenced by the imported Markdown frontmatter.

OCR requires a configured `MISTRAL_API_KEY`, non-empty `ingestion_ocr_endpoint` and `ingestion_ocr_model` settings, and the `pdf_ocr` strategy. If OCR is unavailable, do not request OCR enrichments; use the default local PDF import or report the missing configuration to the user.

For routine research imports, omit all OCR enrichment options. Use `options={"strategies": ["pdf_ocr"]}` for scanned or layout-heavy PDFs, add `"capture_ocr_images": true` when figures matter, and add `"ocr_confidence": "page"` when low-quality pages need review. Blocks, separate tables, and word confidence are primarily intended for deterministic downstream scripts. PDF enrichment options affect `pdf_ocr`; local `pdf_text` extraction ignores them.

The maximum sources accepted per call is controlled by the editable `content_import_max_batch_size` setting, which defaults to 20.

## Status

Use `operation="status"` with one job id or a list of job ids:

```text
content_import(operation="status", job_ids=[41, 42])
```

The returned JSON includes an `items` array with the job id, source, source kind, current state, output paths, any durable ingestion error, strategies attempted, the selected strategy/provider/model, and any fallback reason. Selection fields remain empty while a queue-only job is waiting or when no extractor succeeds. The tool does not define research progress or retry policy.

For Dashboard monitoring, cancellation, and background-worker settings, see [Importing Content](../use/importing-content.md).

## Boundaries

- File paths are resolved inside the active vault. Absolute paths, traversal, symlink escapes, and virtual mounts are rejected.
- URL loading uses the configured ingestion transport, public-network policy, timeouts, and response-size limit.
- Initial remote conversion supports HTML and PDF responses.
- The tool does not search for sources, organize a library, summarize results, or track which sources a research effort still needs.
