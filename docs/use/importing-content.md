# Importing Content

Assistant.md imports vault files and public HTTP/HTTPS URLs into Markdown files. Imports can be submitted from Vault Explorer, chat, or Monty workflows. Every accepted import creates a durable ingestion job so status, outputs, errors, and cancellation remain visible across the application.

## Start an import

Open Vault Explorer and make the intended output folder active. Select one or more supported PDF or image files and choose **Import**, or choose **Import URL** for a public HTTP/HTTPS source. To add local files, choose **Upload** and then **Upload & import** to create the source files and submit their Markdown imports in one workflow. Imported source files remain in the vault.

The import panel uses the saved defaults unless you expand **Options for this import** and choose one-off overrides. The panel shows the destination explicitly, so normal Explorer imports do not depend on a typed vault-relative path or the global fallback output pattern.

Interactive submissions normally process each new job immediately. The `content_import` tool waits for terminal results by default, making imported Markdown available in the same agent turn. For a large multi-file submission, the caller can set `queue_only=true` and let the background worker process it. See the [`content_import` tool reference](../tools/content_import.md) for the complete invocation contract.

## Monitor and control imports

Open **Dashboard → Import** to see recent jobs for the selected vault and edit the defaults used by new imports. The Import Status table shows queued, processing, completed, failed, and cancelled jobs, along with their outputs or errors. Use **Open Vault Explorer** to start another file or URL import.

- Use **Refresh Import Status** to reload the durable job list.
- Use **Process Queue Now** to request an immediate run of the scheduled ingestion worker. The run still observes the configured batch size.
- Save **Import defaults** to change the PDF output mode, strategy order, and OCR behavior used when a request does not provide an override.
- Queued jobs can be cancelled. A processing job cannot be cancelled because its extraction thread or external OCR request may already be running.

The table refreshes automatically while queued or processing jobs are present.

## Tune queue timing

Two editable settings control imports left for background processing:

- `ingestion_worker_interval_seconds` controls how often the worker checks for queued jobs. A shorter interval reduces pickup latency.
- `ingestion_worker_batch_size` controls how many jobs a worker run processes concurrently. A batch larger than this value requires multiple worker runs.

Keep the immediate default for normal agent-driven research. Tune these settings when using `queue_only=true` or another surface that deliberately leaves jobs queued. A shorter interval reduces background pickup latency; a longer interval reduces scheduling activity. Increasing batch size can improve throughput, but it also increases simultaneous network, OCR, CPU, and external API usage.

Scheduled worker runs do not overlap. Slow imports can therefore extend the effective wait for jobs left in the queue.
