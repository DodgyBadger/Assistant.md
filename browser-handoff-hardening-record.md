# Browser handoff review and validation record

Status: implementation started on `dev/browser-handoff`, 2026-11-12. This record tracks independently reviewed contracts, findings and checkpoint evidence. It is not a release-readiness assertion. The authoritative behavior and delivery sequence are in `interactive-browser-implementation-plan.md`.

## Early peer reviews

Architecture reviewer: independent `architecture_review` agent. Security reviewer: independent `security_review` agent. Reviewed baseline: `af0f955` (main plus implementation plan). Both reviews were source/dependency inspection only; no deployment or attack execution was claimed. A follow-up architecture review accepted the narrowly DNS-gated checkpoint 1a below.

| ID | Severity | Contract and concrete evidence | Required correction / disposition |
| --- | --- | --- | --- |
| A1 | High | `core/tools/browser.py:955` declares all browser work replay-safe; `core/chat/run_recovery.py:130–149,179–190` uses it for uncertain calls | Open before interactive writes: classify mixed browser tool conservatively as manual recovery and verify real chat recovery after a controlled write. |
| A2 | High | `core/tools/browser.py:56–63,195–211` owns global counters/semaphore and queues without a deadline | Checkpoint 1a foundation verified: runtime-owned admission, immediate capacity refusal and per-task accounting. Retained lease sharing and mixed-mode coverage remain open until interactive behavior is reachable. |
| A3 | High | Normal shutdown in `core/runtime/context.py:111–135`; partial-start cleanup in `core/runtime/bootstrap.py:453–494` | Open lifecycle requirement: own browser processes/workers/profiles lazily and release on normal/partial startup failure and cancellation. No cleanup behind unbounded operation locks. |
| A4 | Medium | Single delete and purge use separate paths in `api/services/chat_sessions.py:1506–1533` | Open before retained contexts: close/revoke browser contexts on both paths and recheck session access. |
| A5 | Medium | Existing memory guard in `core/tools/browser.py:244–268` precedes every transient launch | Accepted architecture constraint: task calls count on reuse; headroom admission applies to resource growth. Handback, reuse and cleanup remain possible under pressure. Verify with retained-context/resource checks. |
| A6 | Medium | `core/settings/settings.template.yaml:158–161` defaults to four tool calls per task | Open delivery evaluation: preserve shared configurable budget, assess representative interactive journeys, and report clear refusals. Do not exempt interactive calls or count human keystrokes. |
| S1 | High | `core/logger.py:343–346,732,882` disables scrubbing and instruments Pydantic/FastAPI; installed integration captures solved values, errors and model input | Open before human input: bounded sensitive ingress, nonreflecting errors and explicit telemetry capture exclusions. Verify sentinels with instrumentation active. Ordinary typed input body is unsafe here. |
| S2 | High | `core/tools/browser.py:383,420,831` installs page-level routes, follows requests and validates final URL afterward; installed Playwright documents redirect/service-worker/popup routing gaps | Open before retained navigation acceptance: context-level policy before pages, explicit redirect checks before requests, blocked service workers and unsupported WebSockets, popup policy. Assert denied endpoints receive no request. Existing extraction guard gaps must remain visible. |
| S3 | High | `core/tools/browser.py:373–376` omits sandbox intent; installed Playwright adds `--no-sandbox` unless explicitly enabled | Open deployment blocker: explicitly validate sandboxed launch under supported non-root security options, never silently retry unsandboxed. Prefer managed pipe transport over TCP CDP. |
| S4 | High | `api/principal.py:20–41` resolves a principal, not the owning chat/task; access services exist in `core/chat/session_access.py` and `core/runtime/task_access.py` | Open before API: authoritative service validates exact chat/task, handoff generation, page and observation ownership. |
| S5 | Medium | CSRF behavior differs by auth mode in `core/authentication/middleware.py:118–130`; cache prevention in `api/application.py:62–76` is path-dependent | Open before API: use protected `/api/` routes, no-store, bounded content types/bodies and reviewed origin behavior across supported auth modes. HTTP control/frame polling initially. |
| S6 | Medium | `core/web/security.py:15–39` checks DNS separately from browser connection | Known limitation requiring security disposition: no socket-pinned egress claim; inspect denial behavior and supported deployment boundary. Avoid synchronous DNS in interactive hot paths. |

Line references identify the reviewed baseline and may move after edits. No security finding is closed by architecture agreement alone. Before related behavior is accepted, record its fix revision, executed evidence and independent verification.

## Agreed initial service contracts

The runtime owns admission and browser lifecycle. Admission accounts for actual allocated transient/retained leases, consults current capacity for new allocations and returns bounded refusal without evicting human/active work. Retained context reuse does not allocate twice. Agent calls obey shared task limits. Resource headroom checks apply at allocation, not at release or handback. Keep admission bookkeeping thread-safe; Playwright contexts/pages stay on their runtime event loop.

Separate browser instance, page, observation/viewport and task/handoff identities. Elements refer to the observation the agent received; server code cannot substitute its current revision. Ownership and lifecycle are separate concepts, with serialized transitions and explicit terminal errors. A stale request cannot transfer authority to a cancelled task or replacement page.

Only one inactivity setting is introduced: `browser_idle_timeout_seconds`, default 600, positive bounded numeric value through existing settings repair/editor conventions. Actual setting metadata and validation will be implemented at checkpoint 1c. Handoff begins the clock; meaningful human/agent browser activity refreshes it; passive polling does not. Chat currently has no fixed task deadline and disables model-stream idle timeout while tools run; preserve that path and validate actual handoff wait later rather than creating a second timer.

No new browser history schema is needed for checkpoint 1a. Before checkpoint 3, define minimal safe handoff history/status projections in existing chat persistence; never persist browser profiles, credentials or frames. Do not settle a new table or migration before inspecting existing records at that checkpoint.

## Checkpoint evidence

### Existing call-budget baseline

The unchanged admitted `test_browser_budget_is_scoped_to_actual_execution_task` passed on the clean implementation baseline. Evidence: `validation/runs/pytest_20261112_184436_2a39394702/report.md`. It proves task-scoped call refusal via real runtime/tool execution and invalid-URL policy; it does not launch Chromium.

### 1a: bounded capacity refusal

Acceptance: while one real browser tool call holds sole capacity during external DNS latency, a second tool call returns explicit capacity refusal without queueing. After the delayed DNS resolves to a denied loopback address, the first call fails real URL policy and releases capacity; a later request is admitted. The selected source is `validation/scenarios/integration/core/test_browser_call_budget.py`; the external DNS gate lives in `validation/support/browser/admission.py`.

Only one declared hostname's DNS delay/answer is substituted. Application runtime, settings, tool, admission, URL policy and errors remain real. The synchronous resolver runs on a separate thread-owned loop so the concurrent tool call can execute. This does not prove same-loop responsiveness, Chromium launch/cleanup or retained interactive behavior. No browser is needed because policy rejects the released private address before launch. Meaningful red: `validation/runs/pytest_20261112_184801_48c39d817d/report.md` (one existing pass, one new failure). The new case failed because the second real call queued in `_acquire_session_slot` until its one-second assertion deadline. Green with the same acceptance condition: `validation/runs/pytest_20261112_185019_ededfdda07/report.md` (two passes). Added independent assertions for structured refusal reason and actual System Activity emission, then reran successfully in `validation/runs/pytest_20261112_185112_7d86d4f141/report.md` (two passes); these additions strengthen the oracle and do not replace the original capacity assertion. Scenario digests are retained by the harness.

### Checkpoint 1a implementation and checks

`RuntimeContext` owns `BrowserService`, composed during bootstrap and closed in both normal shutdown and partial-start cleanup. The browser tool routes call accounting and capacity/headroom admission through it. Existing task budgets retain their behavior; terminal observers remove completed counters without evicting active tasks. Unscoped fallback counters use weak task references instead of integer task identities. A reservation lasts until the real call releases its resource, including URL denial and cancellation; new allocations inspect current capacity without an unlimited queue. The tool exposes a structured refusal reason. No interactive placeholder, extra feature flag, browser process or human input route was introduced at this checkpoint.

Completed checks: final four-case admission/shutdown run passed in `validation/runs/pytest_20261112_185425_65e562af82/report.md`; full Ruff/Black/MyPy production gate passed (304 typed source files); admission inventory has no issues; 34 collection/admission policy checks passed. Browser launch/retained context and mixed-mode budget acceptance remain unproven and blocked by prerequisites below.

Independent checkpoint review requested changes on two findings. High: coordinator shutdown only requests cancellation, so checking live reservations immediately could abort teardown before cancelled calls release them. The new runtime-shutdown scenario reproduced this in `validation/runs/pytest_20261112_185311_a4f21877f4/report.md`. Closing admission followed by an owned, bounded drain fixed it; the same acceptance passed in `validation/runs/pytest_20261112_185355_89ee1e4793/report.md`. Added caller-cancellation coverage passed in the final four-case run. Other runtime teardown executes in `finally` if browser cleanup fails. Medium: a generic failed result did not prove usable admission after release; strengthened the oracle to require actual blocked-policy observations for the first and later calls. The independent reviewer re-reviewed the code and evidence, found both findings resolved and reported no remaining checkpoint 1a blocker, subject to the final run that subsequently passed. Actual engine cleanup, drain-timeout behavior, repeated cancellation and simultaneous teardown failures remain later lifecycle-hardening checks, not claims established here.

## Environment limits

The environment lacks browser shared libraries (including NSS/NSPR, X11/GBM, fonts/audio dependencies). Installed the lockfile-matching Chromium bundle using `uv run python -m playwright install --no-shell chromium`. A real launch preflight failed; `uv run python -m playwright install-deps chromium` then required unavailable root authentication. The user has been asked to install host prerequisites or provide a prepared environment. This is an environment blocker for real browser checkpoints, not meaningful behavioral red evidence. No extracted investigation-runtime fallback or production workaround has been introduced.

## Outstanding readiness work

All later behavior checkpoints and the seven-stage hardening phase remain pending. No full core suite is requested during this initial checkpoint. Protected product documentation edits remain pending exact-draft approval under repository policy.
