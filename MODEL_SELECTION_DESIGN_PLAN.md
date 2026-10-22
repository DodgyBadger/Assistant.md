# Durable model aliases

## Status and scope

Implementation and hardening are complete on `dev/durable-model-aliases`. Documentation drafts remain pending explicit user approval. Keep script-facing model names usable across provider rebranding and managed catalog updates through a canonical alias and one plain editable alternate list. Preserve explicit model selection, existing defaults, and user-defined models.

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
| Display name | Friendly UI label independent of lookup identity; consider an additive `display_name` field. |

Automatically preserved aliases are ordinary list entries. Users may add, edit, or remove them under the same collision rules. Do not introduce origin fields, protected historical names, separate permissions, or alias-to-alias chains. Alternates resolve directly to the same canonical model and inherit its binding and capabilities. Scripts and APIs accept them; ordinary pickers show one model choice rather than a choice for each alternate.

On an automatic canonical rename from A to B, atomically make B canonical and append A to the current alternate list if absent after normalization. Preserve the other entries currently in that list. If B later becomes C, append B and keep A only if it remains in the user-edited list. If the canonical name is unchanged, leave alternates unchanged; do not regenerate names the user removed. Promoting an existing same-model alternate to canonical removes that alternate from the list and preserves the previous canonical as an alternate. Case-only or whitespace-only changes must not create duplicate lookup identities.

## Managed updates and custom models

Automatic managed updates are accepted, including canonical changes that preserve the previous name as an alternate. Use a stable catalog identity independent of canonical spelling to recognize the same managed entry through renames. Validate binding updates and names together; repeated upgrades must be idempotent.

Custom model canonical names remain editable, and custom bindings must survive repair and catalog refresh. Recommend preserving the prior name as an alternate on custom renames. Never infer that different user entries represent the same model merely because they share a provider model identifier.

Accepted: managed canonical aliases and provider/model bindings are centrally controlled. Users can duplicate a managed model as custom to edit its binding independently; catalog updates must not retarget the custom copy. Alternate aliases remain editable on managed models. For existing installations, use verified shipped baselines or other reliable evidence before adopting an entry as managed. Preserve customized or ambiguous entries rather than overwriting them. A packaged alias colliding with a user entry must not silently replace it.

## Collision validation

Use one consistent lookup normalization for canonical and alternate names. Inventory current names before imposing stricter syntax; trimmed lowercase identifiers are a candidate rule, with human-friendly display names kept separate. Reserve blank references, `none`, and validation-only identifiers as appropriate to current execution contracts.

Reject collisions between different models, repeated alternates, and alternates equal to their own canonical name. Allow promotion of an alternate belonging to the same model through the atomic rename rule. Compile and validate changes before publishing them; collisions must not partially write settings or update caches. A failed managed update retains the working configuration and raises an actionable warning.

Invalid persisted entries must remain diagnosable through administration. Do not choose an arbitrary winner for ambiguous names. Resolve canonical and alternate references through the same capability checks; adding an alternate must not make an otherwise invalid binding eligible.

## Chat resolution and default behavior

New chats use the configured default model. Existing chats resolve their selected name through canonical and alternate lookup. If that alias no longer resolves, use the configured default. If the default also cannot resolve, raise the configuration error in the System tab and fail the operation. Do not search for another model.

Apply fallback only when the chat's model reference no longer resolves. Provider outages, authentication failures, rate limits, capability failures, and inference errors retain their existing handling. This does not introduce additional fallback for explicit script or delegate requests, change omitted-model inheritance, or require session binding snapshots.

Trace the current chat creation, loading, and request paths before implementation so the rule applies consistently to restored and active sessions. Preserve existing model-dependent preparation and retry behavior. Make the effective model apparent through the existing chat presentation and activity records where those already expose model identity.

## Configuration UI and persistence

Managed canonical aliases are read-only in the normal editor. Expose alternates as one editable string list; keep custom canonical names editable. Show friendly display names where supported, with provider and canonical alias available for disambiguation. Identical display labels may be disambiguated; lookup names must be unique. Copy existing theme-aware styling for every control, label, button, and warning.

When a saved alternate resolves, show the corresponding canonical model without creating duplicate options. Do not rewrite saved references merely for presentation. Preserve exact invalid saved settings and show persistent issues through the existing settings status surfaces. An unresolved default must be reported in the System tab.

Make settings schema changes additive and upgrades idempotent. Preserve user bindings, existing references, authored scripts, capabilities, and historical records. Add display labels and alternate lists without requiring blanket reference rewrites. Do not introduce new execution snapshots or database migrations without a demonstrated requirement from the alias changes.

## Initial implementation inspection

At initial inspection, the shipped settings template marked most generative models user editable, and `ModelConfig` had no stable managed identity or alternate-name field. Adoption therefore cannot use the editability flag as evidence of an untouched binding. Compare against known shipped binding and capability baselines; retain customized or ambiguous entries as custom. Settings repair currently fills missing entries without updating existing model bindings and prunes removed non-editable entries, so the managed rename path must run before generic merging/pruning to preserve aliases and current user-edited alternate lists.

The initial model editor performed a rename as a create followed by a separate delete. The implemented atomic operation preserves the previous canonical as an alternate and rejects collisions before writing. Managed editing must permit alternate-list edits while rejecting changes to the canonical name and binding. Duplicate-as-custom must create a fresh custom entry without copying managed identity or claiming the source model's alternate names.

The initial general settings editor treated model choices as literal canonical dictionary keys. Resolution and validation now accept valid alternates used by `default_model` without creating duplicate picker options. Both ordinary chat and deferred-review preparation use the shared chat fallback resolver, preserving capability and provider error handling.

## Testable implementation slices

Each slice starts with failing behavior assertions, ends with directly relevant passing checks and a local checkpoint commit, and preserves the previous slice's contracts. No runtime roots outside isolated validation fixtures may be changed.

### Slice 1 Shared alias registry

Completed: the scenario first failed because alternate names were absent from the API response. After implementation, `model_alias_resolution`, `decision_model_configuration`, and `settings_editors` passed (run `20261022_220524_585256`); eight registry unit checks passed, and the complete production Ruff, Black, and MyPy gates passed. Binding/capability/provider caches publish as one snapshot; invalid entries remain diagnosable without resolving ambiguous names.

Deliver additive alternate-name and display-name fields, one normalized registry shared by binding and capability lookup, collision diagnostics, and atomic cache refresh. Persisted invalid names remain visible to administration; ambiguous names never resolve to an arbitrary model. Reserve `none`, and allow the canonical validation-only `test` fixture while disallowing alternate names that hijack it.

Validation: focused registry assertions plus `integration/core/model_alias_resolution` demonstrate case/whitespace normalization, alternate/canonical binding and capability equivalence, hidden alternates in model listings, collision rejection without changing saved settings, and diagnostics for invalid persisted data. Existing decision-model and settings-editor scenarios stay green.

### Slice 2 Managed catalog updates

Completed: six catalog/storage checks cover renames, alternate promotion/removal, idempotence, custom preservation, verified legacy adoption, atomic collision rollback, persisted upgrades, and rollback backups. The four relevant integration scenarios passed (run `20261022_220822_830310`). Fresh templates now carry stable managed IDs and centrally controlled bindings; known managed updates persist automatically with a backup, while customized legacy entries remain custom.

Deliver stable managed identity, an explicit baseline for adopting untouched shipped entries, atomic managed rename/update behavior, and preservation of custom bindings. Update known managed entries automatically when settings load; preserve aliases and current alternate lists in the persisted settings. A collision retains working settings and raises a System configuration warning. Repair must use the same update helper and must not prune retained renamed managed entries or overwrite customized entries.

Validation: pure upgrade assertions and the alias scenario cover untouched adoption, customized/ambiguous preservation, repeated renames, same-model alternate promotion, idempotence, deleted alternates remaining deleted, binding updates, collisions without partial writes, repair, and fresh-template seeding.

### Slice 3 Model editing and settings integration

Completed: the managed-alternate edit scenario first failed on the prior read-only rejection, then passed with atomic custom rename, managed binding protection, editable alternate removal, and alternate-backed default validation (run `20261022_221058_656712`). Twenty-two focused Python/module checks passed, including three browser model-editor behavior checks; production quality gates passed and CSS compilation completed. Managed duplication starts a separate custom draft without managed identity or inherited alternate names.

Deliver API and theme-aware UI for one alternate list and independent display labels, centrally controlled managed bindings, atomic custom rename preserving its prior alias, and Duplicate as custom without managed identity or copied alternate names. Model pickers show canonical entries only; valid saved alternates remain accepted and display the canonical option. Use the same collision checks before any write, including Advanced YAML edits that alter the registry.

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

Operator review: Chromium passed managed binding protection, alternate-list editing, custom-copy independence, keyboard focus, theme-dependent colors, and desktop/mobile overflow checks in all six themes. Activity checks cover model edits, chat fallback, and managed update success/blocking. Documentation drafts for architecture, authoring, and installation were presented for explicit approval; they remain unapplied until approved. A separate ADR is unnecessary because the existing settings-backed binding decision already owns this contract.

Further review centralized managed/custom rename preservation in `core/settings/model_registry.py`, disambiguated repeated display labels with canonical names, and protected model drafts while saves are pending. The alias scenario inspects actual managed-update success and blocking records; distinct blocked configurations have distinct warning identities. Chat fallback covers both absent and invalid stored names, while provider and capability failures retain their existing handling. Raw YAML recovery validates the proposed registry independently of the broken current file; a malformed alternate-list fixture first reproduced the failed repair and now passes.

The first complete run passed 136 of 137 scenarios and exposed a timing assumption in `execution_task_runner`: terminal status can precede its failure hook. The scenario now waits for the hook's completion signal before preserving the exact original-exception assertion; production task-runner behavior is unchanged. Four focused integration scenarios passed after the fixes (run `20261022_223910_339035`), along with 26 focused unit/module checks and the complete Python quality gate. The final complete run subsequently passed all 137 scenarios.

The chat path audit also covered deferred inline-review resumes. A missing-alias resume fixture reproduced the separate preparation path failing to use the default; that preparation path now calls the same chat resolver before model-dependent setup. The fallback, deferred-review task skeleton, and deferred-review interruption scenarios passed (run `20261022_224336_554188`), with production quality gates passing. This changes reference resolution only and preserves deferred tool-result handling and existing recovery permissions.

Final validation: all 137 deterministic `integration/core` scenarios passed against the completed implementation (run `20261022_224357_963304`, report `validation/runs/reports/20261022_224357_963304.md`). All 26 focused unit/module checks passed, including the concurrent cold-load regression; model-editor browser behavior checks and native Chromium checks passed across six themes at desktop/mobile sizes. The complete production Ruff, Black, and MyPy gates passed, CSS compilation passed, `uv lock --check` passed, and project/lockfile/release versions match at `0.9.1`. No live-model or experimental scenarios were run. Completed implementation slices and hardening corrections are committed locally. The only remaining work is explicitly approved documentation publication; none of the drafts below has been applied.

## Final documentation review step

Review the model/settings contract in `docs/development/architecture.md`, authored model references in `docs/use/authoring.md`, and setup guidance in `docs/setup/installation.md`. Determine whether the alias and chat-default contracts warrant an ADR alongside ADRs 0007 and 0009. Present exact proposed documentation text or diffs in chat, obtain explicit user approval under AGENTS.md, and apply only approved changes. No protected documentation changes are included in this planning work.

### Pending architecture addition

Target: `docs/development/architecture.md`, after the built-in model/tool binding paragraph. Draft presented for approval:

Model names resolve through one normalized registry of canonical and alternate aliases. Managed catalog entries have stable identities and centrally controlled names and provider bindings; custom entries retain user ownership. A managed canonical rename preserves the previous name in the same editable alternate list. Catalog updates and configuration edits reject name collisions before publishing changes. An unresolved chat selection uses the configured default; an unresolved default produces a System configuration error. Provider and capability failures retain their existing handling.

### Pending authoring addition

Target: `docs/use/authoring.md`, after the paragraph about keeping model aliases near the top of scripts. Draft presented for approval:

Model arguments accept canonical aliases and alternate aliases configured under System → Models. Alternate aliases resolve to the same binding and capabilities and do not appear as separate picker choices. A missing explicit script or delegate model raises an error; chat’s fallback to the configured default does not apply to those explicit selections.

### Pending installation addition

Target: `docs/setup/installation.md`, after the provider-readiness paragraph. Draft presented for approval:

Choose the default model in System → Application Settings. New chats use that default, and a chat whose selected alias no longer resolves falls back to it. If the default cannot resolve, System reports the configuration error. Under System → Models, managed model names and bindings are centrally controlled; use Duplicate as custom for an independently editable binding. Alternate aliases are an editable comma-separated list, including names preserved automatically during managed renames.

### Pending release note

Target: `RELEASE_NOTES.md`. Draft presented in chat; explicit approval is still required before editing:

Managed model updates preserve previous aliases. Alternate aliases remain editable, managed models can be duplicated as custom, and chats use the configured default when their selected alias cannot resolve.

Final action: obtain explicit approval for each affected documentation draft, then apply only the approved text and record the resulting documentation commit. Unapproved drafts remain pending.
