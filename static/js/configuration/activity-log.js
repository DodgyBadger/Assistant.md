(function configurationFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, constants, elements, helpers, resources, state, timers } = runtime;
    const { ACTIVITY_LOG_LEVELS, BUILT_IN_PROVIDER_NAMES, DEFAULT_IMPORT_JOB_STATUSES, SECRET_METADATA } = constants;
    const { escapeHtml, formatDateTime, iconButton, iconSvg, notifyConfigChanged, safeJson, setIconButtonLabel, setStatus, withRestartNotice } = helpers;

    async function refreshActivityLog({ append = false } = {}) {
        if (!elements.activityLogViewer) return;
        if (append && state.isLoadingLog) return;
        if (append && !state.activityLogNextCursor) return;
        if (
            !state.activityLogFilters.levels.length
            || (state.activityLogAvailableTags.length && !state.activityLogFilters.tags.length)
        ) {
            state.activityLogEntries = [];
            state.activityLogNextCursor = null;
            state.activityLogTotalMatching = 0;
            renderActivityLog();
            return;
        }

        if (!append && state.activityLogAbortController) {
            state.activityLogAbortController.abort();
        }
        const requestId = state.activityLogRequestId + 1;
        const abortController = new AbortController();
        state.activityLogRequestId = requestId;
        state.activityLogAbortController = abortController;

        state.isLoadingLog = true;
        const refreshBtn = elements.refreshActivityLogBtn;
        const prevLabel = refreshBtn ? (refreshBtn.dataset.iconLabel || refreshBtn.title || 'Refresh Activity Log') : '';

        if (refreshBtn) {
            refreshBtn.disabled = true;
            setIconButtonLabel(refreshBtn, 'Refreshing activity log...');
        }

        if (!append) {
            elements.activityLogViewer.textContent = 'Loading system log…';
        }

        try {
            const params = new URLSearchParams({ limit: '200' });
            if (append) params.set('cursor', state.activityLogNextCursor);
            state.activityLogFilters.levels.forEach((level) => params.append('level', level));
            state.activityLogFilters.tags.forEach((tag) => params.append('tag', tag));
            if (state.activityLogFilters.query) params.set('search', state.activityLogFilters.query);

            const response = await fetch(`api/system/activity-log?${params.toString()}`, {
                signal: abortController.signal
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            if (requestId !== state.activityLogRequestId) return;
            state.activityLog = data;
            const received = Array.isArray(data.entries)
                ? data.entries.map(normalizeActivityLogEntry)
                : [];
            if (append) {
                const knownIds = new Set(state.activityLogEntries.map((entry) => entry.id).filter(Boolean));
                state.activityLogEntries.push(...received.filter((entry) => !entry.id || !knownIds.has(entry.id)));
            } else {
                state.activityLogEntries = received;
                state.activityLogTotalMatching = Number(data.total_matching || received.length);
            }
            state.activityLogNextCursor = data.next_cursor || null;
            state.activityLogAvailableTags = Array.isArray(data.available_tags) ? data.available_tags : [];
            populateActivityLogFilters();
            renderActivityLog();
        } catch (error) {
            if (error.name !== 'AbortError' && requestId === state.activityLogRequestId) {
                elements.activityLogViewer.textContent = `Failed to load activity log: ${error.message}`;
            }
        } finally {
            if (requestId !== state.activityLogRequestId) return;
            if (refreshBtn) {
                refreshBtn.disabled = false;
                setIconButtonLabel(refreshBtn, prevLabel);
            }
            state.isLoadingLog = false;
            state.activityLogAbortController = null;
        }
    }


    function normalizeActivityLogEntry(record) {
        const data = record && typeof record.data === 'object' && record.data !== null ? record.data : {};
        return {
            id: record.id || '',
            timestamp: record.timestamp || '',
            level: String(record.level || '').toLowerCase(),
            tag: record.tag || '',
            message: record.message || '',
            data,
            bootId: record.boot_id ?? null
        };
    }

    function handleActivityLogFilterChange(event) {
        state.activityLogFilters = {
            query: (elements.activityLogSearch?.value || '').trim().toLowerCase(),
            levels: getCheckedActivityLogFilterValues(elements.activityLogLevelOptions),
            tags: getCheckedActivityLogFilterValues(elements.activityLogTagOptions),
            latestFirst: Boolean(elements.activityLogLatestFirst?.checked)
        };
        updateActivityLogFilterSummaries();
        if (event?.target === elements.activityLogLatestFirst) {
            renderActivityLog();
            return;
        }
        if (!state.activityLogFilters.levels.length || (state.activityLogAvailableTags.length && !state.activityLogFilters.tags.length)) {
            state.activityLogEntries = [];
            state.activityLogNextCursor = null;
            state.activityLogTotalMatching = 0;
            renderActivityLog();
            return;
        }
        refreshActivityLog();
    }

    function handleActivityLogSearchInput() {
        state.activityLogFilters.query = (elements.activityLogSearch?.value || '').trim().toLowerCase();
        if (timers.activityLogSearch) window.clearTimeout(timers.activityLogSearch);
        timers.activityLogSearch = window.setTimeout(() => refreshActivityLog(), 300);
    }

    function populateActivityLogFilters() {
        renderActivityLogLevelOptions();
        renderActivityLogTagOptions();
        updateActivityLogFilterSummaries();
    }

    function renderActivityLogLevelOptions() {
        if (!elements.activityLogLevelOptions) return;

        const levels = ACTIVITY_LOG_LEVELS;
        const selected = new Set(Array.isArray(state.activityLogFilters.levels) ? state.activityLogFilters.levels : levels);
        elements.activityLogLevelOptions.innerHTML = levels
            .map((level) => renderActivityLogCheckbox('level', level, formatActivityLogLevelLabel(level), selected.has(level)))
            .join('');
        state.activityLogFilters.levels = getCheckedActivityLogFilterValues(elements.activityLogLevelOptions);
    }

    function renderActivityLogTagOptions() {
        if (!elements.activityLogTagOptions) return;

        const previousOptions = Array.from(elements.activityLogTagOptions.querySelectorAll('input[type="checkbox"]')).map((input) => input.value);
        const previousSelected = getCheckedActivityLogFilterValues(elements.activityLogTagOptions);
        const previousWasAll = previousOptions.length === 0 || previousSelected.length === previousOptions.length;
        const tags = [...new Set(state.activityLogAvailableTags)].sort();
        const selected = previousWasAll ? new Set(tags) : new Set(previousSelected.filter((tag) => tags.includes(tag)));

        elements.activityLogTagOptions.innerHTML = tags.length
            ? tags.map((tag) => renderActivityLogCheckbox('tag', tag, tag, selected.has(tag))).join('')
            : '<div class="tool-dropdown-menu-header">No tags loaded.</div>';
        state.activityLogFilters.tags = getCheckedActivityLogFilterValues(elements.activityLogTagOptions);
    }

    function renderActivityLogCheckbox(group, value, label, checked) {
        const id = `activity-log-${group}-${value.replace(/[^a-z0-9_-]/gi, '-')}`;
        return `
            <div class="tool-checkbox-wrapper">
                <label for="${escapeHtml(id)}">
                    <input id="${escapeHtml(id)}" type="checkbox" value="${escapeHtml(value)}" ${checked ? 'checked' : ''}>
                    <span class="tool-checkbox-name">${escapeHtml(label)}</span>
                </label>
            </div>
        `;
    }

    function getCheckedActivityLogFilterValues(container) {
        return Array.from(container?.querySelectorAll('input[type="checkbox"]:checked') || [])
            .map((input) => input.value);
    }

    function updateActivityLogFilterSummaries() {
        updateActivityLogFilterSummary(
            elements.activityLogLevelSummary,
            state.activityLogFilters.levels,
            ACTIVITY_LOG_LEVELS,
            'All levels',
            'No levels'
        );
        const allTags = [...new Set(state.activityLogAvailableTags)].sort();
        updateActivityLogFilterSummary(
            elements.activityLogTagSummary,
            state.activityLogFilters.tags,
            allTags,
            'All tags',
            'No tags'
        );
    }

    function updateActivityLogFilterSummary(element, selectedValues, allValues, allLabel, noneLabel) {
        if (!element) return;
        if (!allValues.length || !selectedValues.length) {
            element.textContent = noneLabel;
            return;
        }
        if (selectedValues.length === allValues.length) {
            element.textContent = allLabel;
            return;
        }
        element.textContent = `${selectedValues.length} selected`;
    }

    function formatActivityLogLevelLabel(level) {
        return {
            error: 'Error',
            critical: 'Critical',
            fatal: 'Fatal',
            warning: 'Warning',
            warn: 'Warn',
            info: 'Info',
            debug: 'Debug'
        }[level] || level;
    }

    function setActivityLogFilterMenuOpen(menu, open) {
        state.activityLogFilterMenus[menu] = Boolean(open);
        if (open) {
            const otherMenu = menu === 'level' ? 'tag' : 'level';
            state.activityLogFilterMenus[otherMenu] = false;
            const otherDropdown = otherMenu === 'level' ? elements.activityLogLevelDropdown : elements.activityLogTagDropdown;
            const otherTrigger = otherMenu === 'level' ? elements.activityLogLevelTrigger : elements.activityLogTagTrigger;
            const otherPanel = otherMenu === 'level' ? elements.activityLogLevelMenu : elements.activityLogTagMenu;
            otherDropdown?.classList.remove('open');
            otherPanel?.classList.add('hidden');
            otherTrigger?.setAttribute('aria-expanded', 'false');
        }
        const dropdown = menu === 'level' ? elements.activityLogLevelDropdown : elements.activityLogTagDropdown;
        const trigger = menu === 'level' ? elements.activityLogLevelTrigger : elements.activityLogTagTrigger;
        const panel = menu === 'level' ? elements.activityLogLevelMenu : elements.activityLogTagMenu;

        dropdown?.classList.toggle('open', state.activityLogFilterMenus[menu]);
        panel?.classList.toggle('hidden', !state.activityLogFilterMenus[menu]);
        trigger?.setAttribute('aria-expanded', state.activityLogFilterMenus[menu] ? 'true' : 'false');
    }

    function handleActivityLogDocumentClick(event) {
        const target = event.target;
        if (!(target instanceof Node)) return;

        if (state.activityLogFilterMenus.level && !elements.activityLogLevelDropdown?.contains(target)) {
            setActivityLogFilterMenuOpen('level', false);
        }
        if (state.activityLogFilterMenus.tag && !elements.activityLogTagDropdown?.contains(target)) {
            setActivityLogFilterMenuOpen('tag', false);
        }
    }

    function renderActivityLog() {
        if (!elements.activityLogViewer) return;

        const entries = getFilteredActivityLogEntries();
        if (elements.activityLogCount) {
            const total = state.activityLogTotalMatching;
            const shown = entries.length;
            const earliest = state.activityLog?.earliest_retained_timestamp
                ? ` Retained since ${formatActivityLogTimestamp(state.activityLog.earliest_retained_timestamp)}.`
                : '';
            elements.activityLogCount.textContent = `${shown} of ${total} matching entries loaded.${earliest}`;
        }
        elements.activityLogLoadOlderBtn?.classList.toggle('hidden', !state.activityLogNextCursor);

        if (!state.activityLogEntries.length) {
            elements.activityLogViewer.innerHTML = '<div class="p-3 text-txt-secondary">No activity log entries loaded.</div>';
            return;
        }

        if (!entries.length) {
            elements.activityLogViewer.innerHTML = '<div class="p-3 text-txt-secondary">No entries match the current filters.</div>';
            return;
        }

        elements.activityLogViewer.innerHTML = entries.map(renderActivityLogEntry).join('');
        elements.activityLogViewer.scrollTop = state.activityLogFilters.latestFirst ? 0 : elements.activityLogViewer.scrollHeight;
    }

    function getFilteredActivityLogEntries() {
        const filters = state.activityLogFilters;
        let entries = state.activityLogEntries;

        if (!filters.latestFirst) {
            entries = [...entries].reverse();
        }
        return entries;
    }

    function renderActivityLogEntry(entry) {
        const levelClass = activityLogLevelClass(entry.level);
        const time = formatActivityLogTimestamp(entry.timestamp);
        const dataSummary = summarizeActivityLogData(entry.data, entry.bootId);

        return `
            <div class="activity-log-row ${levelClass}">
                <div class="text-txt-secondary">${escapeHtml(time || 'unknown time')}</div>
                <div class="font-semibold uppercase">${escapeHtml(entry.level || 'raw')}</div>
                <div class="text-txt-primary">${escapeHtml(entry.tag || 'unstructured')}</div>
                <div>
                    <div class="text-txt-primary">${escapeHtml(entry.message)}</div>
                    ${dataSummary ? `<div class="activity-log-meta">${dataSummary}</div>` : ''}
                </div>
            </div>
        `;
    }

    function activityLogLevelClass(level) {
        if (level === 'error' || level === 'critical' || level === 'fatal') return 'log-level-error';
        if (level === 'warning' || level === 'warn') return 'log-level-warn';
        if (level === 'info') return 'log-level-info';
        if (level === 'debug') return 'log-level-debug';
        return '';
    }

    function formatActivityLogTimestamp(timestamp) {
        if (!timestamp) return '';
        const date = new Date(timestamp);
        if (Number.isNaN(date.getTime())) return timestamp;
        return date.toLocaleString(undefined, {
            month: 'short',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    }

    function summarizeActivityLogData(data, bootId) {
        const fields = [];
        if (data && typeof data === 'object') {
            Object.entries(data).forEach(([key, value]) => {
                if (value === undefined || value === null || value === '') return;
                fields.push([key, value]);
            });
        }
        if (bootId !== null && bootId !== undefined) {
            fields.push(['boot_id', bootId]);
        }

        return fields
            .map(([key, value]) => {
                const rendered = typeof value === 'string' ? value : JSON.stringify(value);
                return `<span>${escapeHtml(key)}=${escapeHtml(truncateActivityLogValue(rendered))}</span>`;
            })
            .join('');
    }

    function truncateActivityLogValue(value, limit = 140) {
        const text = String(value ?? '');
        return text.length > limit ? `${text.slice(0, limit)}...` : text;
    }


    Object.assign(actions, {
        refreshActivityLog,
        normalizeActivityLogEntry,
        handleActivityLogFilterChange,
        handleActivityLogSearchInput,
        populateActivityLogFilters,
        renderActivityLogLevelOptions,
        renderActivityLogTagOptions,
        renderActivityLogCheckbox,
        getCheckedActivityLogFilterValues,
        updateActivityLogFilterSummaries,
        updateActivityLogFilterSummary,
        formatActivityLogLevelLabel,
        setActivityLogFilterMenuOpen,
        handleActivityLogDocumentClick,
        renderActivityLog,
        getFilteredActivityLogEntries,
        renderActivityLogEntry,
        activityLogLevelClass,
        formatActivityLogTimestamp,
        summarizeActivityLogData,
        truncateActivityLogValue
    });
}(window, document));
