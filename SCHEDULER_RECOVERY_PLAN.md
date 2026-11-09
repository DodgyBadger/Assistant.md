# Scheduler storage-failure recovery

Status: implementation and focused validation complete; protected documentation additions await approval. Scope includes automatic scheduler recovery and useful System Activity diagnostics. Do not rerun the full suite for this change; use the directly relevant scenarios and quality checks.

## Evidence and contract

The production scheduler stopped polling on November 8 at 04:31 UTC after SQLite failed to advance the `vault-state-refresh` job with `database or disk is full`. APScheduler cancelled its wakeup timer before processing and never installed another timer after the exception. Its error was an asyncio callback exception rather than an application activity event. Subsequent activity-file writes also failed with ENOSPC, and the activity sink runs before other diagnostic sinks. The affected workflow executed successfully through the API on November 9.

Recover from temporary job-store errors without permanently abandoning polling. Report degraded and recovered scheduler state, including a safe error classification, retry count, and affected job identity where known. Preserve existing missed-run and source-ownership policies. Do not erase the job store or silently replay a workflow already dispatched before its metadata update failed.

## Implementation

- Own the scheduler recovery boundary in a small runtime scheduler subclass. Catch storage failures, retain a delayed retry, and emit one degraded event and one recovered summary per incident. Preserve pause and shutdown behavior.
- Retain a failed post-dispatch metadata update or removal in process memory, and settle it before reading due jobs again. Block further dispatch while settlement fails. Keep ordinary job-management errors explicit; pending settlement must precede later management writes. This prevents duplicate dispatch during recovery in the same process; it cannot promise exactly-once execution across a process crash with unavailable durable storage.
- Isolate activity-file I/O errors so diagnostic events still reach available sinks and ordinary operations do not fail because logging failed. Emit a compact stderr fallback once per activity outage. Retain bounded outage metadata in memory and append a summary when the activity file becomes writable again. Do not retain arbitrary log payloads or promise persistence while disk writes are impossible.
- Expose scheduler polling degradation in the existing status response without claiming that APScheduler's started flag proves health.

## Behavioral validation

Extend the existing workflow and logging suites. Exercise a real scheduled workflow against a temporary SQLite write failure, restore the database, and verify automatic recovery, one execution per occurrence, and visible degraded/recovered state. Use a real SQLite trigger as the isolated storage fault boundary; application scheduling, dispatch, workflows, persistence, and observations stay real. Exercise an actual unwritable activity-file path and verify other diagnostic sinks and application behavior continue, followed by a System Activity outage summary after recovery. These tests protect ongoing recovery behavior rather than the absence of retired features.

Capture meaningful red against current production behavior, implement, and rerun the same oracles. Run relevant lifecycle, history, logging and activity scenarios, admission checks, and the production quality gate. Record results and limits here. Do not run the full deterministic suite again without a fresh request.

## Documentation review

Review `docs/agent-guides/activity-logging.md` and relevant scheduler/runtime architecture documentation for the resulting contract. Present any proposed protected-document edits in chat and obtain explicit approval before applying them. No protected documentation changes are authorized yet.

## Implementation and validation evidence

- Added `RecoveringScheduler` and `RecoveringJobStore` at the runtime composition boundary. Polling retries temporary storage failures, settles post-dispatch metadata before reading due jobs, and emits one failure and one recovery event per incident. Storage errors during startup do not trigger the existing stale-job-store wipe path.
- Added polling health and retry count to scheduler status. The dashboard distinguishes a scheduler retrying storage access from a scheduler actively polling successfully.
- Activity-file failures no longer abort delivery to the remaining logging sinks. A compact stderr fallback marks the outage; bounded process-local metadata produces `activity_log_recovered` with failed-record count, timestamps, duration, and safe error identity after storage recovers. Validation-artifact write failures also leave later sinks available.
- Meaningful red: `validation/runs/pytest_20261109_173541_8803341a1b/report.md`, three failures demonstrating the stalled polling callback and propagating activity-file error. Initial green: `validation/runs/pytest_20261109_173814_4244f59be6/report.md`, three cases passed. Review strengthened the scheduler oracle to require a different follow-up workflow to complete while the first occurrence remains recorded exactly once.
- Final resumed validation after the server reboot: `validation/runs/pytest_20261109_175512_fe7ae74a94/report.md`, all 11 focused lifecycle, history, logging, activity-retention, refresh, and bootstrap scenarios passed. Inventory has no issues and all 34 policy checks passed. Ruff, Black, mypy for all 300 production files, JavaScript syntax, and diff-whitespace checks passed.
- No full-suite or browser run was performed. SQLite write failures use actual trigger rejection and activity storage uses a real invalid file path; these tests establish recovery behavior without filling a host disk or verifying every filesystem-specific failure mode. They do not promise exactly-once execution across a process crash while durable storage is unavailable.
- Proposed additions to `docs/agent-guides/activity-logging.md` and v0.9.5 release notes were shown in chat; approval remains pending. Those protected files are unchanged.
