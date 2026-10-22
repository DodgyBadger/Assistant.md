# Durable model aliases

## Status and scope

Implementation and final validation are complete on `dev/durable-model-aliases`, including the catalog refresh and ownership reconciliation. The architecture and shortened authoring additions are applied, and the further architecture sentence covering family adoption is approved and applied. Keep script-facing model names usable across provider rebranding and managed catalog updates through a canonical alias and one plain editable alternate list. Preserve explicit model selection, existing defaults, and user-defined models.

This effort touches settings persistence, model lookup, model configuration API/UI, managed configuration upgrades, chat selection, and alias consumers in scripts and delegates. Secrets stay in the existing secret store. Validation uses isolated runtime roots without modifying active settings, secrets, or vault data.

## Current implementation and architecture

`core/settings/store.py` defines provider bindings using `provider`, `model_string`, capabilities, description, and editability; the model dictionary key is currently the script-facing alias. `core/llm/model_utils.py` resolves aliases and checks capabilities, with inconsistent normalization between mappings and capability lookups that must be unified. `core/llm/model_selection.py` reserves `none` for skip mode. `core/llm/model_factory.py` checks provider readiness and constructs adapters, including a deterministic validation-only `test` model.

`core/settings/config_editor.py`, `api/services/configuration.py`, and the model configuration UI expose bindings. Most shipped generative bindings are editable, so editability does not prove whether a binding is customized or safe to overwrite. `core/settings/upgrades.py` owns deterministic settings repair upgrades. The settings editor metadata and persistent invalid-value warnings also consume model choices.

ADR 0007 establishes shared settings-backed model binding across chat, delegates, workflows, and scripts. ADR 0009 establishes multimodal policy and capability gates. Preserve these boundaries and existing retry/recovery behavior while adding shared alias lookup.

## Alias contract

| Concept | Contract |
| --- | --- |
| Canonical alias | Preferred script-facing model name; centrally controlled for managed entries and editable for custom entries. |
| Alternate aliases | One user-editable list of accepted names on the model, such as `alternate_aliases: [alias1, alias2, alias3]`. |
| Provider binding | The provider and API model identifier associated with the model. |

Automatically preserved aliases are ordinary list entries. Users may add, edit, or remove them under the same collision rules. Do not introduce origin fields, protected historical names, separate permissions, or alias-to-alias chains. Alternates resolve directly to the same canonical model and inherit its binding and capabilities. Scripts and APIs accept them; ordinary pickers show one model choice rather than a choice for each alternate.

On an automatic canonical rename from A to B, atomically make B canonical and append A to the current alternate list if absent after normalization. Preserve the other entries currently in that list. If B later becomes C, append B and keep A only if it remains in the user-edited list. If the canonical name is unchanged, leave alternates unchanged; do not regenerate names the user removed. Promoting an existing same-model alternate to canonical removes that alternate from the list and preserves the previous canonical as an alternate. Case-only or whitespace-only changes must not create duplicate lookup identities.

## Managed updates and custom models

Automatic managed updates are accepted, including canonical changes that preserve the previous name as an alternate. Use a stable catalog identity independent of canonical spelling to recognize the same managed entry through renames. Validate binding updates and names together; repeated upgrades must be idempotent.

Custom model canonical names remain editable, and custom bindings must survive repair and catalog refresh. Recommend preserving the prior name as an alternate on custom renames. Never infer that different user entries represent the same model merely because they share a provider model identifier.

Accepted: managed canonical aliases and provider/model bindings are centrally controlled. Users can duplicate a managed model as custom to edit its binding independently; catalog updates must not retarget the custom copy. Alternate aliases remain editable on managed models. For existing installations, adopt entries under standard canonical or historical built-in aliases when their provider and model family match, even if an earlier installation explicitly marked them unmanaged. Keep models under custom aliases independent. A standard alias bound to a different provider or model family remains unchanged and raises an actionable System warning; the user can restore the expected family or rename the custom entry and remove the standard alias from its alternates. Namespace collisions block the catalog update atomically.

## Collision validation

Use one consistent lookup normalization for canonical and alternate names. Inventory current names before imposing stricter syntax; trimmed lowercase identifiers are a candidate rule, with primary aliases used as UI labels. Reserve blank references, `none`, and validation-only identifiers as appropriate to current execution contracts.

Reject collisions between different models, repeated alternates, and alternates equal to their own canonical name. Allow promotion of an alternate belonging to the same model through the atomic rename rule. Compile and validate changes before publishing them; collisions must not partially write settings or update caches. A failed managed update retains the working configuration and raises an actionable warning.

Invalid persisted entries must remain diagnosable through administration. Do not choose an arbitrary winner for ambiguous names. Resolve canonical and alternate references through the same capability checks; adding an alternate must not make an otherwise invalid binding eligible.

## Chat resolution and default behavior

New chats use the configured default model. Existing chats resolve their selected name through canonical and alternate lookup. If that alias no longer resolves, use the configured default. If the default also cannot resolve, raise the configuration error in the System tab and fail the operation. Do not search for another model.

Apply fallback only when the chat's model reference no longer resolves. Provider outages, authentication failures, rate limits, capability failures, and inference errors retain their existing handling. This does not introduce additional fallback for explicit script or delegate requests, change omitted-model inheritance, or require session binding snapshots.

Trace the current chat creation, loading, and request paths before implementation so the rule applies consistently to restored and active sessions. Preserve existing model-dependent preparation and retry behavior. Make the effective model apparent through the existing chat presentation and activity records where those already expose model identity.

## Configuration UI and persistence

Managed canonical aliases are read-only in the normal editor. Expose alternates as one editable string list; keep custom canonical names editable. Show primary aliases in model lists and pickers; lookup names must be unique. Copy existing theme-aware styling for every control, label, button, and warning.

When a saved alternate resolves, show the corresponding canonical model without creating duplicate options. Do not rewrite saved references merely for presentation. Preserve exact invalid saved settings and show persistent issues through the existing settings status surfaces. An unresolved default must be reported in the System tab.

Make settings schema changes additive and upgrades idempotent. Preserve user bindings, existing references, authored scripts, capabilities, and historical records. Add alternate lists without requiring blanket reference rewrites. Do not introduce new execution snapshots or database migrations without a demonstrated requirement from the alias changes.

## Initial implementation inspection

At initial inspection, the shipped settings template marked most generative models user editable, and `ModelConfig` had no stable managed identity or alternate-name field. Adoption therefore cannot use the editability flag as evidence of an untouched binding. The initial exact binding-and-capability adoption baseline was subsequently replaced by the approved standard-alias/provider/family rule, which handles older generations and explicit unmanaged flags. Settings repair currently fills missing entries without updating existing model bindings and prunes removed non-editable entries, so the managed rename path must run before generic merging/pruning to preserve aliases and current user-edited alternate lists.

The initial model editor performed a rename as a create followed by a separate delete. The implemented atomic operation preserves the previous canonical as an alternate and rejects collisions before writing. Managed editing must permit alternate-list edits while rejecting changes to the canonical name and binding. Duplicate-as-custom must create a fresh custom entry without copying managed identity or claiming the source model's alternate names.

The initial general settings editor treated model choices as literal canonical dictionary keys. Resolution and validation now accept valid alternates used by `default_model` without creating duplicate picker options. Both ordinary chat and deferred-review preparation use the shared chat fallback resolver, preserving capability and provider error handling.

## Testable implementation slices

Each slice starts with failing behavior assertions, ends with directly relevant passing checks and a local checkpoint commit, and preserves the previous slice's contracts. No runtime roots outside isolated validation fixtures may be changed.

### Slice 1 Shared alias registry

Completed: the scenario first failed because alternate names were absent from the API response. After implementation, `model_alias_resolution`, `decision_model_configuration`, and `settings_editors` passed (run `20261022_220524_585256`); eight registry unit checks passed, and the complete production Ruff, Black, and MyPy gates passed. Binding/capability/provider caches publish as one snapshot; invalid entries remain diagnosable without resolving ambiguous names.

Deliver an additive alternate-name field, one normalized registry shared by binding and capability lookup, collision diagnostics, and atomic cache refresh. Persisted invalid names remain visible to administration; ambiguous names never resolve to an arbitrary model. Reserve `none`, and allow the canonical validation-only `test` fixture while disallowing alternate names that hijack it.

Validation: focused registry assertions plus `integration/core/model_alias_resolution` demonstrate case/whitespace normalization, alternate/canonical binding and capability equivalence, hidden alternates in model listings, collision rejection without changing saved settings, and diagnostics for invalid persisted data. Existing decision-model and settings-editor scenarios stay green.

### Slice 2 Managed catalog updates

Completed: six catalog/storage checks cover renames, alternate promotion/removal, idempotence, custom preservation, verified legacy adoption, atomic collision rollback, persisted upgrades, and rollback backups. The four relevant integration scenarios passed (run `20261022_220822_830310`). Fresh templates now carry stable managed IDs and centrally controlled bindings; known managed updates persist automatically with a backup, while customized legacy entries remain custom.

Deliver stable managed identity, provider/family recognition for adopting entries under standard aliases, atomic managed rename/update behavior, and preservation of custom bindings. Update known managed entries automatically when settings load; preserve aliases and current alternate lists in the persisted settings. A collision retains working settings and raises a System configuration warning. Repair must use the same update helper and must not prune retained renamed managed entries or overwrite customized entries.

Validation: pure upgrade assertions and the alias scenario cover untouched adoption, customized/ambiguous preservation, repeated renames, same-model alternate promotion, idempotence, deleted alternates remaining deleted, binding updates, collisions without partial writes, repair, and fresh-template seeding.

### Slice 3 Model editing and settings integration

Completed: the managed-alternate edit scenario first failed on the prior read-only rejection, then passed with atomic custom rename, managed binding protection, editable alternate removal, and alternate-backed default validation (run `20261022_221058_656712`). Twenty-two focused Python/module checks passed, including three browser model-editor behavior checks; production quality gates passed and CSS compilation completed. Managed duplication starts a separate custom draft without managed identity or inherited alternate names.

Deliver API and theme-aware UI for one alternate list, centrally controlled managed bindings, atomic custom rename preserving its prior alias, and Duplicate as custom without managed identity or copied alternate names. Model pickers show canonical entries only; valid saved alternates remain accepted and display the canonical option. Use the same collision checks before any write, including Advanced YAML edits that alter the registry.

Validation: alias integration scenario verifies API ownership, custom duplication/rename, list editing/removal, atomic failure, saved alternate defaults, invalid-reference issues, and custom preservation. Browser behavior checks exercise save payloads, managed field protection, duplicate drafts, atomic rename, and canonical selection without duplicate options. Review keyboard controls and existing themes; rebuild CSS if new utilities require it.

### Slice 4 Chat default fallback

Completed: chat fallback first failed on the missing resolver, then passed alongside alias and delegate scenarios (run `20261022_221441_155256`). Chat retry and persisted-session checks also passed (run `20261022_221718_103817`); expanded collision, custom-copy, System-error, and provider-failure boundaries passed (run `20261022_221901_512858`). Twenty-five focused unit/module checks and the complete production quality gate passed. Chromium verified managed edits, editable alternates, custom duplication, keyboard focus, theme-dependent colors, and no horizontal overflow in six themes at desktop and mobile sizes. The browser no longer chooses an arbitrary first available model.

Deliver shared chat reference resolution: retain a valid canonical or alternate selection, fall back only for an unresolved selected name, and fail with a System-tab issue if the default cannot resolve. Preserve explicit script/delegate failure, omitted-model inheritance, capability checks, provider failure handling, and current retries. Use the resolved model before model-dependent preparation and show it in existing chat metadata. Do not choose an arbitrary available model in the browser.

Validation: deterministic chat scenario and browser selection tests cover valid selections, alternate defaults, removed aliases, invalid defaults, restored sessions, and provider/capability errors without fallback. Extend existing delegate and chat regression assertions where those boundaries are shared.

### Activity events

At successful managed updates emit `managed_models_updated` with `status`, updated canonical aliases, and rename pairs; on blocked updates emit `managed_models_update_blocked` with `status` and bounded diagnostic reasons. At chat fallback emit `chat_model_fallback` with `status`, requested alias, and effective canonical alias. Existing model edit activity records gain canonical/alternate edit metadata as needed. Do not log secrets, prompts, or credentials.

### Final hardening

After all slices pass relevant checks, run the full deterministic `integration/core` suite and complete production Ruff, Black, and MyPy gates. Review theme-aware model editing, keyboard access, persistent warnings, cache refresh, custom-data preservation, and chat selection. Record outcomes here before final delivery. No live inference calls are required.

## Hardening review

Scope and ownership: the authoritative registry now serves API model editing, general-setting validation, model construction, and chat selection. Managed catalog identity owns binding updates; alternates remain a plain user list. YAML dictionaries are confined to the catalog upgrade boundary and validated into typed settings before runtime use. Existing provider authentication, capability gates, script/delegate errors, and same-model retry policy remain authoritative.

Lifecycle finding (high): concurrent cold settings loads could race while replacing the automatic-upgrade temporary file. A six-reader test reproduced `FileNotFoundError`. Settings loading, saving, and cache invalidation now share a reentrant lock; the probe passes with one complete upgrade and one backup. All 26 focused checks and the complete Python quality gate pass after this correction.

Collision review: Advanced YAML edits and settings repair validate the resulting registry before writing, while on-disk invalid names remain available for administration and cannot resolve arbitrarily. Custom duplication and repair preservation are covered by the alias scenario. Catalog upgrades validate the complete resulting namespace and roll back on collisions.

Operator review: Chromium passed managed binding protection, alternate-list editing, custom-copy independence, keyboard focus, theme-dependent colors, and desktop/mobile overflow checks in all six themes. Activity checks cover model edits, chat fallback, and managed update success/blocking. Documentation review is complete: the approved architecture paragraph and shortened authoring addition are applied; installation changes were rejected and release notes are deferred to a separate drafting effort. A separate ADR is unnecessary because the existing settings-backed binding decision already owns this contract.

Further review centralized managed/custom rename preservation in `core/settings/model_registry.py`, and protected model drafts while saves are pending. The alias scenario inspects actual managed-update success and blocking records; distinct blocked configurations have distinct warning identities. Chat fallback covers both absent and invalid stored names, while provider and capability failures retain their existing handling. Raw YAML recovery validates the proposed registry independently of the broken current file; a malformed alternate-list fixture first reproduced the failed repair and now passes.

The first complete run passed 136 of 137 scenarios and exposed a timing assumption in `execution_task_runner`: terminal status can precede its failure hook. The scenario now waits for the hook's completion signal before preserving the exact original-exception assertion; production task-runner behavior is unchanged. Four focused integration scenarios passed after the fixes (run `20261022_223910_339035`), along with 26 focused unit/module checks and the complete Python quality gate. The final complete run subsequently passed all 137 scenarios.

The chat path audit also covered deferred inline-review resumes. A missing-alias resume fixture reproduced the separate preparation path failing to use the default; that preparation path now calls the same chat resolver before model-dependent setup. The fallback, deferred-review task skeleton, and deferred-review interruption scenarios passed (run `20261022_224336_554188`), with production quality gates passing. This changes reference resolution only and preserves deferred tool-result handling and existing recovery permissions.

Final validation: all 137 deterministic `integration/core` scenarios passed against the completed implementation (run `20261022_224357_963304`, report `validation/runs/reports/20261022_224357_963304.md`). All 26 focused unit/module checks passed, including the concurrent cold-load regression; model-editor browser behavior checks and native Chromium checks passed across six themes at desktop/mobile sizes. The complete production Ruff, Black, and MyPy gates passed, CSS compilation passed, `uv lock --check` passed, and project/lockfile/release versions match at `0.9.1`. No live-model or experimental scenarios were run. Completed implementation slices and hardening corrections are committed locally. The approved documentation additions are applied as recorded below. Installation changes were rejected and release notes are deferred; no documentation approval remains pending for this implementation.

### Model alias labels

The model editor labels the primary name as Model Alias. Display names are removed from model settings, API payloads, editing, and picker labels; the primary alias identifies each model throughout the UI. Existing persisted display-name fields are ignored when loading model entries and omitted on subsequent serialization. Provider and connection display names are outside this change.

Validation: the persisted legacy display-name regression failed before removal and passes afterward. All 27 focused registry, catalog, configuration, and chat-selector checks pass. The model-alias integration scenario passes (run `20261022_230030_288637`), including API omission and primary-alias setting labels. Full production Ruff, Black, and MyPy checks pass.

## Managed catalog audit — 2026-10-22

This read-only audit compares the packaged template, repository-local `system/settings.yaml`, and current official provider documentation. It does not establish the state of a separately deployed installation or account-level model access. The initial audit changed no runtime settings or model bindings. The subsequent approved latest-alias preference is recorded below.

The packaged catalog contains 16 managed identities: Google `gemini`, `gemini-flash`, `gemini-flash-lite`; Anthropic `sonnet`, `opus`, `haiku`; OpenAI `astra`, `gpt`, `gpt-mini`, `gpt-nano`; Grok `grok`, `grok-fast`; Mistral `mistral-large`, `mistral-small`, `mistral-medium`; TypeSafe `jev`. Fifteen declare text/vision, while Jev declares decision capability.

The local settings contain 12 managed entries matching the packaged catalog and seven custom entries: `sonnet`, `opus`, `haiku`, `grok`, `grok-mini`, `deepseek`, and `glm`. The first four occupy packaged canonical names but explicitly carry `managed_id: null`; catalog upgrades correctly preserve their bindings and ownership. A pure catalog-upgrade dry run returns no updates, renames, missing canonical entries, or issues. Registry validation reports no collisions. Both default and compaction-author selections resolve to `gpt-mini`.

### Candidate catalog refresh

| Current primary alias | Packaged binding | Audit outcome |
| --- | --- | --- |
| `gemini` | `gemini-3.1-pro-preview` | Still listed; retain pending a reason to change the Pro selection. |
| `gemini-flash` | `gemini-flash-latest` | Rolling provider alias; explicit current target is `gemini-3.8-flash`. |
| `gemini-flash-lite` | `gemini-flash-lite-latest` | Rolling provider alias; explicit current target is `gemini-3.5-flash-lite`. |
| `sonnet` | `claude-sonnet-5` | Candidate `claude-sonnet-5-5`. |
| `opus` | `claude-opus-5` | Candidate `claude-opus-5-5`. |
| `haiku` | `claude-haiku-4-5` | Candidate `claude-haiku-5-5`. |
| `astra` | `gpt-6-astra` | Current; retain. |
| `gpt` | `gpt-5.6-sol` | Candidate primary alias `sol`, target `gpt-6.1-sol`, preserving `gpt` as alternate. |
| `gpt-mini` | `gpt-5.6-terra` | Candidate primary alias `terra`, preserving `gpt-mini`; retain binding because official model documentation remains available and no retirement was established. |
| `gpt-nano` | `gpt-5.6-luna` | Candidate primary alias `luna`, target `gpt-6-luna`, preserving `gpt-nano` as alternate. |
| `grok` | `grok-4.6-latest` | Candidate `grok-4.7`. |
| `grok-fast` | `grok-4.3-latest` | Current public catalog supplies no distinct fast successor; verify availability and intent before retargeting or consolidating. |
| `mistral-large` | `mistral-large-latest` | Exact rolling target unverified; Large 4 is public preview, so do not assume an automatic preview upgrade is appropriate. |
| `mistral-medium` | `mistral-medium-latest` | Exact rolling target unverified; Medium 3.5 is the current documented GA candidate. |
| `mistral-small` | `mistral-small-latest` | Exact rolling target unverified; Small 4 is the current documented GA candidate. |
| `jev` | `jev-latest` | Retain decision-model role; verify current backing version separately. |

Anthropic also lists `claude-fable-5-1`; adding a `fable` entry is a catalog expansion rather than an upgrade to an existing identity. Missing managed entries are seeded through settings repair, not automatically inserted during ordinary catalog loading. The initial audit proposed preserving all explicit unmanaged entries; the later approved family-adoption rule below supersedes that proposal for standard aliases. Models under custom aliases remain independent.

Sources: [OpenAI model catalog](https://developers.openai.com/api/docs/models), [Terra model](https://developers.openai.com/api/docs/models/gpt-5.6-terra), [OpenAI deprecations](https://developers.openai.com/api/docs/deprecations), [Claude models](https://platform.claude.com/docs/en/models/overview), [Gemini models and version naming](https://ai.google.dev/gemini-api/docs/models), [Grok models](https://docs.x.ai/developers/models), [Mistral models](https://docs.mistral.ai/models), [Mistral Large 4 preview](https://docs.mistral.ai/models/mistral-large-4-0), and [TypeSafe documentation](https://docs.typesafe.ai/introduction).

Potential next implementation slices: (1) canonical OpenAI alias cleanup with tests for preserved old references, stable managed IDs, collisions, and custom preservation; (2) agreed binding refreshes with deterministic catalog-upgrade and provider-adapter checks; (3) any separately agreed catalog additions with fresh-install and settings-repair checks. The user subsequently selected provider-supported latest aliases wherever available. Provider availability was researched through documentation; no paid inference probes were performed.

### Accepted latest-alias policy

Use documented provider-supported rolling latest aliases wherever available; do not synthesize unsupported suffixes. Applied the Google Pro catalog binding change from `gemini-3.1-pro-preview` to `gemini-pro-latest`, documented in [Google release notes](https://ai.google.dev/gemini-api/docs/changelog). The subsequent approved refresh below retains Google Flash/Flash-Lite and Mistral latest aliases, updates Grok, and removes the shipped Jev entry. Anthropic explicitly documents current dateless IDs as pinned snapshots in [model ID/version guidance](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions); its current releases require explicit IDs. The OpenAI catalog likewise supplies explicit Astra/Sol/Luna IDs rather than verified per-family latest aliases. Do not substitute a chat-specific latest model for a named family.

Read-only upgrade checks after the Google change show exactly one pending local managed update (`gemini`), preservation of all custom entries and the stable managed identity, valid namespace, conservative legacy adoption against the unchanged frozen baseline, and idempotence on repeat application. All 16 focused catalog and registry checks pass. The integration runner loaded repository settings before initializing its isolated scenario root, which triggered the authorized automatic Google binding update in local runtime settings. Its normal migration backup preserves the prior configuration; the configured default remains `gpt-mini`. The isolated model-alias scenario subsequently passed (run `20261022_230937_713369`).

## Applied catalog refresh and ownership reconciliation

The approved refresh ships 15 managed models. Removed the packaged Jev model and added explicit retirement for its managed identity and exact legacy shipped binding; automatic retirement persists a rollback backup and emits the removed aliases in the managed-update System Activity event. Custom decision aliases and the TypeSafe provider plumbing remain available. Decision-only validation now configures explicit custom fixtures rather than depending on a shipped Jev model.

Applied bindings: Sonnet `claude-sonnet-5-5`, Opus `claude-opus-5-5`, Haiku `claude-haiku-5-5`, Sol `gpt-6.1-sol`, Luna `gpt-6-luna`, Grok `grok-4.7-latest`, and Gemini Pro `gemini-pro-latest`. Retained Astra, Terra, Grok Fast, Google Flash/Flash-Lite, and the Mistral latest aliases. The Grok latest suffix follows the provider's documented model-alias naming convention. Renamed OpenAI primary aliases to `sol`, `terra`, and `luna`, keeping stable catalog identities and accepting `gpt`, `gpt-mini`, and `gpt-nano` as alternates on fresh installs and upgrades. User-removed alternates are not regenerated by subsequent managed updates.

Ownership reconciliation: standard built-in primary and historical aliases are adopted when the provider and model family match, including entries with `managed_id: null`. API model identifiers are matched against explicit provider-family patterns, independent of generation and prior capability flags. Entries under custom aliases remain independent. Different-provider or different-family bindings occupying standard aliases are preserved and generate a System warning explaining restoration or custom renaming; an unresolved conflict blocks the catalog update atomically. The agreed rule replaces exact shipped-baseline adoption. The API save response reflects the post-reload canonical alias, binding, and ownership.

Local verification: the running development API reports `sonnet`, `opus`, and `haiku` with their `builtin-*` managed identities and current 5.5 bindings. A repeated local catalog-upgrade dry run reports no pending updates or issues. This reconciliation also adopted the local standard Grok binding while preserving `grok-mini`, `deepseek`, and `glm` under their custom aliases.

Dependency compatibility: Pydantic AI 2.49.0 failed a regression asserting adaptive thinking for the newly shipped Haiku 5.5 binding. Upgraded the pinned Pydantic AI family to 2.55.0, released October 9 with Haiku 5.5 support, rather than introducing a local SDK profile shim. The corresponding regression now passes. Related SDKs resolve to Anthropic 1.15.0 and OpenAI 3.31.0, with candidate resolution limited to releases through October 15 rather than taking packages released during this work. [Upstream release](https://github.com/pydantic/pydantic-ai/releases/tag/v2.55.0).

Validation so far: adoption and conflict tests failed before implementation and pass afterward; focused checks cover preserved custom aliases, retirement-only persistence/backups, idempotence, family matching, and SDK support. The alias and decision-model integration scenarios pass (run `20261022_232203_773105`), including persisted unmanaged-to-managed adoption, actual System warning delivery for conflicting families, API restoration, retirement Activity, hidden historical aliases, and custom decision-model preservation. Production Ruff, Black, and MyPy gates pass. The earlier full-suite run was intentionally stopped after the ownership rule changed. The final deterministic run passed all 137 `integration/core` scenarios (run `20261022_232306_638458`, report `validation/runs/reports/20261022_232306_638458.md`). All 34 focused registry, catalog, configuration, and selector checks pass, including legacy naming formats and unmanaged historical OpenAI aliases. Full production Ruff, Black, and MyPy gates, lock consistency, and diff whitespace checks pass. Validation used isolated bootstrap roots; no live inference probes were run.

Local checkpoint commits: `f8b1626` simplifies model alias labels and icon actions, `ab6257c` applies the approved architecture and authoring text, `0a9d943` upgrades Pydantic AI and the related SDK lock entries, and the subsequent catalog commit records the binding refresh, Jev retirement, and standard-family ownership rule. Nothing was pushed. The unrelated user edit to `docs/agent-guides/planning.md` remains unstaged and untouched.

## Mobile model-card follow-up

Keep the model alias and action buttons in a non-wrapping header with a flexible, wrapping alias and fixed-size actions. Render provider error messages in their own full-width row below the header. Constrain metadata fields to the card width. Show alternate aliases in a separate full-width card section, with an explicit empty state when none are configured. Continue using existing theme tokens and icon-button styles.

Validation: a Chromium geometry probe first reproduced buttons dropping below the header at 320px. The corrected layout passes across all six themes at 320px, 390px, and 1200px, including top-aligned actions, full-width status, visible aliases, and no horizontal overflow. Inspected the mobile screenshot. All four existing model-editor checks pass, CSS compilation passes, and the diff has no whitespace errors. The approved architecture sentence is applied; no documentation approval remains pending for this effort.

## Final documentation review step

Completed: the user approved the architecture paragraph and only the first sentence of the authoring draft. Both approved additions are applied. The installation draft was rejected because these details do not belong in installation guidance; relevant user-facing details can be considered when release notes are drafted separately. The release-note draft was rejected for this effort, with release-note drafting deferred. No installation or release-note changes were made. A separate ADR is unnecessary because the existing settings-backed binding decision already owns this contract.

### Approved architecture addition

Applied to `docs/development/architecture.md` after the built-in model/tool binding paragraph:

Model names resolve through one normalized registry of canonical and alternate aliases. Managed catalog entries have stable identities and centrally controlled names and provider bindings; custom entries retain user ownership. A managed canonical rename preserves the previous name in the same editable alternate list. Catalog updates and configuration edits reject name collisions before publishing changes. An unresolved chat selection uses the configured default; an unresolved default produces a System configuration error. Provider and capability failures retain their existing handling.

### Approved authoring addition

Applied to `docs/use/authoring.md` after the paragraph about keeping model aliases near the top of scripts:

Model arguments accept canonical aliases and alternate aliases configured under System → Models.

Final review outcome: only the explicitly approved documentation text was applied. Installation remains unchanged, and release notes will be drafted separately. The user approved the further architecture sentence covering standard-alias family adoption and conflict warnings, and it is applied: “Entries under a standard alias become managed when their provider and model family match; conflicting bindings remain unchanged and raise a System warning.”
