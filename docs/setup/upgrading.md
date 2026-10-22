# Upgrading

⚠️ Beta software. Check the [release notes](https://github.com/DodgyBadger/Assistant.md/releases/latest) before upgrading. If you are upgrading from before v0.8, complete the [deployment migration](#upgrading-from-releases-before-v08) before following the routine update procedure. When crossing v0.9.0, also review its [migration notes](#upgrading-to-v090). You do not need to install intermediate releases.

## Routine updates

Let running chats and workflows finish. Back up your vaults, `system/`, `.env`, and Compose files. Protect the `.env` backup separately: its encryption key is needed to restore credentials. Automatic database migration backups do not replace a full deployment backup.

Keep the repository's tracked `docker-compose.yml` and put local customizations in `.env` and `docker-compose.override.yml`.

1. Update the checkout with `git pull --ff-only` so it receives required Compose changes.
2. Validate, stop, update, and restart:

   ```bash
   docker compose config --quiet
   docker compose down
   docker compose pull
   docker compose up -d
   ```

   For a repository build, replace `docker compose pull` with `docker compose build`. **Do not add `-v` to `docker compose down`**: it deletes persistent Docker volumes, including advanced-shell files and pairing state.
3. Open **System → Infrastructure** and confirm the expected authentication and execution modes. Advanced mode is ready when the shell reports `ready`. Review System Notices and System Activity for migration or configuration issues.

## Upgrading to v0.9.0

### Session discovery and retired summaries

Session discovery searches original transcripts and session-map revisions, including existing conversations without maps. It no longer requires an embedding model or nightly summarization.

Startup backs up legacy summary data before removing retired summary tables. Original conversations and maps remain intact. Packaged nightly-summary workflows are archived; customized workflows are preserved with review diagnostics. Remove or rewrite calls to retired summary operations and helpers. If custom database dependencies block retirement, preserve or migrate them before retrying; do not delete the database to bypass the check.

Embedding aliases are excluded from active configuration. Use **Repair settings from template** in System Notices to remove them with a backup. Shared providers, credentials, and supported chat/decision model aliases are preserved.

### Optional Compaction v2

V1 recovery-card compaction remains the default. Set `compaction_strategy` to `session_map` in Settings to use V2 for new sessions. For conversations already compacted under V1, choose **Upgrade to Compaction v2** in the session browser individually; changing the default does not convert them.

In V2, use **Load older messages** to inspect retained original history and the session's map icon to inspect its interpretation. See [Search past sessions](../use/getting-the-most.md#search-past-sessions) for recovering earlier work.

## Upgrading from releases before v0.8

Before pulling or restarting, back up the deployment as described above, then migrate its configuration:

1. If `git remote get-url origin` still points to the former repository address, update it. Your local folder can keep its existing name:

   ```bash
   git remote set-url origin https://github.com/DodgyBadger/Assistant.md.git
   ```

2. Preserve the old local Compose file before pulling the tracked replacement:

   ```bash
   mv docker-compose.yml docker-compose.pre-v0.8.yml
   git pull --ff-only
   ```

   Do not copy the old file back over the new `docker-compose.yml`.
3. Copy `.env.example` to `.env` if needed. Preserve the existing encryption key; otherwise follow [Configure `.env`](installation.md#3-configure-env). Transfer the old host paths:

   ```dotenv
   ASSISTANTMD_DATA_PATH=/old/host/path/previously-mounted-at-app-data
   ASSISTANTMD_SYSTEM_PATH=/old/host/path/previously-mounted-at-app-system
   ```

   Preserve the timezone and appropriate authentication mode, authentication secret, and public URL.
4. Move structural customizations—builds, UID/GID, proxy networks, and extra mounts—into `docker-compose.override.yml`. Copy only the required sections from `docker-compose.override.yml.example`.
5. For advanced mode, add both settings; otherwise leave them unset:

   ```dotenv
   COMPOSE_PROFILES=advanced
   ASSISTANTMD_EXECUTION_MODE=advanced
   ```

6. The checkout is already updated. Continue with validation and restart in [Routine updates](#routine-updates). With the supplied build override, an active advanced profile builds both containers from the checkout.
7. If you use the packaged default context script, preserve custom changes before refreshing system scripts under **System → Misc**.
8. Confirm provider API keys were imported, and reconnect every OAuth account: legacy OAuth state is not imported. Configure and test accounts under [Connections](../use/connections.md). Attachment downloads and draft creation require explicit opt-in; enabling Gmail drafts also requires reauthorization.

The first startup migrates legacy non-OAuth static secrets into encrypted storage. It preserves the old file at `system/migration_backups/secrets.yaml.bak`, using a numbered name if necessary.

## Model aliases during upgrades

Keep model aliases stable because scripts and settings may refer to them. If you rename or remove one, update its references in the same change.

Packaged defaults live in `core/settings/settings.template.yaml`; active mappings live in persistent `system/settings.yaml`. Settings repair adds missing supported models while preserving existing supported entries and user-defined models.

To replace an existing alias with its updated packaged default, delete it under **System → Models**, then choose **Repair settings from template** in System Notices. Repair backs up settings before restoring it. Only delete a customized model when you intend to replace that customization.
