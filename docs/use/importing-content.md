# Importing Content

Assistant.md imports vault files and public HTTP/HTTPS URLs into Markdown files. Imports can be submitted from chat, Monty workflows, or the Dashboard Import section. Every accepted import creates a durable ingestion job so status, outputs, errors, and cancellation remain visible across the application.

Interactive submissions normally process each new job immediately. The `content_import` tool waits for terminal results by default, making imported Markdown available in the same agent turn. For a large multi-file submission, the caller can set `queue_only=true` and let the background worker process it. See the [`content_import` tool reference](../tools/content_import.md) for the complete invocation contract.

## Monitor and control imports

Open **Dashboard → Import** to see recent jobs for the selected vault. The vault selector controls the visible history, inbox processing, and manual URL imports. The Import Status table shows queued, processing, completed, failed, and cancelled jobs, along with their outputs or errors.

- Use **Refresh Import Status** to reload the durable job list.
- Use **Process Queue Now** to request an immediate run of the scheduled ingestion worker. The run still observes the configured batch size.
- Queued jobs can be cancelled. A processing job cannot be cancelled because its extraction thread or external OCR request may already be running.

The table refreshes automatically while queued or processing jobs are present.

## Tune queue timing

Two editable settings control imports left for background processing:

- `ingestion_worker_interval_seconds` controls how often the worker checks for queued jobs. A shorter interval reduces pickup latency.
- `ingestion_worker_batch_size` controls how many jobs a worker run processes concurrently. A batch larger than this value requires multiple worker runs.

Keep the immediate default for normal agent-driven research. Tune these settings when using `queue_only=true` or another surface that deliberately leaves jobs queued. A shorter interval reduces background pickup latency; a longer interval reduces scheduling activity. Increasing batch size can improve throughput, but it also increases simultaneous network, OCR, CPU, and external API usage.

Scheduled worker runs do not overlap. Slow imports can therefore extend the effective wait for jobs left in the queue.
