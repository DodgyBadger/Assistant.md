# v0.9.2 Planning Archive

These completed plans record the settings update and its related Vault Explorer change. They are historical working records: earlier proposals, branch names, pending items, and validation snapshots may have been superseded later in the same plan or another plan. Current contracts live in the product documentation and ADRs.

- [Settings UI upgrade](SETTINGS_UI_UPGRADE_PLAN.md): typed controls, validation, theme-aware list pills and operator status.
- [Durable model aliases](MODEL_SELECTION_DESIGN_PLAN.md): managed catalog ownership, editable alternates, model upgrades and configuration UI.
- [Settings database and lifecycle](SETTINGS_DATABASE_PLAN.md): SQLite canonical state, versioned checkpoints, inherited defaults, reset and UI availability.
- [Shipped defaults review](SETTINGS_DEFAULTS_REVIEW_PLAN.md): final approved application defaults and default-model setup diagnostics.
- [Versioned settings peer hardening](VERSIONED_SETTINGS_HARDENING_PLAN.md): backup revision fencing, reset draft handling and trailing refreshes.
- [Vault review workflow](VAULT_REVIEW_WORKFLOW_PLAN.md): keep the reader open during immediate quote requests and refresh after completion.

The final peer fixes passed focused Python, Node, browser and individual integration checks. The latest full deterministic run was 134/139 before five scenario corrections, which subsequently passed targeted reruns. A new full run remains on hold at the user's request; this archive does not claim a clean full pass against the final branch.

## Final pre-merge check

The branch merges cleanly with the fetched origin/main. Project, lockfile and release-note versions agree at 0.9.2, uv lock --check passes, CSS compiles, and Ruff, Black and MyPy pass. The focused Python group initially passed 66/67; the stale status-selector assertion was updated to the agreed preserve-valid-selection contract and all eight affected status/selection tests pass. All 28 Node checks and 24 browser checks pass. Branch additions contain no tracked runtime/secrets files or new debug/TODO markers. The archive moves preserve all six original plan files byte-for-byte.

The remaining merge-readiness limitation is the held full deterministic validation run. No additional full run was started during this check, and no fresh full-pass claim is made.
