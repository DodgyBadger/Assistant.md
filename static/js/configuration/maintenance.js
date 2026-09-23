(function configurationFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, constants, elements, helpers, resources, state, timers } = runtime;
    const { ACTIVITY_LOG_LEVELS, BUILT_IN_PROVIDER_NAMES, DEFAULT_IMPORT_JOB_STATUSES, SECRET_METADATA } = constants;
    const { escapeHtml, formatDateTime, iconButton, iconSvg, notifyConfigChanged, safeJson, setIconButtonLabel, setStatus, withRestartNotice } = helpers;

    async function loadVaultOptions(select) {
        if (!select) return;

        const cachedVaults = window.App && window.App.metadata && Array.isArray(window.App.metadata.vaults)
            ? window.App.metadata.vaults
            : null;
        const vaults = cachedVaults || await (async () => {
            try {
                const response = await fetch('api/metadata');
                if (!response.ok) return [];
                const data = await response.json();
                window.App = window.App || {};
                window.App.metadata = data;
                return Array.isArray(data?.vaults) ? data.vaults : [];
            } catch { return []; }
        })();

        select.innerHTML = '<option value="">Select vault…</option>';
        vaults.forEach((vault) => {
            const opt = document.createElement('option');
            opt.value = vault;
            opt.textContent = vault;
            select.appendChild(opt);
        });
    }

    async function loadPurgeSessionsVaults() {
        await loadVaultOptions(elements.purgeSessionsVault);
    }

    async function loadCleanupGoalsVaults() {
        await loadVaultOptions(elements.cleanupGoalsVault);
    }

    async function handlePurgeSessions() {
        const btn = elements.purgeSessionsBtn;
        if (!btn || btn.disabled) return;

        const vaultName = elements.purgeSessionsVault?.value;
        if (!vaultName) {
            setStatus(elements.purgeSessionsFeedback, 'Select a vault first.', 'warning');
            return;
        }

        const ageValue = elements.purgeSessionsAge?.value;
        const olderThanDays = ageValue ? parseInt(ageValue, 10) : null;

        const ageLabel = ageValue ? `older than ${ageValue} days` : 'all sessions';
        if (!confirm(`Delete ${ageLabel} in vault "${vaultName}"? This cannot be undone.`)) return;

        btn.disabled = true;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Purge Sessions';
        setIconButtonLabel(btn, 'Purging sessions...');
        setStatus(elements.purgeSessionsFeedback, 'Purging sessions…', 'info');

        try {
            const response = await fetch('api/chat/sessions/purge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ vault_name: vaultName, older_than_days: olderThanDays }),
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            setStatus(elements.purgeSessionsFeedback, result.message, 'success');
        } catch (error) {
            setStatus(elements.purgeSessionsFeedback, `Failed to purge sessions: ${error.message}`, 'error');
        } finally {
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
        }
    }

    async function handleCleanupGoals() {
        const btn = elements.cleanupGoalsBtn;
        if (!btn || btn.disabled || state.isCleaningGoals) return;

        const vaultName = elements.cleanupGoalsVault?.value;
        if (!vaultName) {
            setStatus(elements.cleanupGoalsFeedback, 'Select a vault first.', 'warning');
            return;
        }

        const statusValue = elements.cleanupGoalsStatus?.value || 'completed';
        const ageValue = elements.cleanupGoalsAge?.value;
        const olderThanDays = ageValue ? parseInt(ageValue, 10) : null;
        const statusLabel = elements.cleanupGoalsStatus?.selectedOptions?.[0]?.textContent || 'matching goals';
        const ageLabel = ageValue ? `older than ${ageValue} days` : 'of any age';

        if (!confirm(`Delete ${statusLabel.toLowerCase()} ${ageLabel} in vault "${vaultName}"? This cannot be undone.`)) return;

        state.isCleaningGoals = true;
        btn.disabled = true;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Clean Up Goals';
        setIconButtonLabel(btn, 'Cleaning goals...');
        setStatus(elements.cleanupGoalsFeedback, 'Cleaning up goals…', 'info');

        try {
            const response = await fetch('api/system/goals/cleanup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    vault_name: vaultName,
                    status: statusValue,
                    older_than_days: olderThanDays,
                }),
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            setStatus(elements.cleanupGoalsFeedback, result.message, 'success');
        } catch (error) {
            setStatus(elements.cleanupGoalsFeedback, `Failed to clean up goals: ${error.message}`, 'error');
        } finally {
            state.isCleaningGoals = false;
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
        }
    }

    async function handlePurgeExpiredCache() {
        if (!elements.purgeExpiredCacheBtn || state.isPurgingCache) return;

        state.isPurgingCache = true;
        const button = elements.purgeExpiredCacheBtn;
        const originalLabel = button.dataset.iconLabel || button.title || 'Purge Expired Cache';
        button.disabled = true;
        setIconButtonLabel(button, 'Purging expired cache...');
        setStatus(elements.miscFeedback, 'Purging expired cache artifacts…', 'info');

        try {
            const response = await fetch('api/system/cache/purge-expired', {
                method: 'POST'
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            setStatus(
                elements.miscFeedback,
                result?.message || `Purged ${result?.purged_count ?? 0} expired cache artifact(s).`,
                'success'
            );
        } catch (error) {
            setStatus(elements.miscFeedback, `Failed to purge cache: ${error.message}`, 'error');
        } finally {
            button.disabled = false;
            setIconButtonLabel(button, originalLabel);
            state.isPurgingCache = false;
        }
    }

    async function handleRefreshSystemAuthoring() {
        if (!elements.refreshSystemAuthoringBtn || state.isRefreshingSystemAuthoring) return;

        const confirmed = window.confirm(
            'Refresh system authoring scripts?\n\n'
            + 'This will overwrite scripts in system/Authoring. Use this if you have customized '
            + 'the system scripts and want to return to baseline or update to the latest version '
            + 'of system scripts. Vault scripts in AssistantMD/Authoring are not touched.'
        );
        if (!confirmed) return;

        state.isRefreshingSystemAuthoring = true;
        const button = elements.refreshSystemAuthoringBtn;
        const originalLabel = button.dataset.iconLabel || button.title || 'Refresh System Scripts';
        button.disabled = true;
        setIconButtonLabel(button, 'Refreshing system scripts...');
        setStatus(elements.refreshSystemAuthoringFeedback, 'Refreshing system authoring scripts…', 'info');

        try {
            const response = await fetch('api/system/authoring/seed-refresh', {
                method: 'POST'
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            setStatus(
                elements.refreshSystemAuthoringFeedback,
                result?.message || 'System authoring scripts refreshed.',
                result?.success === false ? 'warning' : 'success'
            );
            await callbacks.refreshStatus?.();
        } catch (error) {
            setStatus(
                elements.refreshSystemAuthoringFeedback,
                `Failed to refresh system authoring scripts: ${error.message}`,
                'error'
            );
        } finally {
            button.disabled = false;
            setIconButtonLabel(button, originalLabel);
            state.isRefreshingSystemAuthoring = false;
        }
    }

    async function handleCleanupVaultState() {
        if (!elements.cleanupVaultStateBtn || state.isCleaningVaultState) return;

        state.isCleaningVaultState = true;
        const button = elements.cleanupVaultStateBtn;
        const originalLabel = button.dataset.iconLabel || button.title || 'Clean Up Vault State';
        button.disabled = true;
        setIconButtonLabel(button, 'Cleaning vault state...');
        setStatus(elements.cleanupVaultStateFeedback, 'Cleaning expired vault-state artifacts…', 'info');

        try {
            const response = await fetch('api/vault-state/cleanup', {
                method: 'POST'
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            const message = result?.message || (
                `Deleted ${result?.expired_mutation_rows_deleted ?? 0} mutation row(s), `
                + `${result?.expired_snapshot_rows_deleted ?? 0} snapshot row(s), `
                + `${result?.snapshot_files_deleted ?? 0} snapshot file(s).`
            );
            setStatus(elements.cleanupVaultStateFeedback, message, 'success');
        } catch (error) {
            setStatus(
                elements.cleanupVaultStateFeedback,
                `Failed to clean vault state: ${error.message}`,
                'error'
            );
        } finally {
            button.disabled = false;
            setIconButtonLabel(button, originalLabel);
            state.isCleaningVaultState = false;
        }
    }

    async function loadSystemJobs() {
        if (!elements.systemJobsList || state.isLoadingSystemJobs) return;

        state.isLoadingSystemJobs = true;
        const button = elements.refreshSystemJobsBtn;
        const originalLabel = button ? (button.dataset.iconLabel || button.title || 'Refresh System Jobs') : '';
        if (button) {
            button.disabled = true;
            setIconButtonLabel(button, 'Refreshing system jobs...');
        }

        try {
            const response = await fetch('api/status');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const payload = await response.json();
            const jobs = payload?.scheduler?.job_details || [];
            state.systemJobs = jobs.filter((job) => job.job_type === 'system');
            renderSystemJobs();
        } catch (error) {
            elements.systemJobsList.innerHTML = `
                <div class="state-error">Failed to load system jobs: ${escapeHtml(error.message)}</div>
            `;
        } finally {
            if (button) {
                button.disabled = false;
                setIconButtonLabel(button, originalLabel);
            }
            state.isLoadingSystemJobs = false;
        }
    }

    function renderSystemJobs() {
        if (!elements.systemJobsList) return;

        if (!state.systemJobs.length) {
            elements.systemJobsList.innerHTML = `
                <div class="text-sm text-txt-secondary">No system scheduler jobs are currently registered.</div>
            `;
            return;
        }

        const rows = state.systemJobs.map((job) => {
            const lastRun = formatDateTime(job.last_run_time);
            const nextRun = formatDateTime(job.next_run_time);
            const status = job.last_status || 'not run';
            const error = job.last_error
                ? `<div class="state-error text-xs mt-1">${escapeHtml(job.last_error)}</div>`
                : '';
            return `
                <tr>
                    <td>
                        <strong>${escapeHtml(job.name || job.id)}</strong>
                        <div class="cell-xs cell-mono subtle">${escapeHtml(job.id)}</div>
                    </td>
                    <td class="cell-xs">${escapeHtml(status)}${error}</td>
                    <td class="cell-xs">${escapeHtml(lastRun)}</td>
                    <td class="cell-xs">${escapeHtml(nextRun)}</td>
                </tr>
            `;
        }).join('');

        elements.systemJobsList.innerHTML = `
            <div class="overflow-x-auto">
                <table class="dashboard-table">
                    <thead>
                        <tr>
                            <th>Job</th>
                            <th>Status</th>
                            <th>Last Run</th>
                            <th>Next Run</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
    }

    async function loadSystemMigrations() {
        if (!elements.systemMigrationsStatus || state.isLoadingSystemMigrations) return;

        state.isLoadingSystemMigrations = true;
        const button = elements.refreshSystemMigrationsBtn;
        const originalLabel = button ? (button.dataset.iconLabel || button.title || 'Refresh Database Migrations') : '';
        if (button) {
            button.disabled = true;
            setIconButtonLabel(button, 'Refreshing database migrations...');
        }

        try {
            const response = await fetch('api/system/migrations/status');
            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            state.systemMigrations = await response.json();
            renderSystemMigrations();
            setStatus(elements.systemMigrationsFeedback, state.systemMigrations.message || '', 'info');
        } catch (error) {
            elements.systemMigrationsStatus.innerHTML = `
                <div class="state-error">Failed to load database migrations: ${escapeHtml(error.message)}</div>
            `;
            updateSystemMigrationsButton(null);
        } finally {
            if (button) {
                button.disabled = false;
                setIconButtonLabel(button, originalLabel);
            }
            state.isLoadingSystemMigrations = false;
        }
    }

    async function handleRunSystemMigrations() {
        if (!elements.runSystemMigrationsBtn || state.isRunningSystemMigrations) return;
        const pendingCount = Number(state.systemMigrations?.pending_count ?? 0);
        if (pendingCount <= 0) return;

        state.isRunningSystemMigrations = true;
        const button = elements.runSystemMigrationsBtn;
        button.disabled = true;
        setIconButtonLabel(button, 'Running database migrations...');
        if (elements.refreshSystemMigrationsBtn) {
            elements.refreshSystemMigrationsBtn.disabled = true;
        }
        setStatus(elements.systemMigrationsFeedback, 'Running system database migrations…', 'info');

        try {
            const response = await fetch('api/system/migrations/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ backup: true }),
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            state.systemMigrations = result;
            renderSystemMigrations();
            const backupCount = Array.isArray(result.backups_created) ? result.backups_created.length : 0;
            const backupText = backupCount > 0 ? ` ${backupCount} backup file(s) created.` : '';
            setStatus(elements.systemMigrationsFeedback, `${result.message || 'System database migrations completed.'}${backupText}`, 'success');
            await callbacks.refreshStatus?.();
        } catch (error) {
            setStatus(elements.systemMigrationsFeedback, `Failed to run database migrations: ${error.message}`, 'error');
        } finally {
            updateSystemMigrationsButton(state.systemMigrations);
            if (elements.refreshSystemMigrationsBtn) {
                elements.refreshSystemMigrationsBtn.disabled = false;
            }
            state.isRunningSystemMigrations = false;
        }
    }

    function renderSystemMigrations() {
        if (!elements.systemMigrationsStatus) return;

        const payload = state.systemMigrations;
        const targets = Array.isArray(payload?.targets) ? payload.targets : [];
        if (!targets.length) {
            elements.systemMigrationsStatus.innerHTML = `
                <div class="text-sm text-txt-secondary">No registered system database migrations were found.</div>
            `;
            updateSystemMigrationsButton(payload);
            return;
        }

        const rows = targets.map((target) => {
            const applied = formatVersionList(target.applied_versions);
            const pending = formatVersionList(target.pending_versions);
            const exists = target.exists ? 'yes' : 'no';
            const backup = target.backup_path
                ? `<div class="cell-xs cell-mono subtle mt-1">backup: ${escapeHtml(target.backup_path)}</div>`
                : '';
            return `
                <tr>
                    <td>
                        <strong>${escapeHtml(target.db_name)}</strong>
                        <div class="cell-xs cell-mono subtle">${escapeHtml(target.namespace)}</div>
                        ${backup}
                    </td>
                    <td class="cell-xs">${escapeHtml(exists)}</td>
                    <td class="cell-xs">${escapeHtml(applied)}</td>
                    <td class="cell-xs">${escapeHtml(pending)}</td>
                </tr>
            `;
        }).join('');

        const pendingCount = Number(payload?.pending_count ?? 0);
        const summaryTone = pendingCount > 0 ? 'state-warning' : 'state-success';
        const summary = payload?.message || (
            pendingCount > 0
                ? `${pendingCount} system database migration(s) pending.`
                : 'All registered system database migrations are applied.'
        );

        elements.systemMigrationsStatus.innerHTML = `
            <div class="${summaryTone} mb-2">${escapeHtml(summary)}</div>
            <div class="overflow-x-auto">
                <table class="dashboard-table">
                    <thead>
                        <tr>
                            <th>Database</th>
                            <th>Exists</th>
                            <th>Applied</th>
                            <th>Pending</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
        updateSystemMigrationsButton(payload);
    }

    function formatVersionList(value) {
        if (!Array.isArray(value) || value.length === 0) return 'none';
        return value.map((version) => String(version)).join(', ');
    }

    function updateSystemMigrationsButton(payload) {
        const button = elements.runSystemMigrationsBtn;
        if (!button || state.isRunningSystemMigrations) return;

        const pendingCount = Number(payload?.pending_count ?? 0);
        const hasPending = pendingCount > 0;
        button.disabled = !hasPending;
        setIconButtonLabel(button, hasPending ? `Run ${pendingCount} Migration${pendingCount === 1 ? '' : 's'}` : 'Database migrations up to date');
        button.classList.toggle('is-primary', hasPending);
    }


    Object.assign(actions, {
        loadVaultOptions,
        loadPurgeSessionsVaults,
        loadCleanupGoalsVaults,
        handlePurgeSessions,
        handleCleanupGoals,
        handlePurgeExpiredCache,
        handleRefreshSystemAuthoring,
        handleCleanupVaultState,
        loadSystemJobs,
        renderSystemJobs,
        loadSystemMigrations,
        handleRunSystemMigrations,
        renderSystemMigrations,
        formatVersionList,
        updateSystemMigrationsButton
    });
}(window, document));
