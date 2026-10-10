# Upgrading

⚠️ Beta software. Check the [release notes](https://github.com/DodgyBadger/Assistant.md/releases/latest) before upgrading.

Existing checkouts can keep their current local folder name. If `git remote get-url origin` still points to the former `DodgyBadger/AssistantMD` address, update it once before pulling:

```bash
git remote set-url origin https://github.com/DodgyBadger/Assistant.md.git
```

## Upgrading to v0.9.0 (planned)

If you are upgrading from any release before v0.8, complete the [pre-v0.8 deployment changes](#upgrading-from-releases-before-v08) below as part of this upgrade. You do not need to install v0.8 separately.

### Before restarting

Back up your vaults, `system/`, `.env`, and Compose files, and let running chats and workflows finish. Keep the `.env` backup secure and separate: its encryption key is needed to read your stored credentials. The automatic database migration backup does not replace a full deployment backup.

For an existing v0.8 deployment, keep the tracked Compose file and your local override, then update and restart:

```bash
docker compose config --quiet
docker compose down
docker compose pull
docker compose up -d
```

For a repository build, update your checkout with `git pull --ff-only` before validating the Compose configuration, and replace `docker compose pull` with `docker compose build`. Do not use `docker compose down -v`: it deletes persistent Docker volumes.

### Session discovery and retired summaries

Session discovery now searches original transcripts and session-map revisions without an embedding model or nightly summarization. It works for existing sessions, including those without maps; there is no need to upgrade every conversation to V2 to make it searchable.

Startup backs up legacy summary data before removing the retired summary tables. Original conversations and session maps remain intact. Recognizable packaged nightly-summary workflows are archived; customized workflows are preserved with review diagnostics, but calls to retired summary operations and helpers must be removed or rewritten. Check System Notices and System Activity after startup. If custom database dependencies block retirement, preserve or migrate those dependencies before retrying; do not delete the database to bypass the check.

Embedding model aliases are no longer supported and are excluded from active configuration. Use **Repair settings from template** in System Notices to remove those aliases from the settings file with a backup. Shared providers, credentials and supported chat/decision model aliases are preserved; do not delete a provider or credential merely because it was also used for embeddings.

### Optional Compaction v2

V1 recovery-card compaction remains the default. To use V2 for new sessions, set `compaction_strategy` to `session_map` in Settings. Sessions already compacted under V1 keep that strategy; use **Upgrade to Compaction v2** in the session browser for each conversation you want to convert. Changing the default does not convert existing V1 sessions automatically.

V2 uses `compaction_high_watermark_tokens` to trigger automatic compaction and `compaction_low_watermark_tokens` to target the remaining map and raw-history size. `compaction_retained_turns` applies only to V1. The optional `compaction_author_model` applies to both strategies and falls back to the default chat model when unset. See [long-chat continuity guidance](../use/getting-the-most.md#keep-continuity-in-long-chats) for inspection and retrieval.

## Upgrading from releases before v0.8

These deployment changes apply when moving from any pre-v0.8 release to v0.8 or later, including a direct upgrade to v0.9.0. Existing v0.8 installations do not need to repeat them.

1. Back up your vaults, `system/`, `.env`, and current Compose files. Keep the `.env` backup separate because it contains the key needed to read encrypted credentials.

2. v0.8 tracks `docker-compose.yml` so later pulls automatically receive required topology changes. In an existing repository checkout, preserve your old local file before pulling:

   ```bash
   mv docker-compose.yml docker-compose.pre-v0.8.yml
   git pull --ff-only
   ```

   Do not copy the old file back over the new tracked `docker-compose.yml`.

3. Copy `.env.example` to `.env` if needed. Preserve an existing encryption key; otherwise follow [Configure `.env`](installation.md#3-configure-env) to generate one. Move the old Compose values into `.env`:

   ```dotenv
   ASSISTANTMD_DATA_PATH=/old/host/path/previously-mounted-at-app-data
   ASSISTANTMD_SYSTEM_PATH=/old/host/path/previously-mounted-at-app-system
   ```

   Also preserve the timezone, authentication mode, authentication secret, and public URL appropriate to the deployment.

4. Move structural customizations—custom builds or UID/GID, external proxy networks, and extra bind mounts—into `docker-compose.override.yml`. Start from `docker-compose.override.yml.example` and copy only the sections you need. Do not edit the tracked `docker-compose.yml`.

5. If you want advanced mode, add these values to `.env`; otherwise leave them unset:

   ```dotenv
   COMPOSE_PROFILES=advanced
   ASSISTANTMD_EXECUTION_MODE=advanced
   ```

6. Validate and restart the deployment:

   ```bash
   docker compose config --quiet
   docker compose down
   docker compose pull
   docker compose up -d
   ```

   For a repository build using the override example, replace `docker compose pull` with `docker compose build`. The override builds both Assistant.md and the advanced shell from the same checkout when the `advanced` profile is active.

7. Open **System → Infrastructure** and confirm the expected authentication and execution modes. Advanced mode is ready when the advanced shell reports `ready`.

8. If you use the packaged default context script, refresh system scripts under **System → Misc**. Save any custom changes to the existing system script first; refresh installs the current soul and playbook loading behavior.

9. Confirm that model-provider API keys were imported, then reconnect every existing OAuth account. Legacy OAuth state is not imported into the encrypted store. Configure and test Gmail and MCP connections under **System → Connections**. Gmail attachment downloads and draft creation remain disabled on each connection until explicitly enabled; enabling drafts requires reauthorizing that connection for Gmail compose permission.

On first startup after upgrading from a pre-v0.8 release, Assistant.md migrates legacy non-OAuth static secrets into encrypted storage and keeps the old file at `system/migration_backups/secrets.yaml.bak`, or the next available numbered name when that file already exists. Routine `docker compose down` preserves advanced-shell pairing, installed files, and workspace data. Do not add `-v` unless you deliberately want to delete those Docker volumes.

## Model aliases during upgrades

Model aliases are part of the authoring contract because context scripts, workflows, and other automation can refer to them directly. Keep an existing alias stable when changing the provider model it resolves to. If you rename or remove an alias, update every script and setting that refers to it in the same change.

Packaged model defaults live in `core/settings/settings.template.yaml`, while the active mappings live in the persistent `system/settings.yaml`. Settings repair copies only new or missing model entries from the packaged template; it never overwrites a supported existing entry with the same alias, and it preserves supported user-defined models. Embedding-capable aliases have no supported consumer: they are excluded from active configuration and removed by backed-up settings repair. Shared providers, secrets and supported chat/decision aliases remain intact.

When an upgrade changes the packaged `model_string` for an existing alias, open **System → Models**, delete that model, then select **Repair settings from template** in System Notices. The repair action creates a settings backup and restores the model from the current packaged defaults. Do not delete a customized model unless you intend to replace it with the packaged version.
