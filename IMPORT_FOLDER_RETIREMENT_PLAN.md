# Import Folder Provisioning and Default Retirement

Branch: `dev/tool-upgrades`. Status: implementation and validation complete. The user authorized commit and push.

## Baseline inspection

- Startup vault provisioning in `core/authoring/template_discovery.py:ensure_vault_directories` creates `AssistantMD/Authoring` and `AssistantMD/Skills` and seeds skills. It does not create `Imports` or `Imported`.
- Chat export creates `AssistantMD/Chat_Sessions` on demand. Vault identity uses `AssistantMD/vault.yaml`; it is a file, not a provisioned subfolder.
- Ingestion processing and the legacy import-folder scan explicitly create `AssistantMD/Import` (singular). Processing does so even for URL imports, where the folder is unnecessary.
- `ingestion_output_path_pattern` remains active and defaults to `Imported/` at the vault root. Import service, processing defaults, output-path helpers/models, and job presentation use it. Explorer direct imports already pass the selected destination.
- Existing `Import`, `Imported`, or similarly named folders may contain user files. Do not delete, move, or rewrite them as part of retiring automatic folder creation or defaults.

## Agreed implementation scope

Retire unnecessary import-folder provisioning and the global `ingestion_output_path_pattern` setting. Keep Explorer-selected destinations and explicitly supplied tool/workflow destinations authoritative. Agreed: omitted destinations use the vault root. Explicit destinations, including Explorer's selected folder, remain authoritative.

The user explicitly approved retiring `POST /api/import/scan` and the automatic-inbox workflow. Remove its request/response models, service/facade exports, inbox-specific source fallback/mirroring, and unused folder constant. Preserve processing of already queued explicit vault-relative sources, stored destinations, and source-consumption flags; keep the worker for intentionally submitted queue jobs. Do not silently broaden this request into retiring the ingestion pipeline or deleting source documents. Preserve meaningful output/source/asset and collision contracts.

## Validation-first implementation

- Protect ongoing import behavior: successful URL and PDF imports, omitted destinations resolving to the vault root, explicit destination selection, source preservation, and preservation of existing user files.
- Retain existing meaningful queue-processing, source-consumption, and settings-repair coverage. Update affected scenarios to use supported entry points.
- Verify removal of retired folders, setting consumers, and scan endpoint by source inspection. Do not add permanent absence-only scenarios for retired features, per the user's testing decision.
- Do not add a retirement-specific scenario file. Update or remove existing import assertions when the supported contract changes; retiring code alone does not require new scenarios. The user clarified this after the initial implementation, and the added retirement test file and its registry entry were removed.
- Reuse controlled local HTTP/PDF providers for deterministic imports; keep in-app ingestion, storage, processing, mutation recording, and source preservation real.
- Update affected scenario admission records, run narrow import/startup/settings checks and the production quality gate. Run the full deterministic core profile after behavior is stable if implementation changes justify it; experimental/live-service checks remain opt-in.

## Documentation and completion

Identify exact affected import-tool and setup/default documentation, show proposed text or diffs, and obtain explicit approval before editing protected docs or release notes. Record approved decisions, implementation scope, meaningful red/green evidence, and any retained legacy-path boundary here. Commit validated changes locally; pushing requires user authorization for this work.

## Implementation and evidence

- Removed the inbox scan endpoint, request/response models, orchestration exports, inbox folder constants, implicit inbox provisioning, and inbox-specific basename fallback and destination mirroring.
- Imports without a destination now write to the vault root. Explicit stored destinations and source-consumption flags remain honored; intentional direct imports preserve source documents.
- Removed `ingestion_output_path_pattern` from the settings template and all consumers; the centralized settings upgrade retires existing entries while preserving other settings.
- The two retained positive acceptance scenarios failed against the old output default: `validation/runs/pytest_20261108_034818_265aab2ee4/report.md`. Seven of eight focused import checks passed initially; one test fixture incorrectly treated a SQLAlchemy model as a dataclass. Correcting it to retrieve public job status preserved all original behavioral assertions; the two processing scenarios then passed in `validation/runs/pytest_20261108_035009_82fdbfc139/report.md`.
- Ruff, Black, and mypy passed; admission inventory reported no issues and all 34 policy checks passed. Full deterministic core validation passed: 554 scenarios in 617 seconds, `validation/runs/pytest_20261108_035016_ba8fd676e3/report.md`. All eight import browser scenarios passed in 94 seconds, `validation/runs/pytest_20261108_035018_32e6c8b7f6/report.md`.
- Existing pending package metadata changes align the version with the approved v0.9.5 release notes; the lockfile changes only the root package name/version ordering and no dependency packages.
- Tool reference, installation summary, and v0.9.5 documentation drafts remain pending explicit approval and are excluded from this implementation commit.

## Test scope correction

Removed `test_import_folder_retirement.py` and its admission entry at the user's direction. Its two scenarios were introduced for this retirement and are not retained or renamed. Existing import suites retain the required destination, queue-processing, source-preservation, and source-consumption updates. The earlier 554-case result above is historical validation before this removal; the suite now contains the original 552 core scenarios. Production code is unchanged by this correction.

Validation of the correction: all six existing targeted import scenarios and all 34 registry/collection policy checks passed; inventory reported no issues. The full suite was not rerun, as requested.
