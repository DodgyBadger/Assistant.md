# Shipped Settings Defaults Review

Status: final baseline implemented and approved documentation applied. Local runtime overrides remain unchanged. Earlier proposals below are decision history; the confirmed baseline and final strategy decision supersede them.

## Read-only evidence

Reviewed repository-local `system/settings.db` through a read-only SQLite connection at configuration revision 73, and compared explicit override values with `core/settings/settings.template.yaml` before this defaults update. The table below records that initial snapshot. Found 15 Custom overrides: 10 differ from the packaged default, and five already match it.

| Setting | Local Custom | Shipped default |
| --- | --- | --- |
| auto_cache_max_tokens | 100000 | 50000 |
| compaction_author_model | terra | none (uses default_model) |
| compaction_high_watermark_tokens | 150000 | 80000 |
| compaction_strategy | session_map | recovery_card |
| default_model | terra | sonnet |
| editable_builtin_providers | openai | none |
| logfire | true | false |
| openai_oauth_enabled | true | false |
| task_snapshot_retention_days | 7 | 30 |
| vault_scan_interval_seconds | 30 | 0 (scheduled scan disabled) |

Matching Custom overrides: ingestion_ocr_endpoint, ingestion_ocr_model, ingestion_pdf_default_strategies, vault_state_enabled, vault_state_excluded_patterns. Clearing these overrides would preserve today's values and resume inheritance; no reset was performed.

## Decisions to discuss

- Consider session_map as the standard strategy for sessions without an existing compaction checkpoint; existing session pinning must remain unchanged.
- Discuss whether periodic external-change reconciliation should be enabled by default. The local 30-second scan is a concrete candidate, with scan cost on large vaults to validate before making it universal.
- Keep token caching/compaction thresholds under discussion as workload-sensitive tuning; local preference does not establish safe context/headroom policy across configured models.
- Treat the default chat model as a separate onboarding/provider preference decision. Following default_model remains a useful compaction-author default, avoiding a second required provider binding.
- Retain opt-in telemetry/OAuth controls, restricted built-in provider editing, and the existing 30-day snapshot retention unless broader product policy changes are explicitly agreed.

## Ingestion destination setting

`ingestion_output_path_pattern` remains live in `core/ingestion/import_service.py`, `core/ingestion/service.py` and API job projection. When destination is omitted, imports fall back to its value (currently Imported/). Explorer imports provide an explicit destination. `core/ingestion/output_paths.py` and the service resolver treat this as a directory, without pattern expansion.

Decide whether installation-wide destination customization is still valuable. Options: retain the behavior under a clearer default-destination name with a versioned key migration, or remove the setting while retaining a centralized Imported/ fallback and per-import destination overrides. Removing the setting must not remove omitted-destination behavior accidentally.

## Implementation slices after agreement

1. Record exact approved default changes and any destination-setting decision; keep personal local overrides unchanged unless separately requested.
2. Update packaged defaults and, if renaming/retiring a setting, add an ordered content migration preserving explicit user intent and existing job destinations. Check ADR 0053 and current shared ingestion boundaries.
3. Validate new/default installations, inherited values, pinned Custom overrides, existing session compaction pinning, and tool/API/Explorer destination precedence using isolated roots and relevant deterministic scenarios. Use broader validation when the approved behavior warrants it.
4. Identify affected architecture, settings/ingestion documentation and release-note updates; present exact drafts, obtain explicit approval, and apply approved text.

## Approved default values

The user approved a compaction high watermark of 200000 tokens (superseding an initial 150000 instruction), an auto-cache threshold of 50000 tokens (superseding 100000; already the shipped value), and a vault scan interval of 60 seconds. The requested default_model baseline is unconfigured/none; a pending clarification distinguishes requiring a System default before new chats from allowing per-chat selections with an explicit setup warning. No compaction strategy change or ingestion-destination change is authorized yet.

Implementation scope: update the approved packaged numeric defaults, preserve all local Custom overrides, and validate inherited versus pinned values. Represent default_model as a deliberate unconfigured state with actionable setup diagnostics; apply the agreed chat-enforcement policy after clarification. Explicit script/model aliases and the separate compaction-author override remain valid contracts. Changes to documentation require approved drafts before editing protected files.

## Live-server baseline draft

The user supplied `system/settings.template.yaml` as the desired baseline and requested a draft before implementation. It contains all 70 currently shipped general settings with no unknown or missing keys. Compare values only: retain current application-owned metadata and managed model/provider/tool catalog rather than copying installation-specific configuration blocks.

Propose the supplied general values, except `default_model: none` and `compaction_author_model: none` so installations select their own model and compaction follows that selection. The supplied 30000 auto-cache threshold and 300-second scan interval differ from the earlier 50000/60-second instructions; explicitly surface both in the draft rather than silently applying either choice. The live file also opts into Logfire, OpenAI OAuth, editable OpenAI provider configuration, and Tavily-backed search/extraction; surface these deployment-dependent defaults for review.

Next slices: (1) confirm the proposed baseline, (2) update packaged values and focused assertions while preserving Custom overrides and the supplied file, (3) validate setup diagnostics and affected compaction/search/scan behavior in isolated roots, (4) present exact affected documentation drafts and apply only approved edits. The running earlier validation pass does not certify the unimplemented live-server draft.

## Confirmed baseline scope

The user approved the draft table through maximum image size: high/low watermarks 200000/40000, cache 30000, scan 300 seconds, snapshot retention 7 days, inline_edit chat mode, unlimited chat model requests, disabled delegate timeout, and 10 MB per image. These supersede earlier 50000/60-second instructions. Compaction strategy remains a discussion exception; keep recovery_card until resolved. Everything below maximum image size remains unchanged: DuckDuckGo search, curl extraction, Logfire off, OpenAI OAuth off, and no editable built-in providers. Model aliases remain unconfigured/follow-default as previously drafted. Runtime overrides and the supplied live-server file remain untouched.

Updated focused assertions cover both the approved values and the preserved exclusions. Final full-profile validation and a local milestone commit follow resolution of the compaction strategy; the earlier full run began before these additional changes and is not final evidence for this expanded slice. Documentation drafts remain pending approval.

## Final compaction strategy decision

The user approved making session_map the shipped default after a week of successful live use. Recovery cards remain selectable. Existing sessions with a checkpoint remain pinned to that checkpoint strategy, and explicit Custom overrides remain pinned. No implicit conversion or fallback is introduced. The default_model setup warning remains the implemented policy; no additional hard restriction on explicit per-chat selection is introduced.

Validate the shipped baseline, settings persistence, both compaction strategies, and explicit strategy upgrades; then run the full deterministic profile against the stable values and all production Python quality gates. Update ADR 0049 and any affected product documentation only after presenting exact drafts and obtaining approval.

## Documentation approval and reset confirmation

The user approved the installation setup instruction, ADR 0049 default-strategy sentence, and short release-note entry; all three are applied. The user also required confirmation before the global Use defaults action. Existing restoreManagedDefaults already calls window.confirm before setting busy state or issuing the POST; cancellation preserves drafts and performs no request. Existing frontend contract coverage verifies cancellation and failed-reset preservation; rerun it with this milestone.

## Completed validation

Full run 20261023_221659_205811 passed 134/139. Four failures were stale scenario assumptions about the approved defaults; the fifth was a cancellation-hook assertion racing terminal task status. After fixture/expectation corrections and explicit hook synchronization, targeted run 20261023_222733_607544 passed all five. Reset/database/repair run 20261023_222851_978452 passed 3/3. Final focused checks after hardening pass 34 Python tests, 25 frontend tests and two browser availability tests; Ruff, Black and MyPy pass. No additional full validation was run after the user's instruction to hold further full runs. See VERSIONED_SETTINGS_HARDENING_PLAN.md for peer findings and corrections.
