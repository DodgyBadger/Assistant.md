# Importing Content

Import vault files or public HTTP/HTTPS URLs into Markdown through Vault Explorer, chat, or workflows. Each import creates a job whose status, output, and errors remain available on the Dashboard. Source files stay in the vault.

## Start an import

In Vault Explorer, make the output folder active. Select supported PDF or image files and choose **Import**, or use **Import URL** for a public source. For local files, choose **Upload → Upload & import** to save the originals and request Markdown versions together.

Check the destination shown in the panel. Saved defaults apply unless you expand **Options for this import** for one-off changes.

Explorer shows progress for file and URL imports. After **Upload & import**, use **Dashboard → Import** to monitor completion. Chat normally waits for imported Markdown before continuing; see the [`content_import` reference](../tools/content_import.md) for workflow calls and background-only submissions.

## Monitor and control imports

**Dashboard → Import** shows recent jobs for the selected vault, including outputs and errors. The table refreshes automatically while work is queued or processing.

- **Refresh Import Status** reloads the job list.
- **Process Queue Now** requests an immediate worker run, subject to its configured batch size.
- **Import defaults** changes PDF output mode, strategy order, and OCR behavior for requests without overrides.
- A job's edit action reopens its source, destination, and saved options in Explorer. Reset those options to use current defaults before resubmitting.
- Queued jobs can be cancelled; processing jobs cannot.

Use **Open Vault Explorer** to start another import.

## Tune queue timing

For background submissions, two settings control pickup and throughput:

- `ingestion_worker_interval_seconds`: shorter intervals pick up queued work sooner; longer intervals reduce scheduling activity.
- `ingestion_worker_batch_size`: larger batches process more jobs concurrently but increase network, OCR, CPU, and external API usage.

Worker runs do not overlap, so slow imports can delay later jobs. Keep the immediate default for normal chat-driven research; adjust queue settings when deliberately processing work in the background.
