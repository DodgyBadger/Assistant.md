(function configurationImportJobsFeature(window) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, constants, elements, helpers, state, timers } = runtime;
    const { DEFAULT_IMPORT_JOB_STATUSES } = constants;
    const { escapeHtml, formatDateTime, iconButton, iconSvg, safeJson, setStatus } = helpers;

    function renderImportJobs() {
        if (!elements.importJobsList) return;
        const jobs = Array.isArray(state.importJobs) ? state.importJobs : [];
        const selectedVault = (elements.importVaultSelect?.value || '').trim();
        if (!selectedVault) {
            if (elements.importJobsSummary) {
                elements.importJobsSummary.textContent = 'Select a vault to view import jobs.';
            }
            elements.importJobsLoadOlderBtn?.classList.add('hidden');
            elements.importJobsList.innerHTML = '<p>Select a vault to load its import history.</p>';
            syncImportJobPolling();
            return;
        }
        if (elements.importJobsSummary) {
            const selectedCounts = selectedImportJobStatuses().map(status => (
                `${Number(state.importJobStatusCounts[status] || 0)} ${status}`
            ));
            elements.importJobsSummary.textContent = state.importJobsTotalMatching
                ? `${selectedCounts.join(' · ')} · ${jobs.length} loaded`
                : 'No recent imports.';
        }
        elements.importJobsLoadOlderBtn?.classList.toggle('hidden', !state.importJobsNextCursor);
        if (!jobs.length) {
            elements.importJobsList.innerHTML = '<p>No import jobs match the selected statuses.</p>';
            syncImportJobPolling();
            return;
        }

        elements.importJobsList.innerHTML = `
            <div class="dashboard-table-wrap import-jobs-table-wrap" role="region" aria-label="Recent import jobs" tabindex="0">
                <table class="dashboard-table">
                    <thead>
                        <tr>
                            <th class="import-job-compact">Job</th>
                            <th class="import-job-source">Source</th>
                            <th class="import-job-compact">Status</th>
                            <th>Strategy</th>
                            <th class="import-job-compact">Updated</th>
                            <th>Output / Error</th>
                            <th class="cell-center import-job-compact">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${jobs.map(job => {
                            const outputs = Array.isArray(job.outputs) ? job.outputs : [];
                            const detail = job.error
                                ? escapeHtml(job.error)
                                : actions.renderImportOutputLinks(outputs, job.vault, { compact: true });
                            const strategy = [
                                job.selected_strategy,
                                job.selected_provider,
                                job.selected_model
                            ].filter(Boolean).join(' · ') || '—';
                            const cancelButton = job.status === 'queued'
                                ? `<button data-import-job-cancel="${escapeHtml(job.id)}" ${iconButton('x', `Cancel import job ${job.id}`, 'is-danger')}>${iconSvg('x')}</button>`
                                : '';
                            const editButton = job.can_resubmit
                                ? `<button data-import-job-edit="${escapeHtml(job.id)}" ${iconButton('edit', `Edit and resubmit import job ${job.id}`, 'is-primary')}>${iconSvg('edit')}</button>`
                                : '';
                            return `
                                <tr>
                                    <td data-label="Job" class="cell-xs cell-mono import-job-compact">${escapeHtml(job.id)}</td>
                                    <td data-label="Source" class="cell-xs import-job-source">${actions.renderImportSource(job.source_uri, job.vault)}</td>
                                    <td data-label="Status" class="cell-xs import-job-compact">${escapeHtml(job.status || 'unknown')}</td>
                                    <td data-label="Strategy" class="cell-xs">${escapeHtml(strategy)}</td>
                                    <td data-label="Updated" class="cell-xs import-job-compact">${escapeHtml(formatDateTime(job.updated_at))}</td>
                                    <td data-label="Output / Error" class="cell-xs">${detail}</td>
                                    <td data-label="Actions" class="cell-center import-job-actions import-job-compact"><div class="import-job-action-buttons">${cancelButton}${editButton}</div></td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        `;
        syncImportJobPolling();
    }

    function selectedImportJobStatuses() {
        if (!elements.importJobsStatusFilters) return [...DEFAULT_IMPORT_JOB_STATUSES];
        return Array.from(elements.importJobsStatusFilters.querySelectorAll('input[type="checkbox"]:checked'))
            .map(input => input.value);
    }

    function handleImportJobFilterChange(event) {
        if (!selectedImportJobStatuses().length && event.target instanceof HTMLInputElement) {
            event.target.checked = true;
            return;
        }
        if (state.isLoadingImportJobs) {
            state.pendingImportJobsReload = true;
        } else {
            loadImportJobs();
        }
    }

    function syncImportJobPolling() {
        const hasActiveJobs = (state.importJobs || []).some(
            job => job.status === 'queued' || job.status === 'processing'
        );
        if (hasActiveJobs && !timers.importJobPoll) {
            timers.importJobPoll = window.setInterval(() => loadImportJobs({ silent: true }), 3000);
        } else if (!hasActiveJobs && timers.importJobPoll) {
            window.clearInterval(timers.importJobPoll);
            timers.importJobPoll = null;
        }
    }

    async function loadImportJobs({ silent = false, append = false } = {}) {
        if (state.isLoadingImportJobs) {
            state.pendingImportJobsReload = true;
            return;
        }
        if (append && !state.importJobsNextCursor) return;
        const selectedVault = (elements.importVaultSelect?.value || '').trim();
        if (!selectedVault) {
            state.importJobs = [];
            state.importJobsNextCursor = null;
            state.importJobsTotalMatching = 0;
            state.importJobStatusCounts = {};
            state.hasLoadedImportJobs = true;
            renderImportJobs();
            return;
        }
        state.isLoadingImportJobs = true;
        if (!silent) setStatus(elements.importJobsFeedback, 'Refreshing import status…', 'info');
        try {
            const refreshLimit = silent && !append
                ? Math.min(Math.max(state.importJobs.length, 25), 100)
                : 25;
            const params = new URLSearchParams({ limit: String(refreshLimit) });
            params.set('vault', selectedVault);
            const selectedStatuses = selectedImportJobStatuses();
            selectedStatuses.forEach(status => params.append('status', status));
            if (append) params.set('cursor', state.importJobsNextCursor);
            const scrollTop = elements.importJobsList
                ?.querySelector('.import-jobs-table-wrap')?.scrollTop || 0;
            const response = await fetch(`api/import/jobs?${params.toString()}`, { cache: 'no-store' });
            const data = await safeJson(response);
            if (!response.ok) throw new Error(data?.message || `HTTP ${response.status}`);
            if ((elements.importVaultSelect?.value || '').trim() !== selectedVault) return;
            if (selectedImportJobStatuses().join('\u0000') !== selectedStatuses.join('\u0000')) return;
            const received = Array.isArray(data?.jobs) ? data.jobs : [];
            let nextJobs;
            if (append) {
                const knownIds = new Set(state.importJobs.map(job => job.id));
                nextJobs = [
                    ...state.importJobs,
                    ...received.filter(job => !knownIds.has(job.id))
                ];
            } else {
                nextJobs = received;
            }
            const nextCursor = data?.next_cursor || null;
            const nextTotalMatching = Number(data?.total_matching || 0);
            const nextStatusCounts = data?.status_counts || {};
            const viewChanged = (
                !state.hasLoadedImportJobs
                || JSON.stringify(nextJobs) !== JSON.stringify(state.importJobs)
                || nextCursor !== state.importJobsNextCursor
                || nextTotalMatching !== state.importJobsTotalMatching
                || JSON.stringify(nextStatusCounts) !== JSON.stringify(state.importJobStatusCounts)
            );
            state.importJobs = nextJobs;
            state.importJobsNextCursor = nextCursor;
            state.importJobsTotalMatching = nextTotalMatching;
            state.importJobStatusCounts = nextStatusCounts;
            state.hasLoadedImportJobs = true;
            if (viewChanged) {
                renderImportJobs();
                const tableWrap = elements.importJobsList?.querySelector('.import-jobs-table-wrap');
                if (tableWrap) tableWrap.scrollTop = scrollTop;
            }
            if (!silent) setStatus(elements.importJobsFeedback, '', 'info');
        } catch (error) {
            if (!silent) setStatus(elements.importJobsFeedback, `Failed to load imports: ${error.message}`, 'error');
        } finally {
            state.isLoadingImportJobs = false;
            if (state.pendingImportJobsReload) {
                state.pendingImportJobsReload = false;
                loadImportJobs();
            }
        }
    }

    async function handleImportJobAction(event) {
        const target = event.target;
        if (!(target instanceof Element)) return;
        const outputLink = target.closest('[data-import-output-path]');
        if (outputLink instanceof HTMLElement) {
            const path = outputLink.getAttribute('data-import-output-path');
            const vault = outputLink.getAttribute('data-import-output-vault');
            if (path && callbacks.openFile) callbacks.openFile(path, vault || undefined);
            return;
        }
        const sourceLink = target.closest('[data-import-source-path]');
        if (sourceLink instanceof HTMLElement) {
            const path = sourceLink.getAttribute('data-import-source-path');
            const vault = sourceLink.getAttribute('data-import-source-vault');
            if (path) callbacks.openExplorer?.({ vaultName: vault || '', revealPath: path });
            return;
        }
        const button = target.closest('[data-import-job-cancel], [data-import-job-edit]');
        if (!(button instanceof HTMLElement) || button.disabled) return;
        const editJobId = button.getAttribute('data-import-job-edit');
        if (editJobId) {
            const job = state.importJobs.find(item => String(item.id) === editJobId);
            if (!job || !job.can_resubmit) return;
            const request = {
                vaultName: job.vault || '',
                importOptions: job.request_options || {},
            };
            if (job.source_type === 'url') request.importUrl = job.source_uri || '';
            else request.importSources = [job.source_uri || ''].filter(Boolean);
            callbacks.openExplorer?.(request);
            return;
        }
        const jobId = button.getAttribute('data-import-job-cancel');
        if (!jobId) return;
        button.disabled = true;
        setStatus(elements.importJobsFeedback, `Cancelling import job ${jobId}…`, 'info');
        try {
            const response = await fetch(
                `api/import/jobs/${encodeURIComponent(jobId)}/cancel`,
                { method: 'POST' }
            );
            const data = await safeJson(response);
            if (!response.ok) throw new Error(data?.message || `HTTP ${response.status}`);
            setStatus(elements.importJobsFeedback, `Import job ${jobId} cancelled.`, 'success');
            await loadImportJobs({ silent: true });
        } catch (error) {
            setStatus(
                elements.importJobsFeedback,
                `Could not cancel import: ${error.message}`,
                'error'
            );
            button.disabled = false;
        }
    }

    async function handleRunImportQueueNow() {
        if (!elements.importJobsRunNowBtn || state.isTriggeringImportQueue) return;
        state.isTriggeringImportQueue = true;
        elements.importJobsRunNowBtn.disabled = true;
        setStatus(elements.importJobsFeedback, 'Requesting an ingestion worker run…', 'info');
        try {
            const response = await fetch('api/import/run-now', { method: 'POST' });
            const data = await safeJson(response);
            if (!response.ok) throw new Error(data?.message || `HTTP ${response.status}`);
            const queuedCount = Number(data?.queued_count || 0);
            setStatus(
                elements.importJobsFeedback,
                queuedCount
                    ? `Worker run requested for ${queuedCount} queued import${queuedCount === 1 ? '' : 's'}.`
                    : 'Worker run requested; the queue is currently empty.',
                'success'
            );
            await loadImportJobs({ silent: true });
        } catch (error) {
            setStatus(elements.importJobsFeedback, `Could not run imports: ${error.message}`, 'error');
        } finally {
            state.isTriggeringImportQueue = false;
            elements.importJobsRunNowBtn.disabled = false;
        }
    }

    Object.assign(actions, {
        renderImportJobs,
        selectedImportJobStatuses,
        handleImportJobFilterChange,
        syncImportJobPolling,
        loadImportJobs,
        handleImportJobAction,
        handleRunImportQueueNow
    });
}(window));
