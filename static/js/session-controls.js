(function sessionControlsModule(window) {
    function createSessionControlsController({ state, elements, icons, utils, sessionMap, callbacks }) {
        const { escapeHtml } = utils;
        let editingSessionId = '';
        let sessionBrowserFilter = '';
        let sessionBrowserSearchMode = 'name';
        let sessionBrowserSearchMatches = [];
        let sessionBrowserSearchLimit = 20;
        let sessionBrowserSearchVault = '';
        let sessionBrowserSearchLoading = false;
        let sessionBrowserSearchError = '';
        let sessionBrowserSearchTimer = null;
        let sessionBrowserSearchController = null;
        let sessionBrowserSearchGeneration = 0;

        function cancelSessionBrowserSearch() {
            window.clearTimeout(sessionBrowserSearchTimer);
            sessionBrowserSearchTimer = null;
            sessionBrowserSearchController?.abort();
            sessionBrowserSearchController = null;
            sessionBrowserSearchGeneration += 1;
            sessionBrowserSearchLoading = false;
        }

        function scheduleSessionBrowserSearch() {
            cancelSessionBrowserSearch();
            sessionBrowserSearchMatches = [];
            sessionBrowserSearchError = '';
            const vault = elements.vaultSelector?.value || '';
            sessionBrowserSearchVault = vault;
            const query = sessionBrowserFilter.trim();
            const modal = sessionBrowserModal();
            const input = modal?.querySelector('#session-browser-filter');
            if (input) input.placeholder = sessionBrowserSearchMode === 'content' ? 'Search session contents...' : 'Filter sessions...';
            const toggle = modal?.querySelector('[data-session-browser-search-mode-toggle]');
            if (toggle) toggle.textContent = sessionBrowserSearchMode === 'content' ? 'Contents' : 'Names';
            const menu = modal?.querySelector('[data-session-browser-search-mode-menu]');
            menu?.querySelectorAll('[data-session-browser-search-mode-option]').forEach((option) => {
                option.setAttribute('aria-checked', String(option.dataset.sessionBrowserSearchModeOption === sessionBrowserSearchMode));
            });
            if (!modal || !vault || !query || sessionBrowserSearchMode !== 'content') {
                renderSessionBrowserList();
                return;
            }
            sessionBrowserSearchLoading = true;
            renderSessionBrowserList();
            const generation = sessionBrowserSearchGeneration;
            sessionBrowserSearchTimer = window.setTimeout(async () => {
                const controller = new AbortController();
                sessionBrowserSearchController = controller;
                const isCurrent = () => generation === sessionBrowserSearchGeneration
                    && sessionBrowserModal() === modal && elements.vaultSelector?.value === vault;
                try {
                    const params = new URLSearchParams({ vault_name: vault, query });
                    const response = await fetch(`api/chat/sessions/search?${params}`, { signal: controller.signal });
                    if (!response.ok) throw new Error(`Search failed (HTTP ${response.status}).`);
                    const payload = await response.json();
                    if (!isCurrent()) return;
                    sessionBrowserSearchMatches = payload.matches;
                    sessionBrowserSearchLimit = payload.limit;
                } catch (error) {
                    if (!isCurrent() || error.name === 'AbortError') return;
                    sessionBrowserSearchError = 'Unable to search session contents. Please try again.';
                } finally {
                    if (isCurrent()) {
                        sessionBrowserSearchLoading = false;
                        sessionBrowserSearchController = null;
                        renderSessionBrowserList();
                    }
                }
            }, 300);
        }

        function title(session) {
            if (!session || !session.session_id) {
                return 'New session';
            }
            const sessionTitle = String(session.title || '').trim();
            return sessionTitle || session.session_id;
        }

        function activityLabel(session) {
            if (!session || !session.session_id) {
                return '';
            }
            const rawDate = session.last_activity_at || session.created_at || '';
            const parsed = rawDate ? new Date(rawDate.replace(' ', 'T')) : null;
            const activity = parsed && !Number.isNaN(parsed.getTime())
                ? parsed.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
                : rawDate;
            return activity ? `Updated ${activity}` : 'No activity yet';
        }

        function formatOptionLabel(session) {
            if (!session || !session.session_id) {
                return 'New session';
            }
            const meta = activityLabel(session);
            return meta ? `${title(session)} (${meta})` : title(session);
        }

        function renderSelector() {
            renderSessionBrowserList();
            focusEditingInput();
        }

        function renderSessionActions(session) {
            const sessionId = session?.session_id || '';
            return `
                <div class="session-dropdown-row-actions" aria-label="Session actions">
                    ${session?.can_upgrade_to_v2
                        ? renderRowActionButton('upgrade-v2', sessionId, 'Upgrade to Compaction v2', icons.REFRESH_ICON_SVG)
                        : ''}
                    ${renderRowActionButton('edit-title', sessionId, 'Edit title', icons.EDIT_ICON_SVG)}
                    ${renderRowActionButton('export', sessionId, 'Export transcript', icons.DOWNLOAD_ICON_SVG)}
                    ${renderRowActionButton('delete', sessionId, 'Delete session', icons.TRASH_ICON_SVG, 'is-danger')}
                </div>
            `;
        }

        function renderRowActionButton(action, sessionId, label, icon, extraClass = '') {
            const classes = ['session-dropdown-action', extraClass].filter(Boolean).join(' ');
            return `
                <button
                    type="button"
                    class="${classes}"
                    data-session-action="${action}"
                    data-session-action-id="${escapeHtml(sessionId)}"
                    title="${escapeHtml(label)}"
                    aria-label="${escapeHtml(label)}"
                >${icon}</button>
            `;
        }

        function focusEditingInput() {
            if (!editingSessionId) return;
            window.requestAnimationFrame(() => {
                const input = document.querySelector(`[data-session-title-input="${cssEscape(editingSessionId)}"]`);
                if (input instanceof HTMLInputElement) {
                    input.focus();
                    input.select();
                }
            });
        }

        async function selectSession(sessionId) {
            editingSessionId = '';
            if (!sessionId) {
                await callbacks.clearSession(false);
            } else {
                await callbacks.loadSession(sessionId);
            }
        }

        function renderCompactionProgress(status) {
            const fill = elements.compactionFill;
            const track = elements.compactionTrack;
            if (!fill || !track) return;

            fill.classList.remove('compaction-warm', 'compaction-hot');
            if (!status || !status.compaction_high_watermark_tokens || status.compaction_type === 'none') {
                fill.style.width = '0%';
                track.title = status && status.compaction_type === 'none'
                    ? 'Chat history compaction is disabled'
                    : 'Chat history compaction status unavailable';
                return;
            }

            const threshold = Math.max(Number(status.compaction_high_watermark_tokens) || 0, 1);
            const tokens = Math.max(Number(status.estimated_tokens_before) || 0, 0);
            const percent = Math.round((tokens / threshold) * 100);
            const boundedPercent = tokens > 0 ? Math.max(2, Math.min(percent, 100)) : 0;
            fill.style.width = `${boundedPercent}%`;
            if (percent >= 100) {
                fill.classList.add('compaction-hot');
            } else if (percent >= 70) {
                fill.classList.add('compaction-warm');
            }
            const actionText = status.compaction_type === 'auto'
                ? 'Chat will be automatically compacted at 100%.'
                : 'Ask chat to compact when ready.';
            track.title = `${percent}% (${tokens.toLocaleString()} / ${threshold.toLocaleString()} threshold). ${actionText}`;
        }

        function clearCompactionProgress() {
            state.compactionStatusRequestId += 1;
            renderCompactionProgress(null);
        }

        async function refreshCompactionProgress({ signal } = {}) {
            const vault = elements.vaultSelector?.value || '';
            const sessionId = state.sessionId || '';
            if (!vault || !sessionId) {
                clearCompactionProgress();
                return;
            }

            const requestId = state.compactionStatusRequestId + 1;
            state.compactionStatusRequestId = requestId;
            const isCurrent = () => !signal?.aborted && requestId === state.compactionStatusRequestId
                && state.sessionId === sessionId && elements.vaultSelector?.value === vault;
            try {
                const response = await fetch(
                    `api/chat/sessions/${encodeURIComponent(sessionId)}/compaction-status?vault_name=${encodeURIComponent(vault)}`,
                    { cache: 'no-store', signal }
                );
                if (!isCurrent()) return;
                if (!response.ok) {
                    throw new Error('Failed to fetch chat compaction status');
                }
                const status = await response.json();
                if (!isCurrent()) return;
                renderCompactionProgress(status);
            } catch (error) {
                if (isCurrent()) {
                    console.error('Error fetching chat compaction status:', error);
                    renderCompactionProgress(null);
                }
            }
        }

        function updateTitleRow() {
            renderSelector();
        }

        function sessionBrowserModal() {
            return document.getElementById('session-browser-modal');
        }

        function closeSessionBrowserSearchMenu(restoreFocus = false) {
            const modal = sessionBrowserModal();
            const menu = modal?.querySelector('[data-session-browser-search-mode-menu]');
            const toggle = modal?.querySelector('[data-session-browser-search-mode-toggle]');
            if (menu) menu.hidden = true;
            toggle?.setAttribute('aria-expanded', 'false');
            if (restoreFocus) toggle?.focus();
        }

        function modalControlsSource() {
            return document.getElementById('chat-settings-modal-controls-source');
        }

        function moveSettingsControlsIntoModal(modal) {
            const source = modalControlsSource();
            const target = modal.querySelector('#chat-settings-modal-controls');
            if (!source || !target) return;
            while (source.firstChild) {
                target.appendChild(source.firstChild);
            }
        }

        function restoreSettingsControlsFromModal(modal) {
            const source = modalControlsSource();
            const target = modal?.querySelector('#chat-settings-modal-controls');
            if (!source || !target) return;
            while (target.firstChild) {
                source.appendChild(target.firstChild);
            }
        }

        function closeSessionBrowserModal() {
            cancelSessionBrowserSearch();
            const modal = sessionBrowserModal();
            restoreSettingsControlsFromModal(modal);
            modal?.remove();
        }

        function filteredBrowserSessions() {
            const filter = sessionBrowserFilter.trim().toLowerCase();
            if (!filter) return state.sessions;
            if (sessionBrowserSearchMode === 'content') {
                return sessionBrowserSearchMatches.map((hit) =>
                    state.sessions.find((session) => session.session_id === hit.session.session_id) || hit.session
                );
            }
            return state.sessions.filter((session) => {
                const haystack = [
                    title(session),
                    session.session_id,
                    session.workspace_path || '',
                    activityLabel(session),
                ].join(' ').toLowerCase();
                return haystack.includes(filter);
            });
        }

        function renderSessionBrowserList() {
            const modal = sessionBrowserModal();
            const list = modal?.querySelector('#session-browser-list');
            const count = modal?.querySelector('#session-browser-count');
            if (!list) return;

            if (sessionBrowserSearchMode === 'content' && sessionBrowserSearchVault !== (elements.vaultSelector?.value || '')) {
                scheduleSessionBrowserSearch();
                return;
            }
            const searchingContents = sessionBrowserSearchMode === 'content' && sessionBrowserFilter.trim();
            if (searchingContents && (sessionBrowserSearchLoading || sessionBrowserSearchError)) {
                if (count) count.textContent = sessionBrowserSearchLoading ? 'Searching contents...' : 'Search unavailable';
                list.innerHTML = `<p class="session-browser-empty" role="status">${escapeHtml(sessionBrowserSearchError || 'Searching session contents...')}</p>`;
                return;
            }

            const sessions = filteredBrowserSessions();
            if (count) {
                count.textContent = searchingContents
                    ? `${sessions.length} matches · up to ${sessionBrowserSearchLimit} best matches shown`
                    : `${sessions.length} of ${state.sessions.length} sessions`;
            }
            if (sessions.length === 0) {
                list.innerHTML = '<p class="session-browser-empty">No sessions match this filter.</p>';
                return;
            }
            list.innerHTML = sessions
                .map((session) => renderSessionBrowserRow(session, session.session_id === state.sessionId))
                .join('');
            focusEditingInput();
        }

        function renderSessionBrowserRow(session, isActive) {
            const sessionId = session?.session_id || '';
            if (sessionId && editingSessionId === sessionId) {
                return renderSessionBrowserEditingRow(session, isActive);
            }
            const meta = activityLabel(session);
            const evidence = sessionBrowserSearchMode === 'content' && sessionBrowserFilter.trim()
                ? sessionBrowserSearchMatches.find((hit) => hit.session.session_id === sessionId)?.evidence || []
                : [];
            const excerpts = evidence.map((hit) => {
                const label = hit.source === 'session_map'
                    ? (hit.historical ? 'Earlier map' : 'Map')
                    : hit.source === 'transcript' ? 'Transcript' : 'Session details';
                return `<span class="session-browser-row-excerpt"><span class="session-browser-row-meta">${label}:</span> ${escapeHtml(hit.excerpt)}</span>`;
            }).join('');
            return `
                <div
                    class="session-browser-row${isActive ? ' is-active' : ''}"
                    data-session-browser-row-id="${escapeHtml(sessionId)}"
                >
                    <div class="session-browser-row-main">
                        <span class="session-dropdown-title-wrap">
                            <span class="session-dropdown-title">${escapeHtml(title(session))}</span>
                            ${renderSessionBrowserMapAction(session)}
                        </span>
                        ${meta ? `<span class="session-browser-row-meta">${escapeHtml(meta)}</span>` : ''}
                        ${excerpts}
                    </div>
                    ${renderSessionActions(session)}
                </div>
            `;
        }

        function renderSessionBrowserMapAction(session) {
            const sessionId = session?.session_id || '';
            if (!session?.has_session_map || !sessionId) return '';
            return renderRowActionButton('map', sessionId, 'Open session map', icons.MAP_ICON_SVG, 'is-map');
        }

        function renderSessionBrowserEditingRow(session, isActive) {
            const sessionId = session?.session_id || '';
            return `
                <div class="session-browser-row session-browser-row-editing${isActive ? ' is-active' : ''}">
                    <input
                        type="text"
                        class="session-dropdown-title-input"
                        value="${escapeHtml(session?.title || '')}"
                        placeholder="Add a title..."
                        maxlength="120"
                        data-session-title-input="${escapeHtml(sessionId)}"
                        aria-label="Session title"
                    />
                    <div class="session-dropdown-row-actions">
                        ${renderRowActionButton('save-title', sessionId, 'Save title', icons.CHECK_ICON_SVG)}
                        ${renderRowActionButton('cancel-title', sessionId, 'Cancel title edit', icons.CIRCLE_X_ICON_SVG)}
                    </div>
                </div>
            `;
        }

        function openSessionBrowserModal() {
            closeSessionBrowserModal();
            const overlay = document.createElement('div');
            overlay.id = 'session-browser-modal';
            overlay.className = 'app-modal-overlay fixed inset-0 z-50 flex bg-black/40';
            overlay.innerHTML = `
                <div class="absolute inset-0" data-session-browser-close="true"></div>
                <section class="app-modal-panel relative flex flex-col" role="dialog" aria-modal="true" aria-labelledby="session-browser-modal-title">
                    <div class="app-modal-header flex-none">
                        <div class="app-modal-title-block">
                            <h2 id="session-browser-modal-title" class="text-lg font-semibold text-txt-primary inline-flex items-center gap-2">
                                <span class="app-modal-title-icon" aria-hidden="true">${icons.SETTINGS_ICON_SVG}</span>
                                <span>Chat Settings</span>
                            </h2>
                            <p id="session-browser-count" class="mt-1 text-xs text-txt-secondary"></p>
                        </div>
                        <div class="app-modal-actions">
                            <button type="button" class="session-browser-close-button" data-session-browser-close="true" aria-label="Close chat settings" title="Close">
                                ${icons.X_ICON_SVG}
                            </button>
                        </div>
                    </div>
                    <div class="session-browser-body flex-1">
                        <details class="chat-settings-options">
                            <summary class="chat-settings-options-summary">
                                <span>Options</span>
                                <svg class="chat-settings-options-chevron" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                                    <path d="M6 8l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"></path>
                                </svg>
                            </summary>
                            <div id="chat-settings-modal-controls" class="chat-settings-modal-controls"></div>
                        </details>
                        <div class="session-browser-section-header">
                            <div>
                                <h3 class="session-browser-section-title">Sessions</h3>
                                <p class="session-browser-section-subtitle">Filter, open, upgrade, export, or delete chat sessions.</p>
                            </div>
                            <button type="button" class="session-browser-new-button" data-session-browser-new="true" aria-label="New session" title="New session">
                                ${icons.PLUS_ICON_SVG}
                            </button>
                        </div>
                        <div class="vault-explorer-search-control session-browser-search-control">
                            <input
                                id="session-browser-filter"
                                type="search"
                                class="file-reference-search"
                                placeholder="Filter sessions..."
                                value="${escapeHtml(sessionBrowserFilter)}"
                                autocomplete="off"
                                maxlength="2000"
                                aria-label="Find sessions"
                            />
                            <button type="button" class="vault-explorer-search-mode-toggle"
                                data-session-browser-search-mode-toggle aria-haspopup="menu" aria-expanded="false"
                                aria-label="Session search mode" title="Search mode">${sessionBrowserSearchMode === 'content' ? 'Contents' : 'Names'}</button>
                            <div class="vault-explorer-search-mode-menu" data-session-browser-search-mode-menu role="menu" hidden>
                                <button type="button" data-session-browser-search-mode-option="name" role="menuitemradio" aria-checked="${sessionBrowserSearchMode === 'name'}">Names</button>
                                <button type="button" data-session-browser-search-mode-option="content" role="menuitemradio" aria-checked="${sessionBrowserSearchMode === 'content'}">Contents</button>
                            </div>
                        </div>
                        <div id="session-browser-list" class="session-browser-list"></div>
                    </div>
                </section>
            `;
            overlay.addEventListener('click', handleSessionBrowserClick);
            overlay.addEventListener('input', handleSessionBrowserInput);
            overlay.addEventListener('keydown', handleSessionBrowserKeydown);
            document.body.appendChild(overlay);
            moveSettingsControlsIntoModal(overlay);
            scheduleSessionBrowserSearch();
            const filterInput = overlay.querySelector('#session-browser-filter');
            if (filterInput instanceof HTMLInputElement) {
                filterInput.focus();
                filterInput.select();
            }
        }

        async function saveTitle(sessionId = state.sessionId, titleValue = '', btn = null) {
            const vault = elements.vaultSelector?.value || '';
            if (!sessionId || !vault) return;

            const nextTitle = String(titleValue || '').trim() || null;
            if (btn) btn.disabled = true;
            try {
                const response = await fetch(`api/chat/sessions/${encodeURIComponent(sessionId)}/title`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ vault_name: vault, title: nextTitle }),
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const session = state.sessions.find((s) => s.session_id === sessionId);
                if (session) session.title = nextTitle;
                const match = sessionBrowserSearchMatches.find((hit) => hit.session.session_id === sessionId);
                if (match) match.session.title = nextTitle;
                editingSessionId = '';
                renderSelector();
                renderSessionBrowserList();
            } catch (error) {
                console.error('Failed to save session title:', error);
            } finally {
                if (btn) btn.disabled = false;
            }
        }

        async function exportCurrent(sessionId = state.sessionId, btn = null) {
            const vault = elements.vaultSelector?.value || '';
            if (!sessionId || !vault) return;

            if (btn) btn.disabled = true;
            try {
                const response = await fetch(`api/chat/sessions/${encodeURIComponent(sessionId)}/export`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ vault_name: vault }),
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const payload = await response.json();
                alert(`Transcript exported to ${payload.filename}`);
            } catch (error) {
                console.error('Failed to export session transcript:', error);
                alert('Failed to export transcript');
            } finally {
                if (btn) btn.disabled = false;
                callbacks.syncChatControlLocks();
            }
        }

        async function upgradeContextStrategy(sessionId, btn = null) {
            const vault = elements.vaultSelector?.value || '';
            const session = state.sessions.find((item) => item.session_id === sessionId);
            if (!sessionId || !vault || !session?.can_upgrade_to_v2) return;
            if (!confirm(
                `Upgrade "${title(session)}" to Compaction v2? This will use the configured map-author model. Canonical chat history will be preserved.`
            )) return;

            if (btn) btn.disabled = true;
            try {
                const response = await fetch(
                    `api/chat/sessions/${encodeURIComponent(sessionId)}/upgrade-context-strategy`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ vault_name: vault }),
                    }
                );
                const started = await response.json();
                if (!response.ok) {
                    throw new Error(started?.message || `HTTP ${response.status}`);
                }
                const taskId = started?.task?.task_id || '';
                if (!taskId) throw new Error('Upgrade task did not return an ID');

                let task = started.task;
                for (let attempt = 0; attempt < 1200; attempt += 1) {
                    if (!['queued', 'running'].includes(task?.status)) break;
                    await new Promise((resolve) => window.setTimeout(resolve, 500));
                    const taskResponse = await fetch(
                        `api/tasks/${encodeURIComponent(taskId)}`,
                        { cache: 'no-store' }
                    );
                    if (!taskResponse.ok) {
                        throw new Error(`Task status HTTP ${taskResponse.status}`);
                    }
                    task = await taskResponse.json();
                }
                if (task?.status !== 'completed') {
                    throw new Error(
                        task?.terminal_reason
                        || `Upgrade ended with status ${task?.status || 'unknown'}`
                    );
                }

                let refreshError = null;
                try {
                    await callbacks.fetchSessions(vault, state.sessionId || '');
                    if (state.sessionId === sessionId) {
                        await callbacks.loadSession(sessionId, { skipActiveTaskCheck: true });
                    }
                } catch (error) {
                    refreshError = error;
                    console.warn('Session upgrade succeeded, but the session view could not refresh:', error);
                }
                if (refreshError) {
                    alert(`Session upgraded to Compaction v2, but the view could not refresh: ${refreshError.message}. Reload the session to see the updated context.`);
                } else {
                    alert('Session upgraded to Compaction v2.');
                }
            } catch (error) {
                console.error('Failed to upgrade session context strategy:', error);
                alert(`Failed to upgrade session: ${error.message}`);
                try {
                    await callbacks.fetchSessions(vault, state.sessionId || '');
                } catch (refreshError) {
                    console.warn('Could not refresh sessions after upgrade failure:', refreshError);
                }
            } finally {
                if (btn) btn.disabled = false;
                renderSessionBrowserList();
            }
        }

        async function deleteCurrent(sessionId = state.sessionId, btn = null) {
            const vault = elements.vaultSelector?.value || '';
            if (!sessionId || !vault) return;

            if (!confirm(
                `Delete session "${sessionId}"? This removes it from the chat session list and database only. Exported transcripts are not deleted.`
            )) return;

            if (btn) btn.disabled = true;
            try {
                const response = await fetch(
                    `api/chat/sessions/${encodeURIComponent(sessionId)}?vault_name=${encodeURIComponent(vault)}`,
                    { method: 'DELETE' }
                );
                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const deletedActiveSession = state.sessionId === sessionId;
                state.sessions = state.sessions.filter((s) => s.session_id !== sessionId);
                sessionBrowserSearchMatches = sessionBrowserSearchMatches.filter((hit) => hit.session.session_id !== sessionId);
                if (deletedActiveSession) {
                    state.sessionId = null;
                }
                editingSessionId = '';
                if (deletedActiveSession) {
                    callbacks.clearPendingAttachments();
                    callbacks.renderChatEmptyState();
                    callbacks.resetChatModeToDefault();
                }
                renderSelector();
                renderSessionBrowserList();
                callbacks.syncChatControlLocks();
                callbacks.updateStatus();
            } catch (error) {
                console.error('Failed to delete session:', error);
            } finally {
                if (btn) btn.disabled = false;
            }
        }

        async function handleSessionAction(button) {
            const action = button.dataset.sessionAction || '';
            const sessionId = button.dataset.sessionActionId || '';
            if (!sessionId) return;
            if (action === 'delete' && state.isLoading && sessionId === state.sessionId) return;

            if (action === 'edit-title') {
                editingSessionId = sessionId;
                renderSelector();
                renderSessionBrowserList();
                return;
            }
            if (action === 'cancel-title') {
                editingSessionId = '';
                renderSelector();
                renderSessionBrowserList();
                return;
            }
            if (action === 'save-title') {
                const input = document.querySelector(`[data-session-title-input="${cssEscape(sessionId)}"]`);
                const titleValue = input instanceof HTMLInputElement ? input.value : '';
                await saveTitle(sessionId, titleValue, button);
                return;
            }
            if (action === 'map') {
                const session = state.sessions.find((item) => item.session_id === sessionId)
                    || sessionBrowserSearchMatches.find((hit) => hit.session.session_id === sessionId)?.session;
                if (!session) return;
                closeSessionBrowserModal();
                sessionMap.openModalForSession(session, {
                    backLabel: 'Sessions',
                    onBack: openSessionBrowserModal,
                });
                return;
            }
            if (action === 'export') {
                await exportCurrent(sessionId, button);
                return;
            }
            if (action === 'upgrade-v2') {
                await upgradeContextStrategy(sessionId, button);
                return;
            }
            if (action === 'delete') {
                await deleteCurrent(sessionId, button);
            }
        }

        async function handleSessionBrowserClick(event) {
            const target = event.target;
            if (!(target instanceof Element)) return;

            const modeOption = target.closest('[data-session-browser-search-mode-option]');
            if (modeOption instanceof HTMLButtonElement) {
                sessionBrowserSearchMode = modeOption.dataset.sessionBrowserSearchModeOption === 'content' ? 'content' : 'name';
                closeSessionBrowserSearchMenu(true);
                scheduleSessionBrowserSearch();
                return;
            }
            const modeToggle = target.closest('[data-session-browser-search-mode-toggle]');
            if (modeToggle instanceof HTMLButtonElement) {
                const menu = sessionBrowserModal()?.querySelector('[data-session-browser-search-mode-menu]');
                if (!menu) return;
                menu.hidden = !menu.hidden;
                modeToggle.setAttribute('aria-expanded', String(!menu.hidden));
                if (!menu.hidden) menu.querySelector('[aria-checked="true"]')?.focus();
                return;
            }
            closeSessionBrowserSearchMenu();

            const closeTarget = target.closest('[data-session-browser-close]');
            if (closeTarget) {
                closeSessionBrowserModal();
                return;
            }

            const newTarget = target.closest('[data-session-browser-new]');
            if (newTarget) {
                closeSessionBrowserModal();
                await selectSession('');
                return;
            }

            const actionButton = target.closest('[data-session-action]');
            if (actionButton instanceof HTMLButtonElement) {
                event.preventDefault();
                event.stopPropagation();
                await handleSessionAction(actionButton);
                return;
            }

            const row = target.closest('[data-session-browser-row-id]');
            if (row instanceof HTMLElement) {
                event.preventDefault();
                const sessionId = row.dataset.sessionBrowserRowId || '';
                closeSessionBrowserModal();
                await selectSession(sessionId);
            }
        }

        function handleSessionBrowserInput(event) {
            const target = event.target;
            if (!(target instanceof HTMLInputElement)) return;
            if (target.id !== 'session-browser-filter') return;
            sessionBrowserFilter = target.value;
            scheduleSessionBrowserSearch();
        }

        async function handleSessionBrowserKeydown(event) {
            const target = event.target;
            const menu = target instanceof Element ? target.closest('[data-session-browser-search-mode-menu]') : null;
            if (menu && event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                closeSessionBrowserSearchMenu(true);
                return;
            }
            if (menu && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                const options = Array.from(menu.querySelectorAll('[data-session-browser-search-mode-option]'));
                const index = options.indexOf(target);
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
                    : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
                event.preventDefault();
                options[next]?.focus();
                return;
            }
            if (event.key === 'Tab') closeSessionBrowserSearchMenu();
            if (target instanceof HTMLInputElement && target.dataset.sessionTitleInput) {
                const sessionId = target.dataset.sessionTitleInput || '';
                if (event.key === 'Enter') {
                    event.preventDefault();
                    await saveTitle(sessionId, target.value);
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    editingSessionId = '';
                    renderSelector();
                    renderSessionBrowserList();
                }
                return;
            }
            if (event.key === 'Escape') {
                closeSessionBrowserModal();
            }
        }

        function cssEscape(value) {
            if (window.CSS && typeof window.CSS.escape === 'function') {
                return window.CSS.escape(value);
            }
            return String(value).replace(/"/g, '\\"');
        }

        function attachEventListeners() {
            if (elements.newSessionTrigger) {
                elements.newSessionTrigger.addEventListener('click', () => {
                    closeSessionBrowserModal();
                    selectSession('');
                });
            }
            if (elements.sessionBrowserTrigger) {
                elements.sessionBrowserTrigger.addEventListener('click', openSessionBrowserModal);
            }

        }

        return Object.freeze({
            formatOptionLabel,
            renderSelector,
            clearCompactionProgress,
            refreshCompactionProgress,
            updateTitleRow,
            attachEventListeners,
            saveTitle,
            exportCurrent,
            deleteCurrent,
            openSessionBrowserModal,
        });
    }

    window.SessionControls = Object.freeze({
        create: createSessionControlsController,
    });
})(window);
