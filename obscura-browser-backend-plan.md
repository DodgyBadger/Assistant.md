# Obscura Browser Backend Feasibility and Implementation Plan

## Status

Finalized feasibility checkpoint from 2026-09-22 on `dev/replace-playwright`. The production backend remains Playwright with Chromium. The experimental Obscura adapter and packaging changes were removed because the request-interception gate below fails on dynamic pages; the opt-in probe at `validation/scenarios/experiments/obscura_playwright_interception_probe.py` preserves a reproducible check for later releases.

## Objective

Determine whether AssistantMD can use Obscura for the existing read-only `browser` extraction capability while preserving its model-facing contract, network boundary, bounded resource behavior, per-call state isolation, structured failures, and deterministic validation coverage. The footprint goal is to remove the bundled Chromium runtime; removing the Playwright Python client is a separate, later decision.

## Executive Conclusion

Obscura is technically plausible for AssistantMD's narrow extraction workload, but an unconditional replacement is not ready to ship from the evidence available today. A local spike with Obscura `v0.2.3` and Playwright `1.58.0` successfully ran the existing `BrowserTool._run_browser_session` path over CDP without changing the extraction logic, including JavaScript execution, semantic-root extraction, heuristic-root extraction, selectors, title retrieval, and link extraction. The render-enabled daemon settled around 35-39 MiB RSS and the no-render daemon around 29 MiB RSS in these synthetic probes.

The resource savings are substantial enough to justify revisiting the experiment, but Obscura `v0.2.3` is a no-go for the production backend. If request interception becomes compatible, the recommended first implementation remains a reversible hybrid: replace the bundled Chromium executable with a pinned Obscura no-render server while retaining Playwright as the CDP client. That shape would preserve the mature locator and request-routing layer, including AssistantMD's GET/HEAD-only enforcement, and capture most of the runtime memory and browser-binary savings. It would not remove the approximately 130 MiB installed Playwright Python package.

A true Playwright removal should be considered only after the hybrid passes the full contract and compatibility gates. That phase should use a small purpose-built CDP adapter with `Fetch` interception and `Runtime`/`DOM` evaluation. Direct use of `obscura fetch` or Obscura's MCP server is not an acceptable backend for the current tool because neither path, as currently documented, preserves AssistantMD's per-request GET/HEAD policy and the MCP surface broadens the capability into stateful interaction.

Production adoption is conditional on resolving or containing upstream security and maturity risks. Obscura is currently a fast-moving `0.2.x` engine with acknowledged gaps from Chromium parity. At the time of this snapshot, upstream issue [#1046](https://github.com/h4ckf0r0day/obscura/issues/1046) describes a high-severity CPU denial of service reachable through an untrusted `Set-Cookie` response header, and issue [#1059](https://github.com/h4ckf0r0day/obscura/issues/1059) describes unbounded render-build raster allocation. The no-render build avoids the reported raster path, but every browser invocation still needs a parent-enforced wall-clock deadline and forcible child-process cleanup. The production version must have no unmitigated high-severity issue reachable through ordinary untrusted page loading.

## Gate 1 Spike Result

The process-lifecycle portion works: AssistantMD can start a pinned Obscura no-render child on loopback, verify its version and CDP readiness, connect Playwright, extract static and `data:` fixtures, enforce a V8 heap cap, and reap a child that ignores graceful termination. A public `example.com` extraction also succeeds with status, title, selectors, content, and links after avoiding Playwright locator waits that remain coupled to incomplete Obscura navigation lifecycle events.

The request-interception gate currently fails. On `https://quotes.toscrape.com/js/`, Obscura `v0.2.3` without Playwright routing executes the page's JavaScript and produces all ten quote records. With `page.route("**/*", handler)` installed and every intercepted request continued, Playwright receives only the top-level document request, Obscura never loads the JavaScript subresources, and the dynamic records remain absent. Removing or limiting the route after navigation does not resume those resources.

This is a blocking incompatibility because the route is not an optional optimization: it enforces AssistantMD's GET/HEAD-only policy, validates every redirect and subrequest against the public-network boundary, and blocks downloads and nonessential resources. Replacing it with injected JavaScript hooks would be bypassable and would materially weaken the current security contract. Gate 1 is therefore no-go on Obscura `v0.2.3` unless upstream request interception is fixed or another design provides equivalent network enforcement without exposing a broader browser authority.

## Current Contract and Invariants

- Keep the model-facing tool named `browser` with the existing arguments and output shape documented in `docs/tools/browser.md`.
- Allow only `http`, `https`, and test-only `data:` navigation, and reject local, loopback, link-local, and private targets at initial navigation, redirects, and subrequests.
- Permit only GET and HEAD requests; block downloads and prevent browser-created files.
- Create fresh browser state for every call and never configure persistent cookies, local storage, profiles, or an Obscura `--storage-dir`.
- Preserve the process-wide concurrency limit, per-execution-task call budget, memory admission check, navigation and selector timeouts, content and HTML caps, link cap, untrusted-data markers, and structured tool-failure behavior.
- Preserve the stable lifecycle and policy events already emitted by `BrowserTool`; backend identity may be added as a field but must not rename the existing events.
- Keep `web_extract` as the preferred static-page path and do not add automatic fallback between extraction strategies.

## Evidence Collected

| Probe or source | Observation | Interpretation |
| --- | --- | --- |
| Existing code | `core/tools/browser.py` launches Playwright Chromium per call, installs a route handler for every request, blocks non-GET/HEAD methods and image/media/font resources, applies public-network URL validation, creates a new context, and bounds extracted content. | The browser engine is not a simple executable substitution; request interception and context behavior are security contracts. |
| Existing deployment | `docker/Dockerfile` installs Chromium with `playwright install --no-shell chromium`; development setup and doctor commands also know Playwright explicitly. | A replacement touches packaging, setup diagnostics, settings prose, docs, and validation as well as the tool implementation. |
| Local footprint sample | The installed Playwright Python package occupies about 130 MiB. The locally cached full Chromium directory occupies about 620 MiB; this is a development-machine observation, not a promised production-image delta. | Retaining Playwright over CDP still leaves material disk cost, but eliminating Chromium should provide the largest immediate reduction. |
| Obscura `v0.2.3` aarch64 release | The render-enabled `obscura` binary was 77,466,600 bytes and the no-render binary was 58,657,768 bytes. The separate worker is not needed for the documented single-server/single-fetch paths and should not be packaged unless a test proves otherwise. | The no-render artifact is the preferred candidate because AssistantMD does not expose screenshots, PDFs, or screencasts. |
| Local CDP compatibility sample | The existing session implementation worked through `connect_over_cdp` for a JavaScript-mutated semantic `<main>` fixture and for the current visibility/scoring heuristic on a long `<div class="content">` fixture. The no-render build also passed the heuristic fixture. | The narrow DOM-extraction workload has an encouraging compatibility path, but two synthetic fixtures are not a production corpus. |
| Obscura network default | A direct fetch to `127.0.0.1` was rejected by Obscura without `--allow-private-network`. | This provides useful defense in depth, but AssistantMD must retain its own URL validation and interception rather than delegating the boundary solely to the engine. |
| Obscura release posture | The official `v0.2.3` notes describe CDP authentication, isolation, concurrency, and compatibility hardening; the `v0.2.0` notes explicitly state that rendering is not at complete Chromium parity. | The project is active and improving quickly, but the integration must pin a tested version and assume compatibility can change between releases. |
| Playwright CDP documentation | Playwright documents `connect_over_cdp` as significantly lower fidelity than its native protocol and warns that some functionality can break when Playwright did not launch the browser. | The exact AssistantMD call surface needs contract tests; broad claims of drop-in compatibility are insufficient. |

## Benefits Worth Validating

- Lower steady-state and per-session memory may make the browser viable on the approximately 1 GB deployment profile, though the profile must not be changed until cgroup measurements prove safe headroom under hostile pages.
- Smaller browser artifacts and removal of Chromium-specific shared libraries may materially reduce image download size, build time, cold start, and host storage.
- Fast process startup makes per-call process isolation and hard-kill cleanup practical, aligning well with AssistantMD's current fresh-state-per-call contract.
- Linux amd64 and arm64 release artifacts match the architectures published by AssistantMD's release workflow.
- Apache-2.0 licensing is compatible in principle, subject to retaining notices and including the pinned binary in the software bill of materials.
- Obscura's default private-network denial can supplement AssistantMD's existing application-level network checks.

## Trade-offs and Risks

- Web compatibility is the primary product risk. Obscura implements a browser surface rather than embedding Chromium, so modern framework hydration, uncommon DOM APIs, CSS-dependent visibility, frames, encoding, redirects, and anti-bot scripts can behave differently or fail.
- Security maturity is the primary release risk. AssistantMD intentionally loads untrusted pages, while the upstream issue tracker currently contains remotely reachable resource-exhaustion findings. Upstream documentation emphasizes session isolation but does not establish Chromium-equivalent renderer sandboxing; deployment isolation therefore needs an explicit review.
- The hybrid path retains Playwright's Python/driver footprint and adds an Obscura process lifecycle. It is a Chromium replacement first, not a complete Playwright replacement.
- A direct CDP client reduces disk use further but transfers protocol framing, event ordering, interception, context isolation, timeout, and cleanup responsibilities into AssistantMD.
- A persistent Obscura daemon has a smaller launch cost but expands failure sharing and long-lived-state risk. A per-call child better matches current isolation, provided port allocation, readiness, and termination are reliable.
- Obscura `serve --port 0` printed an endpoint containing port `0` in the local probe, so ephemeral-port discovery cannot be assumed. The spike must resolve lifecycle and port allocation without a bind race before choosing an in-process child; otherwise use a separately supervised fixed-port service with authentication and no host port exposure.
- Stealth mode changes network and page behavior through tracker blocking and is not required by the current product contract. It should remain off during parity evaluation and should not be enabled by default without a separate policy decision.
- Release binaries require a new dependency update and integrity process outside `uv.lock`. Version, architecture, URL, and SHA-256 must be pinned, and upgrades must rerun the browser corpus.

## Recommended Architecture Sequence

### Gate 1: Hybrid Evaluation Adapter

1. Add a small internal runtime boundary under `core/browser/` that starts or connects to a pinned Obscura no-render CDP server, waits for readiness, reports its version, and guarantees termination on success, cancellation, timeout, and application shutdown.
2. Keep the existing Playwright page/context/locator code for this gate, replacing `chromium.launch()` with `chromium.connect_over_cdp()` and explicitly retaining `page.route("**/*", BrowserTool._route_request)`.
3. Prefer one Obscura child per admitted browser call because the current concurrency default is one and per-call teardown limits state leakage and denial-of-service duration. If reliable collision-free endpoint allocation cannot be implemented, evaluate a separately supervised, loopback/private-network-only daemon with a generated CDP token instead of silently choosing a fixed application port.
4. Apply both the existing navigation timeout and a parent-owned hard deadline. On deadline or protocol loss, terminate the exact Obscura child, wait briefly, then kill it if necessary; never leave a detached process.
5. Run the no-render build with a measured V8 heap cap and the existing cgroup admission policy. Select the cap from corpus results rather than adopting upstream's multi-gigabyte default.
6. Do not expose the backend selector as a normal user-facing setting during evaluation. A test/development-only switch may select Chromium or Obscura for A/B measurement; a permanent setting is justified only if both backends will be supported intentionally.

### Gate 2: True Playwright Removal

1. Proceed only if Gate 1 meets every functional, security, reliability, and resource threshold.
2. Implement only the CDP subset required by the current tool: target/context lifecycle, page navigation and lifecycle events, `Fetch` interception, response status/final URL, selector waiting, and bounded `Runtime.evaluate` extraction.
3. Make `websockets` an explicit dependency if the existing transitive installation is used; do not rely on a transitive package contract.
4. Move root selection and extraction into versioned JavaScript payloads returning a bounded JSON result so output is capped before large data is retained in Python. Preserve the Python markdown fallback only where required by the current output contract.
5. Remove Playwright from `pyproject.toml` and `uv.lock` only after the direct adapter passes the same contract suite and the hybrid comparison corpus.

### Rejected Backend Shapes

- Do not implement the current tool with `obscura fetch` alone unless upstream adds a tested read-only subrequest policy or equivalent interception hook. Top-level URL validation does not prevent page JavaScript from issuing mutating requests.
- Do not map the built-in `browser` tool to Obscura MCP. Its click, fill, type, keypress, and persistent multi-call page behavior exceed the established extraction-only authority.
- Do not enable `--allow-private-network`, persistent storage, proxying, or stealth by default.
- Do not ship a silent Chromium fallback after an Obscura failure; ADR 0027 requires explicit strategies and visible failures.

## Validation-First Contract

### Deterministic Scenario Coverage

Extend `validation/scenarios/integration/core/browser_resource_policy.py` or split a focused `browser_backend_contract.py` scenario before implementation. The scenario should run against a controlled engine fixture or protocol fake and assert:

- the model-facing schema and success/error output remain unchanged;
- semantic root, heuristic root, explicit extraction selector, selector wait, JavaScript mutation, optional links, truncation, and empty-content behavior;
- initial private URL, private redirect, private subresource, and DNS-rebinding-equivalent resolution are blocked;
- POST/PUT/PATCH/DELETE subrequests are blocked while GET/HEAD continue;
- downloads create no files and return the stable `download` result type;
- every call receives a clean context with no cookie/local-storage leakage;
- cancellation, navigation timeout, selector timeout, protocol disconnect, engine crash, and parent hard deadline close the context and reap the exact child process;
- concurrency and per-task call limits remain authoritative;
- engine absence, unsupported architecture, checksum mismatch during image construction, and incompatible version fail clearly;
- existing event names remain stable and include `engine="obscura"` where backend identity is operationally useful.

The durable events are the existing `browser_session_launch_started`, `browser_session_launch_completed`, `browser_navigation_response_received`, `browser_request_blocked`, `browser_navigation_succeeded`, `browser_extraction_succeeded`, `browser_navigation_failed`, `browser_session_closed`, and `browser_launch_refused` events. Add only the minimum fields needed for validation: `engine`, `engine_version`, and a stable termination `reason` where applicable. Do not log CDP tokens, raw query strings, page content, or full URLs beyond the existing sanitization behavior.

### Opt-in Compatibility Experiment

Add an `experiments` scenario that runs the same public-page corpus through Chromium and Obscura without making it a merge gate. The corpus should include representative static pages, client-rendered SPAs, delayed selectors, redirects, frames, common encodings, large DOMs, cookie-heavy responses, and sites previously observed in real AssistantMD use. Record success classification, final URL, status, selected root strategy, content length/hash, useful-text recall from stable probes, wall time, peak child RSS, cgroup peak, and forced termination count. Do not assert byte-identical prose from live sites.

### Go/No-Go Thresholds

- All deterministic browser contract assertions pass on amd64 and arm64.
- No must-work corpus site regresses, and aggregate successful useful-content extraction is no worse than Chromium by more than an agreed small tolerance documented with the experiment results.
- Median and p95 latency improve or remain acceptable, and measured peak cgroup memory supports a lower admission threshold without OOM or swap pressure.
- Repeated cancellation, timeout, and hostile-page probes leave zero child processes and no cross-call state.
- No known high-severity upstream issue reachable through ordinary page loading remains unpatched or uncontained by a tested parent hard deadline and process-isolation boundary.
- The image build uses pinned per-architecture checksums, produces an SBOM entry and license notice, and demonstrates the expected image-size reduction.

## Expected Code and Documentation Surface

- `core/tools/browser.py`: preserve the public tool and formatting contract; move engine lifecycle behind an internal boundary; make errors and descriptions engine-neutral.
- `core/browser/` (new): Obscura process/CDP lifecycle, readiness, version checks, and later the minimal direct CDP client if Gate 2 proceeds.
- `core/settings/__init__.py` and `core/settings/settings.template.yaml`: retain existing limits, update Chromium-specific descriptions, and add only a restart-required internal endpoint/runtime setting if lifecycle design proves it necessary.
- `validation/scenarios/integration/core/browser_resource_policy.py` and a possible new backend contract scenario: add the deterministic assertions above.
- `validation/scenarios/experiments/`: add the explicit Chromium-versus-Obscura compatibility and resource corpus.
- `docker/Dockerfile`: install the pinned no-render binary for the target architecture, verify its checksum, remove the Chromium install step, and remove Chromium-only system libraries only after runtime inspection proves they are unused elsewhere.
- `scripts/dev`: replace Playwright Chromium setup and doctor checks with Obscura version/readiness diagnostics while keeping a development comparison path through Gate 1.
- `pyproject.toml` and `uv.lock`: retain Playwright during Gate 1; remove it only in Gate 2 after direct-CDP parity.
- `docs/tools/browser.md`, `docs/development/dev-setup.md`, `docs/setup/installation.md`, `docs/setup/security.md`, and relevant settings comments: describe only the resulting current contract and supported resource profile.
- `docs/development/adr/`: add a new decision record for the engine/process boundary rather than rewriting ADR 0027's historical decision.

## Settings, Secrets, and Persistent Runtime State

No browser profile or session data should be persisted under the configured data or system roots. A same-container loopback child requires no new secret. If the selected lifecycle uses a separate service or any non-loopback bind, CDP authentication is mandatory; its bearer token must be generated and handled through the existing secret boundary, never written to logs or committed in `system/secrets.yaml`. Any new endpoint or backend setting is restart-required and must default to a safe unavailable state when the runtime is missing.

## Concrete Next Steps

1. Periodically rerun `validation/scenarios/experiments/obscura_playwright_interception_probe.py` against a pinned newer Obscura release and review upstream interception and security changes.
2. Resume Gate 1 only when Playwright routing can continue the full dynamic-page request graph while preserving AssistantMD's method and network restrictions.
3. Add deterministic backend contract assertions for request methods, private subresources, state isolation, cancellation, hard deadlines, and child reaping before restoring any production adapter.
4. Run the targeted browser scenarios and the opt-in comparison corpus on amd64 and arm64; attach raw measurements and the chosen V8/memory limits to this plan.
5. If adopted, update packaging and current-contract documentation, record the engine/process decision in an ADR, run the Production Python Quality Gate, run the targeted scenarios, and then run `python validation/run_validation.py run integration/core` once during hardening or merge preparation.

## Primary References

- [Obscura README and CDP/CLI/MCP surface](https://github.com/h4ckf0r0day/obscura/blob/main/README.md)
- [Obscura `v0.2.3` release notes and checksums](https://github.com/h4ckf0r0day/obscura/releases/tag/v0.2.3)
- [Obscura CLI reference](https://github.com/h4ckf0r0day/obscura/wiki/CLI-reference)
- [Obscura issue #1046: response-header CPU denial of service](https://github.com/h4ckf0r0day/obscura/issues/1046)
- [Obscura issue #1059: render-build raster allocation](https://github.com/h4ckf0r0day/obscura/issues/1059)
- [Playwright Python `connect_over_cdp` fidelity warning](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)
- [ADR 0027: stable web capabilities and explicit strategies](docs/development/adr/0027-stable-web-capabilities-and-explicit-strategies.md)
