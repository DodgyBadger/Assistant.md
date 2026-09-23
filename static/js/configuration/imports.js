(function configurationFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, constants, elements, helpers, resources, state, timers } = runtime;
    const { ACTIVITY_LOG_LEVELS, BUILT_IN_PROVIDER_NAMES, DEFAULT_IMPORT_JOB_STATUSES, SECRET_METADATA } = constants;
    const { escapeHtml, formatDateTime, iconButton, iconSvg, notifyConfigChanged, safeJson, setIconButtonLabel, setStatus, withRestartNotice } = helpers;

    function updateImportOcrAvailability() {
        if (!elements.importPdfModeSelect || !elements.importPdfStrategySelect) return;
        const capability = window.App?.metadata?.ingestion_capabilities?.pdf_ocr;
        const isPageImagesMode = (elements.importPdfModeSelect?.value || 'markdown') === 'page_images';
        const selectedStrategy = elements.importPdfStrategySelect.value || 'default';
        const defaultOrder = Array.isArray(capability?.default_order)
            ? capability.default_order.map(value => String(value))
            : [];
        const defaultIncludesOcr = defaultOrder.includes('pdf_ocr');
        const selectedPathUsesOcr = selectedStrategy === 'ocr'
            || (selectedStrategy === 'default' && defaultIncludesOcr);
        const missingRequirements = Array.isArray(capability?.missing)
            ? capability.missing.map(value => String(value))
            : ['OCR capability metadata'];
        const ocrAvailable = capability?.available === true;
        const disableEnrichments = !ocrAvailable || !selectedPathUsesOcr || isPageImagesMode;
        const disabledReason = !ocrAvailable
            ? `OCR unavailable: configure ${missingRequirements.join(', ')}`
            : 'Select a conversion strategy that can invoke Mistral OCR.';
        const enrichmentControls = [
            elements.importIncludeOcrBlocksCheckbox,
            elements.importExtractOcrHeaderCheckbox,
            elements.importExtractOcrFooterCheckbox,
            elements.importOcrTableFormatSelect,
            elements.importOcrConfidenceSelect
        ].filter(Boolean);

        elements.importMarkdownOptions?.classList.toggle('hidden', isPageImagesMode);
        elements.importPageImageOptions?.classList.toggle('hidden', !isPageImagesMode);
        elements.importPdfOcrEnrichments?.classList.toggle('hidden', !selectedPathUsesOcr);
        const ocrOption = elements.importPdfStrategySelect.querySelector('option[value="ocr"]');
        if (ocrOption instanceof HTMLOptionElement) ocrOption.disabled = !ocrAvailable;
        if (elements.importCaptureOcrImagesCheckbox) {
            elements.importCaptureOcrImagesCheckbox.disabled = !ocrAvailable || isPageImagesMode;
        }
        enrichmentControls.forEach(control => { control.disabled = disableEnrichments; });
        if (disableEnrichments) {
            enrichmentControls.forEach(control => { control.title = disabledReason; });
            if (!ocrAvailable && elements.importCaptureOcrImagesCheckbox) {
                elements.importCaptureOcrImagesCheckbox.title = disabledReason;
            }
        } else {
            enrichmentControls.forEach(control => { control.removeAttribute('title'); });
            elements.importCaptureOcrImagesCheckbox?.removeAttribute('title');
        }
        if (disableEnrichments) {
            if ((!ocrAvailable || isPageImagesMode) && elements.importCaptureOcrImagesCheckbox) {
                elements.importCaptureOcrImagesCheckbox.checked = false;
            }
            enrichmentControls.forEach(control => {
                if (control instanceof HTMLInputElement) control.checked = false;
                if (control instanceof HTMLSelectElement) control.value = '';
            });
        }
        if (elements.importPdfStrategyHelp) {
            const orderLabel = defaultOrder.length ? defaultOrder.join(' → ') : 'no strategies configured';
            if (selectedStrategy === 'local_text') {
                elements.importPdfStrategyHelp.textContent = 'Uses local PDF text extraction only. PDF OCR enrichments do not apply.';
            } else if (selectedStrategy === 'ocr') {
                elements.importPdfStrategyHelp.textContent = ocrAvailable
                    ? 'Uses Mistral OCR only. OCR enrichment options apply to this import.'
                    : disabledReason;
            } else {
                const ocrIndex = defaultOrder.indexOf('pdf_ocr');
                elements.importPdfStrategyHelp.textContent = ocrIndex < 0
                    ? `Configured default: ${orderLabel}. PDF OCR enrichments do not apply.`
                    : ocrIndex === 0
                        ? `Configured default: ${orderLabel}. OCR runs first, so enrichment options apply.`
                        : `Configured default: ${orderLabel}. Enrichment options apply only if earlier strategies fall through to OCR.`;
            }
            const defaultOption = elements.importPdfStrategySelect.querySelector('option[value="default"]');
            if (defaultOption instanceof HTMLOptionElement) {
                defaultOption.textContent = `Configured default (${orderLabel})`;
            }
        }
    }


    function renderImportVaults() {
        const select = elements.importVaultSelect;
        if (!select) return;
        const selectedVault = select.value;
        select.innerHTML = '<option value="">Select vault…</option>';
        if (!state.importVaults || state.importVaults.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'No vaults detected';
            opt.disabled = true;
            select.appendChild(opt);
            return;
        }
        state.importVaults.forEach((vault) => {
            const opt = document.createElement('option');
            opt.value = vault;
            opt.textContent = vault;
            select.appendChild(opt);
        });
        if (state.importVaults.includes(selectedVault)) {
            select.value = selectedVault;
        }
    }

    function handleImportVaultChange() {
        state.importResults = null;
        state.importUrlResult = null;
        renderImportResults();
        if (state.isLoadingImportJobs) {
            state.pendingImportJobsReload = true;
        } else {
            loadImportJobs();
        }
    }

    async function loadImportVaults(force = false) {
        if (state.isLoadingImportVaults) return;
        state.isLoadingImportVaults = true;
        setStatus(elements.importStatus, 'Loading vaults…', 'info');

        // Prefer cached metadata from main app if available
        const cachedVaults = window.App && window.App.metadata && Array.isArray(window.App.metadata.vaults)
            ? window.App.metadata.vaults
            : null;
        if (cachedVaults && !force) {
            state.importVaults = cachedVaults;
            renderImportVaults();
            setStatus(elements.importStatus, '', 'info');
        }

        try {
            // Always refresh from API to stay current
            const response = await fetch('api/metadata');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            state.importVaults = Array.isArray(data?.vaults) ? data.vaults : [];
            renderImportVaults();
            setStatus(elements.importStatus, '', 'info');
            // Keep cache in sync for other modules
            window.App = window.App || {};
            window.App.metadata = data;
        } catch (error) {
            if (!cachedVaults) {
                state.importVaults = [];
                renderImportVaults();
            }
            setStatus(elements.importStatus, `Failed to load vaults: ${error.message}`, 'error');
        } finally {
            state.isLoadingImportVaults = false;
        }
    }

    async function handleImportVaultRescan() {
        if (!elements.importRefreshVaultsBtn || state.isLoadingImportVaults) return;
        const btn = elements.importRefreshVaultsBtn;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Refresh Vaults';
        btn.disabled = true;
        setIconButtonLabel(btn, 'Rescanning vaults...');
        setStatus(elements.importStatus, 'Rescanning vaults…', 'info');
        try {
            const response = await fetch('api/vaults/rescan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            // Refresh global status/metadata if caller provided callbacks
            if (callbacks.refreshStatus) await callbacks.refreshStatus();
            if (callbacks.refreshMetadata) await callbacks.refreshMetadata();
            // Reload vault list for import select
            await loadImportVaults(true);
            setStatus(elements.importStatus, 'Vaults refreshed.', 'success');
        } catch (error) {
            setStatus(elements.importStatus, `Rescan failed: ${error.message}`, 'error');
        } finally {
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
        }
    }

    function normalizeOutputPaths(outputs) {
        if (!Array.isArray(outputs)) return [];
        const seen = new Set();
        const normalized = [];
        outputs.forEach((value) => {
            const path = String(value || '').trim();
            if (!path || seen.has(path)) return;
            seen.add(path);
            normalized.push(path);
        });
        return normalized;
    }

    function summarizeImportOutputs(outputs) {
        const paths = normalizeOutputPaths(outputs);
        if (!paths.length) {
            return {
                destinationLabel: 'No output files',
                filesLabel: '0 files',
            };
        }

        const directorySegments = paths.map((path) => {
            const slashIndex = path.lastIndexOf('/');
            const folder = slashIndex >= 0 ? path.slice(0, slashIndex) : '';
            return folder ? folder.split('/').filter(Boolean) : [];
        });

        let commonDir = directorySegments[0] ? directorySegments[0].slice() : [];
        for (let i = 1; i < directorySegments.length; i += 1) {
            const current = directorySegments[i];
            let prefixLen = 0;
            while (
                prefixLen < commonDir.length &&
                prefixLen < current.length &&
                commonDir[prefixLen] === current[prefixLen]
            ) {
                prefixLen += 1;
            }
            commonDir = commonDir.slice(0, prefixLen);
            if (!commonDir.length) break;
        }

        // Collapse companion assets to a useful import destination summary.
        const destinationSegments = (
            commonDir.length >= 2 && commonDir[0] === 'Imported'
                ? commonDir.slice(0, 2)
                : commonDir
        );
        const destinationLabel = destinationSegments.length
            ? destinationSegments.join('/')
            : '(vault root)';

        const extensionCounts = new Map();
        paths.forEach((path) => {
            const slashIndex = path.lastIndexOf('/');
            const filename = slashIndex >= 0 ? path.slice(slashIndex + 1) : path;
            const dotIndex = filename.lastIndexOf('.');
            const extension = dotIndex > 0
                ? filename.slice(dotIndex + 1).toLowerCase()
                : 'no-ext';
            extensionCounts.set(extension, (extensionCounts.get(extension) || 0) + 1);
        });

        const typeParts = Array.from(extensionCounts.entries())
            .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
            .map(([ext, count]) => `${count} ${ext}`);

        const filesLabel = `${paths.length} file${paths.length === 1 ? '' : 's'} (${typeParts.join(', ')})`;

        return { destinationLabel, filesLabel };
    }

    function renderImportOutputLinks(outputs, vault, { compact = false } = {}) {
        const paths = normalizeOutputPaths(outputs);
        if (!paths.length) return '<span class="subtle">No output files</span>';
        const listClass = compact ? 'space-y-1' : 'space-y-1 ml-4';
        return `
            <ul class="${listClass}">
                ${paths.map((path) => `
                    <li class="break-words">
                        <button
                            type="button"
                            class="vault-file-link vault-file-link-code text-left break-all"
                            data-import-output-path="${escapeHtml(path)}"
                            data-import-output-vault="${escapeHtml(vault || '')}"
                            title="Open ${escapeHtml(path)} in the vault viewer"
                        >${escapeHtml(path)}</button>
                    </li>
                `).join('')}
            </ul>
        `;
    }

    function renderImportSource(source) {
        const value = String(source || 'unknown');
        let isWebUrl = false;
        try {
            const parsed = new URL(value);
            isWebUrl = parsed.protocol === 'http:' || parsed.protocol === 'https:';
        } catch (_error) {
            // Local inbox paths are expected and remain plain text.
        }
        if (!isWebUrl) return escapeHtml(value);
        return `<a class="vault-file-link break-all" href="${escapeHtml(value)}" target="_blank" rel="noopener noreferrer">${escapeHtml(value)}</a>`;
    }

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
                                : renderImportOutputLinks(outputs, job.vault, { compact: true });
                            const strategy = [
                                job.selected_strategy,
                                job.selected_provider,
                                job.selected_model
                            ].filter(Boolean).join(' · ') || '—';
                            const cancelButton = job.status === 'queued'
                                ? `<button data-import-job-cancel="${escapeHtml(job.id)}" ${iconButton('x', `Cancel import job ${job.id}`, 'is-danger')}>${iconSvg('x')}</button>`
                                : '';
                            const editButton = job.source_type === 'url'
                                ? `<button data-import-job-edit="${escapeHtml(job.id)}" ${iconButton('edit', `Edit import settings for job ${job.id}`, 'is-primary')}>${iconSvg('edit')}</button>`
                                : '';
                            return `
                                <tr>
                                    <td data-label="Job" class="cell-xs cell-mono import-job-compact">${escapeHtml(job.id)}</td>
                                    <td data-label="Source" class="cell-xs import-job-source">${renderImportSource(job.source_uri)}</td>
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
        const button = target.closest('[data-import-job-cancel], [data-import-job-edit]');
        if (!(button instanceof HTMLElement) || button.disabled) return;
        const editJobId = button.getAttribute('data-import-job-edit');
        if (editJobId) {
            const job = state.importJobs.find(item => String(item.id) === editJobId);
            if (!job || job.source_type !== 'url') return;
            if (elements.importVaultSelect) elements.importVaultSelect.value = job.vault || '';
            if (elements.importPdfModeSelect && elements.importPdfStrategySelect) {
                if (job.selected_strategy === 'pdf_page_images') {
                    elements.importPdfModeSelect.value = 'page_images';
                } else {
                    elements.importPdfModeSelect.value = 'markdown';
                    if (job.selected_strategy === 'pdf_ocr') {
                        elements.importPdfStrategySelect.value = 'ocr';
                    } else if (job.selected_strategy === 'pdf_text') {
                        elements.importPdfStrategySelect.value = 'local_text';
                    } else {
                        elements.importPdfStrategySelect.value = 'default';
                    }
                }
                updateImportOcrAvailability();
            }
            if (elements.importUrlInput) {
                elements.importUrlInput.value = job.source_uri || '';
                elements.importUrlInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
                elements.importUrlInput.focus({ preventScroll: true });
            }
            setStatus(
                elements.importStatus,
                `Loaded URL from job ${editJobId}. Adjust PDF/OCR settings and import again.`,
                'info'
            );
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

    function renderImportResults() {
        if (!elements.importResults) return;
        const results = state.importResults;
        const urlResult = state.importUrlResult;

        let sections = [];

        if (results) {
            const jobs = Array.isArray(results.jobs_created) ? results.jobs_created : [];
            const skipped = Array.isArray(results.skipped) ? results.skipped : [];
            let html = '';
            if (jobs.length > 0) {
                html += `<div class="space-y-2"><div class="font-medium text-txt-primary">File imports (${jobs.length})</div>`;
                html += '<ul class="list-disc list-inside space-y-1">';
                html += jobs
                    .map((job) => {
                        const outputSummary = summarizeImportOutputs(job.outputs);
                        const status = job.status || 'queued';
                        const source = job.source_uri || 'unknown';
                        return `
                            <li>
                                <span class="text-txt-primary font-medium">${renderImportSource(source)}</span>
                                <span class="subtle">(${escapeHtml(status)})</span>
                                <div class="text-xs text-txt-secondary ml-4">
                                    Destination: <span class="text-txt-primary">${escapeHtml(outputSummary.destinationLabel)}</span>
                                </div>
                                <div class="text-xs text-txt-secondary ml-4">
                                    Files: ${escapeHtml(outputSummary.filesLabel)}
                                </div>
                                <div class="text-xs text-txt-secondary ml-4">Import path:</div>
                                ${renderImportOutputLinks(job.outputs, job.vault)}
                            </li>
                        `;
                    })
                    .join('');
                html += '</ul></div>';
            }
            if (skipped.length > 0) {
                html += `<div class="space-y-2 pt-3 border-t border-border-primary"><div class="font-medium text-txt-primary">Skipped (${skipped.length})</div>`;
                html += '<ul class="list-disc list-inside space-y-1">';
                html += skipped.map((name) => `<li>${escapeHtml(name)}</li>`).join('');
                html += '</ul></div>';
            }
            if (html) sections.push(html);
        }

        if (urlResult) {
            const outputSummary = summarizeImportOutputs(urlResult.outputs);
            const status = urlResult.status || 'unknown';
            const error = urlResult.error;
            const source = urlResult.source_uri || urlResult.url || 'unknown';

            let html = `<div class="space-y-2"><div class="font-medium text-txt-primary">Latest URL import</div>`;
            html += `<div class="text-sm"><span class="text-txt-primary font-medium">${renderImportSource(source)}</span> <span class="subtle">(${escapeHtml(status)})</span></div>`;
            html += `<div class="text-sm text-txt-secondary">Destination: <span class="text-txt-primary">${escapeHtml(outputSummary.destinationLabel)}</span></div>`;
            html += `<div class="text-sm text-txt-secondary">Files: ${escapeHtml(outputSummary.filesLabel)}</div>`;
            html += '<div class="text-sm text-txt-secondary">Import path:</div>';
            html += renderImportOutputLinks(urlResult.outputs, urlResult.vault);
            if (error) {
                html += `<div class="text-sm state-error">Error: ${escapeHtml(error)}</div>`;
            }
            html += '</div>';
            sections.push(html);
        }

        elements.importResults.innerHTML = sections.join('<div class="pt-3 border-t border-border-primary"></div>') || 'No imports yet.';
    }

    async function handleImportScan() {
        if (!elements.importScanBtn || state.isScanningImport) return;

        const vault = elements.importVaultSelect?.value || '';
        if (!vault) {
            setStatus(elements.importStatus, 'Select a vault before scanning.', 'warning');
            return;
        }

        const queueOnly = Boolean(elements.importQueueCheckbox?.checked);
        const captureOcrImages = Boolean(elements.importCaptureOcrImagesCheckbox?.checked);
        const pdfMode = (elements.importPdfModeSelect?.value || 'markdown').trim();
        const pdfStrategies = selectedPdfStrategyOverride();

        const payload = { vault, queue_only: queueOnly };
        if (pdfMode === 'page_images') {
            payload.pdf_mode = 'page_images';
        }
        if (pdfStrategies) payload.strategies = pdfStrategies;
        if (captureOcrImages) {
            payload.capture_ocr_images = true;
        }
        addOcrEnrichmentOptions(payload);

        state.isScanningImport = true;
        // Reset URL status when starting a file import
        state.importUrlResult = null;
        renderImportResults();
        const btn = elements.importScanBtn;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Import Files';
        btn.disabled = true;
        setIconButtonLabel(btn, queueOnly ? 'Queueing import jobs...' : 'Importing files...');
        setStatus(
            elements.importStatus,
            queueOnly ? 'Queueing import jobs…' : 'Scanning import folder and processing…',
            'info'
        );

        try {
            const response = await fetch('api/import/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            state.importResults = data;
            renderImportResults();
            await loadImportJobs({ silent: true });
            setStatus(
                elements.importStatus,
                queueOnly ? 'Jobs queued.' : 'Import completed.',
                'success'
            );
            if (callbacks.refreshStatus) callbacks.refreshStatus();
        } catch (error) {
            setStatus(elements.importStatus, `Import failed: ${error.message}`, 'error');
        } finally {
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
            state.isScanningImport = false;
        }
    }

    async function handleImportUrl() {
        if (!elements.importUrlSubmit || state.isImportingUrl) return;

        const vault = elements.importVaultSelect?.value || '';
        const url = (elements.importUrlInput?.value || '').trim();
        if (!vault || !url) {
            setStatus(elements.importStatus, 'Select a vault and enter a URL.', 'warning');
            return;
        }

        // Reset file status when starting a URL import
        state.importResults = null;
        renderImportResults();
        state.isImportingUrl = true;
        const btn = elements.importUrlSubmit;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Import URL';
        btn.disabled = true;
        setIconButtonLabel(btn, 'Ingesting URL...');
        setStatus(elements.importStatus, 'Ingesting URL…', 'info');

        const captureOcrImages = Boolean(elements.importCaptureOcrImagesCheckbox?.checked);
        const pdfMode = (elements.importPdfModeSelect?.value || 'markdown').trim();
        const pdfStrategies = selectedPdfStrategyOverride();
        const payload = { vault, url, clean_html: true, pdf_mode: pdfMode };
        if (pdfStrategies) payload.pdf_strategies = pdfStrategies;
        if (captureOcrImages) payload.capture_ocr_images = true;
        addOcrEnrichmentOptions(payload);

        try {
            const response = await fetch('api/import/url', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            state.importUrlResult = data;
            await loadImportJobs({ silent: true });
            setStatus(elements.importStatus, 'URL ingested.', data.error ? 'warning' : 'success');
            if (callbacks.refreshStatus) callbacks.refreshStatus();
            renderImportResults();
        } catch (error) {
            setStatus(elements.importStatus, `Import failed: ${error.message}`, 'error');
        } finally {
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
            state.isImportingUrl = false;
        }
    }

    function addOcrEnrichmentOptions(payload) {
        if (elements.importIncludeOcrBlocksCheckbox?.checked) payload.include_ocr_blocks = true;
        if (elements.importExtractOcrHeaderCheckbox?.checked) payload.extract_ocr_header = true;
        if (elements.importExtractOcrFooterCheckbox?.checked) payload.extract_ocr_footer = true;
        const tableFormat = (elements.importOcrTableFormatSelect?.value || '').trim();
        const confidence = (elements.importOcrConfidenceSelect?.value || '').trim();
        if (tableFormat) payload.ocr_table_format = tableFormat;
        if (confidence) payload.ocr_confidence = confidence;
    }

    function selectedPdfStrategyOverride() {
        const selected = elements.importPdfStrategySelect?.value || 'default';
        if (selected === 'local_text') return ['pdf_text'];
        if (selected === 'ocr') return ['pdf_ocr'];
        return null;
    }


    Object.assign(actions, {
        updateImportOcrAvailability,
        renderImportVaults,
        handleImportVaultChange,
        loadImportVaults,
        handleImportVaultRescan,
        normalizeOutputPaths,
        summarizeImportOutputs,
        renderImportOutputLinks,
        renderImportSource,
        renderImportJobs,
        selectedImportJobStatuses,
        handleImportJobFilterChange,
        syncImportJobPolling,
        loadImportJobs,
        handleImportJobAction,
        handleRunImportQueueNow,
        renderImportResults,
        handleImportScan,
        handleImportUrl,
        addOcrEnrichmentOptions,
        selectedPdfStrategyOverride
    });
}(window, document));
