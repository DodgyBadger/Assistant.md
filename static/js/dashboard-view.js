(function dashboardViewModule(window) {
    function createDashboardViewController({ state, elements, utils, callbacks }) {
        const { escapeHtml, formatShortDate } = utils;
        const icons = window.AssistantMDIcons;

        // Display system status information
        function displaySystemStatus() {
            const status = state.systemStatus;
            if (!status) return;
            renderDashboardVaults(status);
            renderDashboardExecutionTasks();
            renderDashboardWorkflows(status);
            renderDashboardVaultActivity(status);
        }

        function renderDashboardVaults(status) {
            if (!elements.systemStatus) return;
            const sortedVaults = sortDashboardVaults(status.vaults || []);

            elements.systemStatus.innerHTML = `
                <div class="dashboard-table-wrap" role="region" aria-label="Vaults" tabindex="0">
                    <table class="dashboard-table">
                        <thead>
                            <tr>
                                ${renderDashboardVaultSortHeader('name', 'Name')}
                                ${renderDashboardVaultSortHeader('path', 'Path inside container')}
                                ${renderDashboardVaultSortHeader('workflows', 'Workflows', 'cell-center')}
                                ${renderDashboardVaultSortHeader('files', 'Files', 'cell-center')}
                                ${renderDashboardVaultSortHeader('file_delta', '+/- 7d', 'cell-center')}
                                ${renderDashboardVaultSortHeader('latest_change', 'Latest Change')}
                            </tr>
                        </thead>
                        <tbody>
                            ${sortedVaults.map(v => `
                                <tr>
                                    <td><strong>${escapeHtml(v.name)}</strong></td>
                                    <td class="cell-mono cell-xs subtle">${escapeHtml(v.path)}</td>
                                    <td class="cell-center">${v.workflow_count}</td>
                                    <td class="cell-center">${v.tracked_files ?? '—'}</td>
                                    <td class="cell-center">${formatVaultFileDelta(v)}</td>
                                    <td class="cell-xs">${formatShortDate(v.latest_vault_change_at)}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            `;
        }

        function renderDashboardWorkflows(status) {
            if (!elements.workflowsStatus) return;
            const enabledWorkflows = status.enabled_workflows || [];
            const disabledWorkflows = status.disabled_workflows || [];
            const systemWorkflowTemplates = status.system_workflow_templates || [];
            const templateWorkflows = systemWorkflowTemplates.map(template => ({
                global_id: `system/${template.name}`,
                name: template.name,
                vault: 'system',
                enabled: Boolean(template.enabled),
                run_type: template.run_type || 'workflow',
                schedule_cron: template.schedule_cron || '',
                description: template.description || '',
                is_system_template: true
            }));
            const combinedWorkflows = [...enabledWorkflows, ...disabledWorkflows, ...templateWorkflows];
            const schedulerJobs = status.scheduler?.job_details || [];
            const runByWorkflowId = new Map(Object.entries(status.workflow_runs || {}));
            const schedulerRunning = Boolean(status.scheduler?.running);
            const jobByWorkflowId = new Map(
                schedulerJobs.map(job => [job.id.replace('__', '/'), job])
            );
            const schedulerBadge = schedulerRunning
                ? '<span class="badge badge-scheduler-running">SCHEDULER RUNNING</span>'
                : '<span class="badge badge-scheduler-stopped">SCHEDULER STOPPED</span>';
            if (elements.workflowSchedulerBadge) {
                elements.workflowSchedulerBadge.innerHTML = schedulerBadge;
            }
            const workflowLoadErrors = renderWorkflowLoadErrors(
                status,
                combinedWorkflows
            );
            if (combinedWorkflows.length === 0) {
                elements.workflowsStatus.innerHTML = `
                    ${renderDashboardBadgeStyles()}
                    ${workflowLoadErrors}
                    <p class="text-sm text-txt-secondary">No workflows loaded.</p>
                `;
                return;
            }
            const sortedWorkflows = sortDashboardWorkflows(
                combinedWorkflows,
                jobByWorkflowId,
                runByWorkflowId
            );
            elements.workflowsStatus.innerHTML = `
                ${renderDashboardBadgeStyles()}
                ${workflowLoadErrors}
                <div class="dashboard-table-wrap" role="region" aria-label="Workflows" tabindex="0">
                    <table class="dashboard-table">
                        <thead>
                            <tr>
                                ${renderDashboardWorkflowSortHeader('id', 'ID')}
                                ${renderDashboardWorkflowSortHeader('status', 'Status')}
                                <th>Last Result</th>
                                ${renderDashboardWorkflowSortHeader('last_run', 'Last Run')}
                                ${renderDashboardWorkflowSortHeader('next_run', 'Next Run')}
                                <th>Description</th>
                                <th class="cell-center" aria-label="Run"></th>
                            </tr>
                        </thead>
                        <tbody>
                            ${sortedWorkflows.map(workflow => {
                                const job = workflow.is_system_template
                                    ? dashboardSystemWorkflowTemplateJob(workflow, schedulerJobs)
                                    : jobByWorkflowId.get(workflow.global_id);
                                const latestRun = runByWorkflowId.get(workflow.global_id);
                                const nextRun = job?.next_run_time
                                    ? new Date(job.next_run_time).toLocaleString('en-US', {
                                        month: 'short',
                                        day: 'numeric',
                                        hour: 'numeric',
                                        minute: '2-digit'
                                    })
                                    : '—';
                                const lastRun = latestRun?.completed_at
                                    ? new Date(latestRun.completed_at).toLocaleString('en-US', {
                                        month: 'short',
                                        day: 'numeric',
                                        hour: 'numeric',
                                        minute: '2-digit'
                                    })
                                    : '—';
                                const description = workflow.description || '—';
                                const { statusLabel, statusClass } = dashboardWorkflowStatus(workflow, job);
                                const toggleLabel = workflow.enabled ? 'Disable workflow' : 'Enable workflow';
                                const nextEnabled = workflow.enabled ? 'false' : 'true';
                                const statusButton = `
                                    <button
                                        type="button"
                                        class="badge ${statusClass}"
                                        data-dashboard-workflow-toggle="${escapeHtml(workflow.global_id)}"
                                        data-dashboard-workflow-enabled="${nextEnabled}"
                                        title="${toggleLabel}"
                                        aria-label="${toggleLabel}"
                                    >
                                        ${statusLabel}
                                    </button>
                                `;
                                const runButton = renderDashboardWorkflowRunButton(workflow);
                                return `
                                    <tr>
                                        <td>
                                            <button
                                                type="button"
                                                class="font-semibold text-accent hover:underline focus:outline-none focus:ring-2 focus:ring-accent rounded-sm text-left"
                                                data-dashboard-workflow-edit="${escapeHtml(workflow.global_id)}"
                                            >
                                                ${escapeHtml(workflow.global_id)}
                                            </button>
                                        </td>
                                        <td>${statusButton}</td>
                                        <td>${renderWorkflowResultBadge(workflow.global_id, latestRun)}</td>
                                        <td class="cell-xs">${lastRun}</td>
                                        <td class="cell-xs">${nextRun}</td>
                                        <td class="cell-xs subtle">${escapeHtml(description)}</td>
                                        <td class="cell-center">${runButton}</td>
                                    </tr>
                                `;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            `;
        }

        function renderDashboardExecutionTasks() {
            if (!elements.executionTasksStatus) return;
            elements.executionTasksStatus.innerHTML = `
                ${renderDashboardBadgeStyles()}
                ${renderInFlightTasks()}
            `;
        }

        function renderDashboardVaultActivity(status) {
            if (!elements.vaultActivityStatus) return;
            const activityVaults = status.vaults || [];
            const selectedActivityVault = state.selectedActivityVault && activityVaults.some(v => v.name === state.selectedActivityVault)
                ? state.selectedActivityVault
                : activityVaults[0]?.name || '';
            state.selectedActivityVault = selectedActivityVault;

            elements.vaultActivityStatus.innerHTML = `
                <div class="flex flex-col md:flex-row md:items-end gap-3">
                    <div class="flex-1 min-w-0">
                        <select id="vault-activity-selector" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent bg-app-bg text-txt-primary">
                            ${activityVaults.length ? activityVaults.map(v => `
                                <option value="${escapeHtml(v.name)}" ${v.name === selectedActivityVault ? 'selected' : ''}>${escapeHtml(v.name)}</option>
                            `).join('') : '<option value="">No vaults detected</option>'}
                        </select>
                    </div>
                    <button id="vault-activity-refresh" type="button" class="ui-icon-button self-start md:self-auto" aria-label="Refresh Activity" title="Refresh Activity">
                        ${icons.REFRESH_ICON_SVG}
                    </button>
                </div>
                <div id="vault-activity-result" class="mt-3">
                    ${callbacks.renderVaultActivityResult(selectedActivityVault)}
                </div>
            `;

            if (selectedActivityVault && !state.vaultActivity[selectedActivityVault]) {
                callbacks.loadVaultActivity(selectedActivityVault);
            }
        }

        function renderDashboardBadgeStyles() {
            return `
                <style>
                    .badge { display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
                    button.badge { cursor: pointer; border: 1px solid currentColor; line-height: 1.2; transition: filter 120ms ease, box-shadow 120ms ease; }
                    button.badge:hover { filter: brightness(1.06); box-shadow: 0 0 0 2px rgb(var(--accent-primary) / 0.18); }
                    button.badge:focus-visible { outline: 2px solid rgb(var(--accent-primary)); outline-offset: 2px; }
                    button.badge:disabled { cursor: not-allowed; opacity: 0.65; }
                    .badge-scheduler-running { background: rgb(var(--accent-primary)); color: rgb(var(--text-on-accent)); }
                    .badge-scheduler-stopped { background: rgb(var(--state-warning) / 0.2); color: rgb(var(--state-warning)); }
                    .badge-scheduled { background: rgb(var(--accent-primary) / 0.14); color: rgb(var(--accent-primary)); }
                    .badge-enabled { background: rgb(var(--bg-elevated)); color: rgb(var(--text-primary)); }
                    .badge-disabled { background: rgb(var(--text-secondary) / 0.14); color: rgb(var(--text-secondary)); }
                    .badge-result-completed { background: rgb(var(--state-success) / 0.16); color: rgb(var(--state-success)); }
                    .badge-result-skipped { background: rgb(var(--text-secondary) / 0.14); color: rgb(var(--text-secondary)); }
                    .badge-result-failed, .badge-result-timed_out { background: rgb(var(--state-error) / 0.16); color: rgb(var(--state-error)); }
                    .badge-result-missed, .badge-result-cancelled { background: rgb(var(--state-warning) / 0.18); color: rgb(var(--state-warning)); }
                </style>
            `;
        }

        function renderWorkflowLoadErrors(status, workflows) {
            const loadedIds = new Set(workflows.map(workflow => workflow.global_id));
            const loadErrors = (status.configuration_errors || []).filter(error => {
                const path = String(error.file_path || '');
                return path.includes('/AssistantMD/Workflows/') || (
                    error.workflow_name && !loadedIds.has(`${error.vault}/${error.workflow_name}`)
                );
            });
            if (!loadErrors.length) return '';

            const items = loadErrors.map(error => `${error.workflow_name || error.file_path || 'Workflow'}: ${error.error_message || 'Failed to load'}`);
            return `
                <div class="state-surface-error border p-3 mb-3 text-sm">
                    <p class="font-semibold text-txt-primary">Workflows failed to load</p>
                    <ul class="mt-1 space-y-1 text-txt-secondary">
                        ${items.slice(0, 5).map(item => `<li>${escapeHtml(item)}</li>`).join('')}
                    </ul>
                    ${items.length > 5 ? `<p class="mt-1 text-xs text-txt-secondary">${items.length - 5} more issue(s)</p>` : ''}
                </div>
            `;
        }

        function renderWorkflowResultBadge(globalId, run) {
            if (!run) return '<span class="subtle">—</span>';
            const status = String(run.status || 'unknown').toLowerCase();
            const label = status.replaceAll('_', ' ');
            const title = run.reason || run.message || `View ${globalId} run history`;
            return `
                <button
                    type="button"
                    class="badge badge-result-${escapeHtml(status)}"
                    data-dashboard-workflow-history="${escapeHtml(globalId)}"
                    title="${escapeHtml(title)}"
                    aria-label="View run history for ${escapeHtml(globalId)}"
                >${escapeHtml(label)}</button>
            `;
        }

        function renderDashboardVaultSortHeader(column, label, className = '') {
            const sort = state.dashboardVaultSort || { column: 'name', direction: 'asc' };
            const active = sort.column === column;
            const indicator = active ? (sort.direction === 'asc' ? '▲' : '▼') : '↕';
            const nextDirection = active && sort.direction === 'asc' ? 'desc' : 'asc';
            return `
                <th class="${className}" aria-sort="${active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}">
                    <button
                        type="button"
                        class="inline-flex items-center gap-1 text-left font-semibold whitespace-nowrap hover:text-accent focus:outline-none focus:ring-2 focus:ring-accent rounded-sm"
                        data-dashboard-vault-sort="${column}"
                        data-dashboard-vault-sort-next="${nextDirection}"
                    >
                        <span>${escapeHtml(label)}</span>
                        <span class="cell-xs subtle" aria-hidden="true">${indicator}</span>
                    </button>
                </th>
            `;
        }

        function renderDashboardWorkflowSortHeader(column, label) {
            const sort = state.dashboardWorkflowSort || { column: 'id', direction: 'asc' };
            const active = sort.column === column;
            const indicator = active ? (sort.direction === 'asc' ? '▲' : '▼') : '↕';
            const nextDirection = active && sort.direction === 'asc' ? 'desc' : 'asc';
            return `
                <th aria-sort="${active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}">
                    <button
                        type="button"
                        class="inline-flex items-center gap-1 text-left font-semibold whitespace-nowrap hover:text-accent focus:outline-none focus:ring-2 focus:ring-accent rounded-sm"
                        data-dashboard-workflow-sort="${column}"
                        data-dashboard-workflow-sort-next="${nextDirection}"
                    >
                        <span>${escapeHtml(label)}</span>
                        <span class="cell-xs subtle" aria-hidden="true">${indicator}</span>
                    </button>
                </th>
            `;
        }

        function sortDashboardVaults(vaults) {
            const sort = state.dashboardVaultSort || { column: 'name', direction: 'asc' };
            const direction = sort.direction === 'asc' ? 1 : -1;
            return [...vaults].sort((a, b) => {
                const compared = compareDashboardVaults(a, b, sort.column);
                if (compared !== 0) return compared * direction;
                return String(a.name || '').localeCompare(String(b.name || ''));
            });
        }

        function compareDashboardVaults(a, b, column) {
            if (column === 'path') {
                return String(a.path || '').localeCompare(String(b.path || ''));
            }
            if (column === 'workflows') {
                return (Number(a.workflow_count) || 0) - (Number(b.workflow_count) || 0);
            }
            if (column === 'files') {
                return compareNullableNumbers(a.tracked_files, b.tracked_files);
            }
            if (column === 'file_delta') {
                return compareVaultFileDelta(a, b);
            }
            if (column === 'latest_change') {
                return compareOptionalDates(a.latest_vault_change_at, b.latest_vault_change_at);
            }
            return String(a.name || '').localeCompare(String(b.name || ''));
        }

        function formatVaultFileDelta(vault) {
            const created = Number(vault?.files_created_recent) || 0;
            const deleted = Number(vault?.files_deleted_recent) || 0;
            if (created === 0 && deleted === 0) {
                return '<span class="subtle">0</span>';
            }
            return `<span class="text-state-success">+${created}</span> <span class="text-txt-secondary">/</span> <span class="text-state-error">-${deleted}</span>`;
        }

        function compareVaultFileDelta(a, b) {
            const aCreated = Number(a?.files_created_recent) || 0;
            const aDeleted = Number(a?.files_deleted_recent) || 0;
            const bCreated = Number(b?.files_created_recent) || 0;
            const bDeleted = Number(b?.files_deleted_recent) || 0;
            const netCompared = (aCreated - aDeleted) - (bCreated - bDeleted);
            if (netCompared !== 0) return netCompared;
            return (aCreated + aDeleted) - (bCreated + bDeleted);
        }

        function compareNullableNumbers(a, b) {
            const aMissing = a === null || a === undefined || Number.isNaN(Number(a));
            const bMissing = b === null || b === undefined || Number.isNaN(Number(b));
            if (aMissing && bMissing) return 0;
            if (aMissing) return 1;
            if (bMissing) return -1;
            return Number(a) - Number(b);
        }

        function sortDashboardWorkflows(workflows, jobByWorkflowId, runByWorkflowId) {
            const sort = state.dashboardWorkflowSort || { column: 'id', direction: 'asc' };
            const direction = sort.direction === 'asc' ? 1 : -1;
            return [...workflows].sort((a, b) => {
                const compared = compareDashboardWorkflows(
                    a,
                    b,
                    jobByWorkflowId,
                    runByWorkflowId,
                    sort.column
                );
                if (compared !== 0) return compared * direction;
                return String(a.global_id || '').localeCompare(String(b.global_id || ''));
            });
        }

        function compareDashboardWorkflows(a, b, jobByWorkflowId, runByWorkflowId, column) {
            const aJob = jobByWorkflowId.get(a.global_id);
            const bJob = jobByWorkflowId.get(b.global_id);
            if (column === 'status') {
                return dashboardWorkflowStatus(a, aJob).statusLabel.localeCompare(
                    dashboardWorkflowStatus(b, bJob).statusLabel
                );
            }
            if (column === 'last_run') {
                return compareOptionalDates(
                    runByWorkflowId.get(a.global_id)?.completed_at,
                    runByWorkflowId.get(b.global_id)?.completed_at
                );
            }
            if (column === 'next_run') {
                return compareOptionalDates(aJob?.next_run_time, bJob?.next_run_time);
            }
            return String(a.global_id || '').localeCompare(String(b.global_id || ''));
        }

        function dashboardWorkflowStatus(workflow, job) {
            if (!workflow.enabled) {
                return { statusLabel: 'disabled', statusClass: 'badge-disabled' };
            }
            if (job) {
                return { statusLabel: 'scheduled', statusClass: 'badge-scheduled' };
            }
            return { statusLabel: 'enabled', statusClass: 'badge-enabled' };
        }

        function renderDashboardWorkflowRunButton(workflow) {
            const systemAttribute = workflow.is_system_template
                ? ' data-dashboard-workflow-system-template="true"'
                : '';
            return `
                <button
                    type="button"
                    class="ui-icon-button is-primary is-compact"
                    data-dashboard-workflow-run="${escapeHtml(workflow.global_id)}"${systemAttribute}
                    aria-label="Run workflow"
                    title="Run workflow"
                >
                    ${icons.PLAY_ICON_SVG}
                </button>
            `;
        }

        function renderInFlightTasks() {
            const tasks = activeExecutionTasks();
            if (!tasks.length) {
                return `
                    <div class="mb-3 rounded-md border border-border-primary bg-app-elevated p-3 text-sm text-txt-secondary">
                        No tasks are currently running.
                    </div>
                `;
            }

            return `
                <div class="rounded-md border border-border-primary bg-app-elevated p-3">
                    <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
                        <div>
                            <p class="font-semibold text-txt-primary">In-flight Tasks</p>
                            <p class="text-xs text-txt-secondary">${tasks.length} active task${tasks.length === 1 ? '' : 's'}</p>
                        </div>
                        <button
                            type="button"
                            class="chat-stop-btn ui-icon-button"
                            data-dashboard-task-stop-all="true"
                            aria-label="Stop all tasks"
                            title="Stop all tasks"
                        >
                            ${icons.STOP_ICON_SVG}
                        </button>
                    </div>
                    <div class="dashboard-table-wrap" role="region" aria-label="In-flight tasks" tabindex="0">
                        <table class="dashboard-table">
                            <thead>
                                <tr>
                                    <th>Task</th>
                                    <th>Scope</th>
                                    <th>Kind</th>
                                    <th>Status</th>
                                    <th>Started</th>
                                    <th>Source</th>
                                    <th class="cell-center" aria-label="Stop"></th>
                                </tr>
                            </thead>
                            <tbody>
                                ${tasks.map(task => `
                                    <tr>
                                        <td class="cell-xs">
                                            <strong>${escapeHtml(executionTaskName(task))}</strong>
                                            <div class="cell-mono subtle">${escapeHtml(task.task_id || '')}</div>
                                        </td>
                                        <td class="cell-xs">${escapeHtml(executionTaskScopeLabel(task))}</td>
                                        <td class="cell-xs">${escapeHtml(task.kind || 'task')}</td>
                                        <td class="cell-xs">${escapeHtml(task.status || 'running')}</td>
                                        <td class="cell-xs">${formatShortDate(task.started_at || task.created_at)}</td>
                                        <td class="cell-xs">${escapeHtml(task.source || 'unknown')}</td>
                                        <td class="cell-center">
                                            <button
                                                type="button"
                                                class="chat-stop-btn ui-icon-button is-compact"
                                                data-dashboard-task-stop="${escapeHtml(task.task_id || '')}"
                                                aria-label="Stop task"
                                                title="Stop task"
                                            >
                                                ${icons.STOP_ICON_SVG}
                                            </button>
                                        </td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        }

        function activeExecutionTasks() {
            return (state.executionTasks || []).filter(task => !callbacks.isTerminalTaskStatus(task.status));
        }

        function executionTaskName(task) {
            return task?.metadata?.workflow_id
                || task?.metadata?.session_id
                || task?.metadata?.activity_label
                || task?.label
                || task?.task_id
                || '';
        }

        function executionTaskScopeLabel(task) {
            const scope = String(task?.scope || '');
            if (scope.startsWith('workflow_vault:')) return scope.slice('workflow_vault:'.length);
            if (scope.startsWith('chat_session:')) return scope.slice('chat_session:'.length);
            if (scope.startsWith('ingestion_vault:')) return scope.slice('ingestion_vault:'.length);
            return scope || '—';
        }

        function syncExecutionTaskPolling() {
            const hasActiveExecutionTasks = activeExecutionTasks().length > 0;
            if (hasActiveExecutionTasks && !state.executionTaskPollTimer) {
                state.executionTaskPollTimer = window.setInterval(() => {
                    callbacks.fetchExecutionTasks({ render: true });
                }, 2000);
            } else if (!hasActiveExecutionTasks && state.executionTaskPollTimer) {
                window.clearInterval(state.executionTaskPollTimer);
                state.executionTaskPollTimer = null;
            }
        }

        function dashboardSystemWorkflowTemplateJob(workflow, schedulerJobs) {
            const templateName = String(workflow?.name || '').replace(/\//g, '__');
            if (!templateName) return null;
            const jobSuffix = `__system__${templateName}`;
            const matchingJobs = schedulerJobs.filter(job => String(job.id || '').endsWith(jobSuffix));
            if (!matchingJobs.length) return null;
            return matchingJobs.reduce((best, job) => {
                const bestTime = Date.parse(best?.next_run_time || '') || Number.POSITIVE_INFINITY;
                const jobTime = Date.parse(job?.next_run_time || '') || Number.POSITIVE_INFINITY;
                return jobTime < bestTime ? job : best;
            }, matchingJobs[0]);
        }

        function compareOptionalDates(a, b) {
            const aTime = Date.parse(a || '') || 0;
            const bTime = Date.parse(b || '') || 0;
            return aTime - bTime;
        }

        function attachEventListeners() {
            if (elements.systemStatus) {
                elements.systemStatus.addEventListener('click', (event) => {
                    const target = event.target;
                    if (!(target instanceof Element)) return;
                    const vaultSortButton = target.closest('[data-dashboard-vault-sort]');
                    if (vaultSortButton instanceof HTMLElement) {
                        state.dashboardVaultSort = {
                            column: vaultSortButton.getAttribute('data-dashboard-vault-sort') || 'name',
                            direction: vaultSortButton.getAttribute('data-dashboard-vault-sort-next') || 'asc'
                        };
                        displaySystemStatus();
                    }
                });
            }

            if (elements.executionTasksStatus) {
                elements.executionTasksStatus.addEventListener('click', (event) => {
                    const target = event.target;
                    if (!(target instanceof Element)) return;
                    const stopButton = target.closest('[data-dashboard-task-stop]');
                    if (stopButton instanceof HTMLElement) {
                        callbacks.stopExecutionTask(
                            stopButton.getAttribute('data-dashboard-task-stop') || '',
                            stopButton
                        );
                        return;
                    }
                    const stopAllButton = target.closest('[data-dashboard-task-stop-all]');
                    if (stopAllButton instanceof HTMLElement) {
                        callbacks.stopAllExecutionTasks(stopAllButton);
                    }
                });
            }

            if (elements.workflowsStatus) {
                elements.workflowsStatus.addEventListener('click', (event) => {
                    const target = event.target;
                    if (!(target instanceof Element)) return;
                    const editButton = target.closest('[data-dashboard-workflow-edit]');
                    if (editButton instanceof HTMLElement) {
                        callbacks.openWorkflowFileEditor(editButton.getAttribute('data-dashboard-workflow-edit') || '');
                        return;
                    }
                    const historyButton = target.closest('[data-dashboard-workflow-history]');
                    if (historyButton instanceof HTMLElement) {
                        callbacks.openWorkflowRunHistory(
                            historyButton.getAttribute('data-dashboard-workflow-history') || ''
                        );
                        return;
                    }
                    const toggleButton = target.closest('[data-dashboard-workflow-toggle]');
                    if (toggleButton instanceof HTMLElement) {
                        callbacks.toggleWorkflowEnabled(
                            toggleButton.getAttribute('data-dashboard-workflow-toggle') || '',
                            toggleButton.getAttribute('data-dashboard-workflow-enabled') === 'true',
                            toggleButton
                        );
                        return;
                    }
                    const runButton = target.closest('[data-dashboard-workflow-run]');
                    if (runButton instanceof HTMLElement) {
                        callbacks.executeWorkflow(
                            runButton.getAttribute('data-dashboard-workflow-run') || '',
                            runButton,
                            runButton.getAttribute('data-dashboard-workflow-system-template') === 'true'
                        );
                        return;
                    }
                    const workflowSortButton = target.closest('[data-dashboard-workflow-sort]');
                    if (workflowSortButton instanceof HTMLElement) {
                        state.dashboardWorkflowSort = {
                            column: workflowSortButton.getAttribute('data-dashboard-workflow-sort') || 'id',
                            direction: workflowSortButton.getAttribute('data-dashboard-workflow-sort-next') || 'asc'
                        };
                        displaySystemStatus();
                    }
                });
            }
        }

        return Object.freeze({
            displaySystemStatus,
            activeExecutionTasks,
            syncExecutionTaskPolling,
            attachEventListeners,
        });
    }

    window.DashboardView = Object.freeze({
        create: createDashboardViewController,
    });
})(window);
