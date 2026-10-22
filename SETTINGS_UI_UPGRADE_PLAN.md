# System / Application Settings UI Upgrade

## Goal and scope

Reduce entry errors in System / Application Settings by matching each editor to the setting's value type and supported choices. Keep the existing per-setting edit, save, cancel, filtering, categories, restart notices, and configuration reload behavior.

This effort affects persisted settings and the settings API. Preserve configured runtime data and secrets; use isolated temporary roots for validation. Do not edit repository `data/`, active `system/settings.yaml`, or `system/secrets.yaml` during development checks.

## Findings

`static/js/configuration/settings.js` renders settings from backend editor metadata. `SettingInfo` in `api/models.py` retains the string value/update protocol and adds editor constraints, canonical edit values, and validation issues. `core/settings/config_editor.py` parses and validates updates through `core/settings/editor_schema.py` before persistence. `core/settings/settings.template.yaml` defines product defaults and descriptive metadata. ADR 0007 establishes settings as the registry for model and tool binding.

## Proposed controls

| Value category | Editor | Examples and behavior |
| --- | --- | --- |
| Integer or decimal | Number input | Enforce integer versus decimal, finite values, required input, established bounds, and relevant relationships between settings. Preserve documented meanings of zero. |
| Boolean | On / Off dropdown | Display friendly labels and persist actual boolean values through the existing string update payload. |
| Fixed set of strings | Dropdown | Thinking policy, chat mode, web strategies, compaction policy and strategy, PDF output mode, and URL fetch strategy. Use runtime-supported values as the authority. |
| Configured reference | Dropdown from current configuration | Text-capable model aliases and context scripts; include explicit default-model fallback and no-context choices where supported. Refresh choices when configuration changes. |
| Unordered constrained list | Wrapping toggle pills | Disabled tools from the registered tool names; editable built-in providers from protected provider entries. Allow clearing the list and reject duplicate or unknown values. |
| Ordered constrained list | Ordered selected rows with Add, Remove, Move up, and Move down | PDF and image ingestion strategies. Offer only supported choices, prevent duplicates, preserve priority, and follow the actual runtime contract for empty lists. |
| Unconstrained string list | Individual text rows with Add and Remove | Vault exclusion patterns and OpenRouter provider slugs. Avoid requiring JSON syntax; preserve order and validate string items. Do not introduce a hardcoded external provider catalog. |
| Free text | Text input | Paths, endpoint URLs, and the provider-specific OCR model identifier where the application does not own a closed choice set. |

Use accessible labels and keyboard-operable controls. Show list values in readable form when viewing a setting. Display unsupported existing values explicitly so merely entering edit mode never silently changes or discards persisted configuration; require correction before saving a constrained setting.

## Invalid settings and persistent status warnings

Apply validity checks to every application setting, not only model references. Use the same backend-owned rules for validating current configured values, describing editor constraints, and accepting updates. Detect wrong types, malformed or out-of-range numbers, non-finite numbers, unsupported choices, unresolved references, invalid list entries, prohibited duplicates, and invalid relationships between settings. Attribute cross-setting failures to the relevant keys. Do not infer unsupported constraints for free-form values.

Expose structured current-value validation issues with setting keys and actionable reasons in the settings API. Invalid existing values must not prevent the settings collection from loading. Return their values and issues so the UI can display them and allow correction. Distinguish an invalid configured value from a failure to load or validate configuration; do not report unavailable validation as a healthy state.

Raise a persistent warning in the settings tab's status area whenever any current setting is invalid. List the affected settings and reasons, including saved values where useful. Evaluate all settings independently of the current filter, and recompute after loading settings, relevant configuration changes, and successful saves. Keep the warning visible independently of transient loading, editing, success, or error feedback. Clear individual issues only when resolved and clear the warning when all settings are valid. Missing model aliases are one instance of this general behavior.

Preserve invalid saved values until explicitly corrected. Opening or cancelling an editor, filtering, refreshing, and editing unrelated settings must not rewrite them. Existing unrelated invalid values must not block a valid update; reject an update that is itself invalid or creates or worsens a directly related cross-setting violation. Revalidate current configured values after successful updates. Keep draft-input errors separate from warnings about persisted configuration.

## Model alias changes and unresolved references

Model choices must be populated from the current backend model registry, with validation against that same registry at save time. Keep editor rendering independent of how aliases are defined so a future alias redesign can change the choice source without replacing the controls.

If an alias still exists but its provider or underlying model identifier changes, preserve the saved alias reference. If a saved alias is removed or renamed, preserve its exact persisted value and display it as an unresolved reference in both view and edit states. In the dropdown, show a selected placeholder such as `old-alias (unavailable)` alongside valid replacement choices. The placeholder represents existing state only and cannot be selected as a new value or saved as a valid choice. Do not select the first available model, infer a replacement from a similar name, or silently use the default model.

Opening, cancelling, filtering, refreshing, or saving an unrelated setting must not rewrite an unresolved model reference. Users can repair that setting by selecting a currently registered alias; restore ordinary display automatically if the alias becomes available again. Preserve the intentional `compaction_author_model` default-model fallback as a separate supported choice, with a friendly label that distinguishes it from a missing alias.

For unresolved model references, the general persistent status warning must name each affected setting and saved alias and tell the user to select an available model. Resolve that issue when the reference is corrected or its alias is restored. Treat supported fallback values as valid rather than warning about them. The status-area warning is a minimum requirement, in addition to marking the affected control.

If the model registry changes while the editor is open, reject a newly unavailable selection without writing it, retain the draft, and refresh available choices with an actionable validation message. Refresh choices when opening an editor and after relevant configuration changes. Existing unresolved references must not prevent loading the settings page or changing unrelated settings.

The current runtime rejects unknown model aliases. Retain that explicit failure behavior for unresolved references; this UI effort does not add automatic model migration or change model resolution policy. Future intentional alias renames may provide an explicit migration mapping as a separate change.

## Contract and implementation approach

1. Inventory every template setting against its runtime consumer to establish supported choices, numeric bounds, optional values, list ordering, and empty-list semantics. Confirm context-script scope before deciding how to populate its choices.
2. Define a shared backend settings editor specification with types, choices, numeric constraints, and list semantics. Keep product-owned constraints authoritative even when persisted values have the wrong type. Reuse runtime registries and constants where practical; avoid deriving constraints from description prose or copying separate rule tables into JavaScript.
3. Extend `SettingInfo` with additive editor metadata while retaining the existing string `value` and string update payload for existing clients. Include equivalent metadata in list and successful update responses. Keep product-owned UI metadata separate from user-editable persisted values.
4. Enforce the specification in the general-setting update path before persistence. Reject malformed numbers, non-finite values, unsupported choices, invalid list item types, duplicates where prohibited, and invalid cross-setting combinations. Preserve intentional supported aliases and current reload behavior.
5. Render the appropriate editor from the API metadata, including list editing and serialization to the existing payload. Validate locally before sending a request and retain the draft on validation or API failure. Keep server validation authoritative.
6. Rebuild CSS with `npm run build:css` if editor markup introduces utility classes that require regeneration.

Raw YAML editing is a separate advanced configuration surface. Inventory its existing validation and explicitly assess whether shared rules can apply without expanding this effort into unrelated configuration lifecycle changes.

## Validation-first delivery

Extend `validation/scenarios/integration/core/api_endpoints.py` or add a focused deterministic settings-editor scenario before implementing the changed API contract. Assert editor metadata and choice sources, valid saves, persisted types and ordered arrays, invalid input rejection without persistence changes, and preserved restart/reload behavior. Cover booleans, enums, dynamic references, integers, decimals, constrained lists, and free-form lists. Include existing invalid configured values and registry changes.

For model references, cover removed and renamed aliases, an alias whose underlying model mapping changes, restored aliases, an empty model registry, and an alias removed between editor load and save. Assert exact preservation of unresolved saved values during unrelated edits, rejection of unavailable replacements, explicit replacement with a valid alias, and continued support for the compaction-author default-model fallback. Frontend checks must verify that the unavailable placeholder remains selected until the user explicitly chooses a replacement.

Verify that unresolved model references produce a persistent settings-status warning naming the affected keys and aliases, that filtering and transient action feedback do not hide it, and that correcting or restoring the final unresolved reference clears it. Assert warning state and referenced values rather than exact editorial copy.

Extend invalid-current-value coverage beyond aliases to wrong types, numeric constraints, fixed choices, list contents, and cross-setting relationships. Assert that issues are returned without breaking the settings collection, valid unrelated updates preserve invalid saved values, warnings remain until every issue is resolved, and draft errors do not masquerade as persisted configuration issues.

Use focused JavaScript behavior checks for draft handling, control serialization, validation blocking, and list ordering; avoid tests coupled to CSS or editorial copy. Run relevant existing settings scenarios such as `system_template_seed_refresh`, `disabled_tools_registry`, and `decision_model_configuration` where their contracts are affected. Use isolated runtime roots throughout.

No new validation events are currently proposed: API responses and persisted settings provide the required deterministic artifacts. Reassess only if implementation introduces a decision boundary that those artifacts cannot adequately verify.

During development run fast targeted checks. During stable-branch hardening run `python validation/run_validation.py run integration/core` once and complete the production Python quality gate from the coding standards guide if Python changes are made.

## Documentation boundary

No changes to `docs/`, `README.md`, or `RELEASE_NOTES.md` are included in this planning step. Any needed product documentation changes remain pending until the affected files and exact proposed text or diff have explicit user approval under AGENTS.md.

## Implementation and hardening status

The backend now owns editor metadata and validation in `core/settings/editor_schema.py`; the general-settings API returns editor specifications, canonical edit values, and structured current-value issues. The UI renders typed controls, validates drafts, preserves unresolved values, and keeps persistent warnings separate from transient feedback. Choices refresh on editor entry and configuration mutations. Request identities prevent late responses from restoring stale metadata or reopening cancelled editors.

Model choices include only registered text-capable aliases. The validation harness explicitly registers its deterministic test model. Invalid-runtime-readiness fixtures now use advanced YAML editing after asserting rejection by the structured settings API. The low/high watermark relationship applies to session-map compaction; recovery-card compaction does not use the low watermark. Switching into session-map mode validates that dependency before persistence. The no-context-script and compaction-author fallback choices retain supported optional values.

Hardening review covered the API-to-persistence contract, product-owned types despite malformed saved values, current tool-registry fallback, read-only inspection and explicit repair, stale requests, cancellation, keyboard-operable controls, and mobile width. Existing configuration mutation and API rejection logging remains in use; no new background operation or warning telemetry was introduced. Advanced YAML retains its existing mapping validation and acts as the deliberate surface for diagnosing invalid persisted configurations.

Validation complete: the final deterministic `integration/core` profile passed all 135 scenarios (run `20261022_182126_680720`). The 12 affected scenarios also passed in focused revalidation. Seven configuration-module pytest checks passed, including a Node harness with nine settings-editor behavior checks. Headless Chromium smoke checks passed with the actual stylesheet and icons at desktop and mobile widths. The CSS build passed, and the complete production Ruff, Black, and MyPy gates passed with zero findings. No live-service or experimental scenarios were run. Runtime validation used isolated roots and left configured persistent data and secrets untouched.

## Theme styling follow-up

Reuse the existing `text-txt-primary`, `text-txt-secondary`, `state-warning`, shared input utilities, `ui-button-secondary`, and icon-button styles throughout the settings controls. Setting cards establish a theme-aware inherited text color for list values and unavailable entries; legends use the same label styles as the model editor. Constrained-list checkboxes reuse the configuration activity-log panel's `tool-checkbox-wrapper` and `tool-checkbox-name` styles, including themed accents, selected backgrounds, and hover behavior. No new color palette or control style is introduced. Verified computed text, field, option, button, warning, and checkbox colors, including hover and selected states, across light, dark, ocean, sunset, lavender, and forest themes in headless Chromium. All seven configuration-module pytest checks passed, including the nine editor checks; the CSS build and JavaScript syntax check passed. This follow-up changes presentation only, so the prior 135-scenario deterministic result remains applicable.

## Compact constrained-list follow-up

Render unordered constrained lists, including `editable_builtin_providers` and `disabled_tools`, as adjacent pills that wrap to the available width. Reuse the existing tool-checkbox colors, checked backgrounds, and hover behavior with a scoped rounded-pill variant. Keep native checkbox semantics, keyboard Space toggling, and visible focus, with the checkbox visually hidden. Selected pills use the themed accent background, border, and glow; there is no check mark or reserved icon space. Preserve draft serialization, invalid-entry repair, save/cancel behavior, ordered priority controls, and free-form list rows. Headless Chromium checks passed for side-by-side layout, mobile wrapping, pointer and keyboard toggles, visible focus, draft serialization, and selected styling across all six themes. The glow-only browser checks also passed: unselected pills have no glow or reserved icon space, with keyboard and pointer toggles working across all six themes. All seven configuration-module pytest checks, the CSS build, and JavaScript syntax checks passed. This presentation-only follow-up does not require another full backend regression run.

## Final documentation review step

The following proposed addition to `docs/setup/installation.md`, after the provider setup instructions, was presented in chat and submitted for explicit approval:

> Under **System → Application Settings**, numeric fields validate their type and range, supported choices use dropdowns, and booleans appear as On/Off. Lists use toggle pills or Add/Remove controls; ingestion strategy lists also provide priority ordering. A persistent warning identifies invalid saved settings, including model aliases that are no longer registered. Invalid values remain intact until corrected and do not block valid edits to unrelated settings.

Implementation and validation are complete; documentation approval remains pending. Leave that addition pending until approved, then apply the exact approved text. No changes to protected documentation are authorized by the general implementation instruction. Preserve the separate working-tree edit to `docs/agent-guides/planning.md`.

After approval and application, review documentation for agreement with the implemented contract. If approval is not provided, report documentation as pending at handoff. Next phase: Commit and Review Prep when requested.
