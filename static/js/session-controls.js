(function sessionControlsModule(window) {
    function createSessionControlsController({ state, elements, icons, utils, sessionSummary, callbacks }) {
        const { escapeHtml } = utils;
        let editingSessionId = '';
        let sessionBrowserFilter = '';

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

        function renderSessionActions(sessionId) {
            return `
                <div class="session-dropdown-row-actions" aria-label="Session actions">
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
            if (!status || !status.compaction_token_threshold || status.compaction_type === 'none') {
                fill.style.width = '0%';
                track.title = status && status.compaction_type === 'none'
                    ? 'Chat history compaction is disabled'
                    : 'Chat history compaction status unavailable';
                return;
            }

            const threshold = Math.max(Number(status.compaction_token_threshold) || 0, 1);
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

        async function refreshCompactionProgress() {
            const vault = elements.vaultSelector?.value || '';
            const sessionId = state.sessionId || '';
            if (!vault || !sessionId) {
                clearCompactionProgress();
                return;
            }

            const requestId = state.compactionStatusRequestId + 1;
            state.compactionStatusRequestId = requestId;
            try {
                const response = await fetch(
                    `api/chat/sessions/${encodeURIComponent(sessionId)}/compaction-status?vault_name=${encodeURIComponent(vault)}`
                );
                if (requestId !== state.compactionStatusRequestId) return;
                if (!response.ok) {
                    throw new Error('Failed to fetch chat compaction status');
                }
                renderCompactionProgress(await response.json());
            } catch (error) {
                if (requestId === state.compactionStatusRequestId) {
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
            const modal = sessionBrowserModal();
            restoreSettingsControlsFromModal(modal);
            modal?.remove();
            sessionSummary.closePreview();
        }

        function filteredBrowserSessions() {
            const filter = sessionBrowserFilter.trim().toLowerCase();
            if (!filter) return state.sessions;
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

            const sessions = filteredBrowserSessions();
            if (count) {
                count.textContent = `${sessions.length} of ${state.sessions.length} sessions`;
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
            const hasSummary = Boolean(session?.has_summary);
            const previewAttribute = hasSummary ? ` data-session-summary-preview-id="${escapeHtml(sessionId)}"` : '';
            const previewFocusAttribute = hasSummary ? ` data-session-summary-preview-focus-id="${escapeHtml(sessionId)}"` : '';
            const meta = activityLabel(session);
            return `
                <div
                    class="session-browser-row${isActive ? ' is-active' : ''}"
                    data-session-browser-row-id="${escapeHtml(sessionId)}"
                    ${previewFocusAttribute}
                >
                    <div class="session-browser-row-main"${previewAttribute}>
                        <span class="session-dropdown-title-wrap">
                            <span class="session-dropdown-title">${escapeHtml(title(session))}</span>
                            ${renderSessionBrowserSummaryAction(session)}
                        </span>
                        ${meta ? `<span class="session-browser-row-meta">${escapeHtml(meta)}</span>` : ''}
                    </div>
                    ${renderSessionActions(sessionId)}
                </div>
            `;
        }

        function renderSessionBrowserSummaryAction(session) {
            const sessionId = session?.session_id || '';
            if (!session?.has_summary || !sessionId) return '';
            return renderRowActionButton('summary', sessionId, 'Open summary', icons.SESSION_SUMMARY_ICON_SVG, 'is-summary');
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
                                <span class="session-summary-title-icon" aria-hidden="true">${icons.SETTINGS_ICON_SVG}</span>
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
                                <p class="session-browser-section-subtitle">Filter, open, summarize, export, or delete chat sessions.</p>
                            </div>
                            <button type="button" class="session-browser-new-button" data-session-browser-new="true" aria-label="New session" title="New session">
                                ${icons.PLUS_ICON_SVG}
                            </button>
                        </div>
                        <input
                            id="session-browser-filter"
                            type="text"
                            class="session-browser-filter"
                            placeholder="Filter sessions..."
                            value="${escapeHtml(sessionBrowserFilter)}"
                            autocomplete="off"
                        />
                        <div id="session-browser-list" class="session-browser-list"></div>
                    </div>
                </section>
            `;
            overlay.addEventListener('click', handleSessionBrowserClick);
            overlay.addEventListener('input', handleSessionBrowserInput);
            overlay.addEventListener('keydown', handleSessionBrowserKeydown);
            overlay.addEventListener('mouseover', handleSummaryPreviewMouseover);
            overlay.addEventListener('mouseout', handleSummaryPreviewMouseout);
            overlay.addEventListener('focusin', handleSummaryPreviewFocusin);
            overlay.addEventListener('focusout', handleSummaryPreviewFocusout);
            document.body.appendChild(overlay);
            moveSettingsControlsIntoModal(overlay);
            renderSessionBrowserList();
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
            if (action === 'summary') {
                const session = state.sessions.find((item) => item.session_id === sessionId);
                if (!session) return;
                closeSessionBrowserModal();
                sessionSummary.openModalForSession(session, {
                    backLabel: 'Sessions',
                    onBack: openSessionBrowserModal,
                });
                return;
            }
            if (action === 'export') {
                await exportCurrent(sessionId, button);
                return;
            }
            if (action === 'delete') {
                await deleteCurrent(sessionId, button);
            }
        }

        async function handleSessionBrowserClick(event) {
            const target = event.target;
            if (!(target instanceof Element)) return;

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
            renderSessionBrowserList();
        }

        async function handleSessionBrowserKeydown(event) {
            const target = event.target;
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

        function handleSummaryPreviewMouseover(event) {
            if (!shouldShowSummaryPreview()) return;
            const target = event.target;
            if (!(target instanceof Element)) return;
            const previewTarget = target.closest('[data-session-summary-preview-id]');
            if (!(previewTarget instanceof HTMLElement)) return;
            const related = event.relatedTarget;
            if (related instanceof Element && previewTarget.contains(related)) return;
            const sessionId = previewTarget.dataset.sessionSummaryPreviewId || '';
            const session = state.sessions.find((item) => item.session_id === sessionId);
            sessionSummary.openPreview(previewTarget, session);
        }

        function handleSummaryPreviewMouseout(event) {
            const target = event.target;
            if (!(target instanceof Element)) return;
            const previewTarget = target.closest('[data-session-summary-preview-id]');
            if (!(previewTarget instanceof HTMLElement)) return;
            const related = event.relatedTarget;
            if (related instanceof Element && previewTarget.contains(related)) return;
            sessionSummary.closePreview();
        }

        function handleSummaryPreviewFocusin(event) {
            if (!shouldShowSummaryPreview()) return;
            const target = event.target;
            if (!(target instanceof Element)) return;
            const previewTarget = target.closest('[data-session-summary-preview-focus-id]');
            if (!(previewTarget instanceof HTMLElement)) return;
            const sessionId = previewTarget.dataset.sessionSummaryPreviewFocusId || '';
            const session = state.sessions.find((item) => item.session_id === sessionId);
            sessionSummary.openPreview(previewTarget, session);
        }

        function handleSummaryPreviewFocusout(event) {
            const target = event.target;
            if (!(target instanceof Element)) return;
            if (target.closest('[data-session-summary-preview-focus-id]')) {
                sessionSummary.closePreview();
            }
        }

        function shouldShowSummaryPreview() {
            return !window.matchMedia?.('(hover: none), (pointer: coarse)').matches;
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
