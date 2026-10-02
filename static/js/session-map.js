(function sessionMapModule(window, document) {
    function createSessionMapController({ elements, icons, utils, callbacks = {} }) {
        const { escapeHtml } = utils;
        let activeModal = null;
        let returnFocusTarget = null;
        const checkpointRequestIds = new WeakMap();

        function closeModal({ restoreFocus = true } = {}) {
            const modal = activeModal || document.getElementById('session-map-modal');
            if (!modal) return;
            modal.removeEventListener('keydown', handleModalKeydown);
            modal.remove();
            if (activeModal === modal) activeModal = null;
            if (restoreFocus && returnFocusTarget?.isConnected) {
                returnFocusTarget.focus();
            }
            returnFocusTarget = null;
        }

        function handleModalKeydown(event) {
            if (event.key !== 'Escape' || !activeModal?.isConnected) return;
            event.preventDefault();
            event.stopPropagation();
            closeModal();
        }

        async function fetchMap(sessionId, checkpointId = '', messagePage = 1) {
            const vault = elements.vaultSelector?.value || '';
            if (!vault || !sessionId) throw new Error('A vault and session are required.');
            const checkpointQuery = checkpointId
                ? `&checkpoint_id=${encodeURIComponent(checkpointId)}`
                : '';
            const response = await fetch(
                `api/chat/sessions/${encodeURIComponent(sessionId)}/map?vault_name=${encodeURIComponent(vault)}${checkpointQuery}&message_page=${encodeURIComponent(messagePage)}`
            );
            if (!response.ok) {
                let message = `HTTP ${response.status}`;
                try {
                    const error = await response.json();
                    message = error.message || message;
                } catch (_error) {
                    // Keep the HTTP status when the response is not JSON.
                }
                throw new Error(message);
            }
            return response.json();
        }

        function humanize(value) {
            return String(value || '')
                .replaceAll('_', ' ')
                .replace(/\b\w/g, letter => letter.toUpperCase());
        }

        function formatDate(value) {
            if (!value) return 'Unknown';
            const parsed = new Date(value);
            return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
        }

        function renderSources(entry) {
            const sources = Array.isArray(entry?.sources) ? entry.sources : [];
            if (!sources.length) return '';
            const sourceLabel = sources.map(source => {
                const start = String(source.start);
                const end = String(source.end);
                return start === end ? start : `${start}–${end}`;
            }).join(', ');
            return `
                <div class="session-map-refs" aria-label="Canonical transcript sources">
                    <span class="session-map-ref" title="Canonical transcript sources">
                        messages ${escapeHtml(sourceLabel)}
                    </span>
                </div>
            `;
        }

        function renderEntry(entry) {
            return `
                <article class="session-map-entry">
                    <div class="session-map-entry-heading">
                        <p>${escapeHtml(entry.text || '')}</p>
                    </div>
                    <div class="session-map-badges">
                        <span>${escapeHtml(humanize(entry.state))}</span>
                        <span>${escapeHtml(humanize(entry.basis))}</span>
                        <span class="session-map-entry-id">${escapeHtml(entry.id || '')}</span>
                    </div>
                    ${renderSources(entry)}
                </article>
            `;
        }

        function renderTrajectory(trajectory) {
            if (!trajectory?.text) return '';
            return `
                <section class="session-map-trajectory">
                    <h3>How We Got Here</h3>
                    <p>${escapeHtml(trajectory.text)}</p>
                    ${renderSources(trajectory)}
                </section>
            `;
        }

        function renderTranscriptMessage(message, transcript) {
            const content = String(message?.content || '').trim();
            const toolCalls = Array.isArray(message?.tool_calls) ? message.tool_calls : [];
            const sequenceStart = message?.sequence_index ?? 'unknown';
            const sequenceEnd = message?.through_sequence_index ?? sequenceStart;
            const sequenceLabel = sequenceStart === sequenceEnd
                ? `Message ${sequenceStart}`
                : `Messages ${sequenceStart}–${sequenceEnd}`;
            const forkSequenceIndex = message?.fork_sequence_index;
            const forkAction = message?.role === 'assistant' && Number.isInteger(forkSequenceIndex)
                ? `
                    <button
                        type="button"
                        class="copy-button message-fork-button"
                        data-session-map-fork="${escapeHtml(String(forkSequenceIndex))}"
                        aria-label="Fork session from this message"
                        title="Fork session from this message"
                    >${icons.FORK_ICON_SVG}</button>
                `
                : '';
            if (message?.is_tool_message) {
                const tools = toolCalls.map(toolCall => `
                    <button
                        type="button"
                        class="session-map-checkpoint-link"
                        data-session-map-tool-call="${escapeHtml(toolCall.tool_call_id || '')}"
                        data-session-map-tool-name="${escapeHtml(toolCall.tool_name || 'Tool call')}"
                        data-session-map-tool-state="${escapeHtml(toolCall.status || 'interrupted')}"
                        data-session-map-tool-tokens="${escapeHtml(toolCall.token_count ?? '')}"
                        data-session-map-tool-checkpoint="${escapeHtml(transcript?.checkpoint_id || '')}"
                        data-session-map-tool-page="${escapeHtml(String(transcript?.page || 1))}"
                    >
                        ${escapeHtml(toolCall.tool_name || 'Tool call')}
                    </button>
                `).join('');
                return `
                    <details class="session-map-transcript-message session-map-transcript-tools">
                        <summary class="session-map-transcript-message-heading session-map-transcript-tool-summary">
                            <strong><span class="tool-status-chevron" aria-hidden="true">▸</span> Tool activity</strong>
                            <span>${escapeHtml(sequenceLabel)}</span>
                        </summary>
                        <div class="tool-status-list">
                            ${tools || '<p class="text-txt-secondary">No inspectable tool details are available.</p>'}
                        </div>
                    </details>
                `;
            }
            return `
                <article class="session-map-transcript-message">
                    <div class="session-map-transcript-message-heading">
                        <strong>${escapeHtml(humanize(message?.role || 'message'))}</strong>
                        <span class="session-map-transcript-message-actions">
                            <span>${escapeHtml(sequenceLabel)}</span>
                            ${forkAction}
                        </span>
                    </div>
                    ${content
                        ? `<pre>${escapeHtml(content)}</pre>`
                        : '<p class="text-txt-secondary">No displayable text.</p>'}
                </article>
            `;
        }

        function renderTranscript(transcript, { open = false } = {}) {
            if (!transcript) return '';
            const messages = Array.isArray(transcript.messages) ? transcript.messages : [];
            return `
                <details class="session-map-transcript"${open ? ' open' : ''}>
                    <summary>View evicted transcript</summary>
                    <div class="session-map-transcript-header">
                        <p>Canonical messages through this checkpoint’s eviction boundary.</p>
                        <span>Page ${escapeHtml(String(transcript.page))} of ${escapeHtml(String(transcript.page_count))} · ${escapeHtml(String(transcript.total_entries))} entries</span>
                    </div>
                    <div class="session-map-transcript-list">
                        ${messages.map(message => renderTranscriptMessage(message, transcript)).join('') || '<p class="text-sm text-txt-secondary">No canonical messages are available for this checkpoint.</p>'}
                    </div>
                    <div class="session-map-transcript-pagination">
                        <button type="button" data-session-map-transcript-page="${transcript.page - 1}" data-session-map-transcript-checkpoint="${escapeHtml(transcript.checkpoint_id || '')}"${transcript.has_previous ? '' : ' disabled'}>Previous</button>
                        <button type="button" data-session-map-transcript-page="${transcript.page + 1}" data-session-map-transcript-checkpoint="${escapeHtml(transcript.checkpoint_id || '')}"${transcript.has_next ? '' : ' disabled'}>Next</button>
                    </div>
                </details>
            `;
        }

        function renderSection(kind, entries) {
            if (!entries.length) return '';
            return `
                <section class="session-map-section">
                    <div class="session-map-section-heading">
                        <h3>${escapeHtml(humanize(kind))}</h3>
                        <span>${entries.length}</span>
                    </div>
                    <div class="session-map-entry-list">${entries.map(renderEntry).join('')}</div>
                </section>
            `;
        }

        function selectedRevision(payload) {
            const revisions = Array.isArray(payload?.revisions) ? payload.revisions : [];
            return revisions.find(item => item.checkpoint_id === payload.selected_checkpoint_id) || null;
        }

        function renderMap(payload, options = {}) {
            const sessionMap = payload?.session_map;
            if (!sessionMap) {
                return '<p class="text-sm text-txt-secondary">No stepped session map exists for this session.</p>';
            }
            const revisions = Array.isArray(payload.revisions) ? payload.revisions : [];
            const selected = selectedRevision(payload);
            const revisionOptions = revisions.map(item => `
                <option value="${escapeHtml(item.checkpoint_id)}"${item.checkpoint_id === payload.selected_checkpoint_id ? ' selected' : ''}>
                    Revision ${item.revision}
                </option>
            `).join('');
            const groups = new Map();
            (sessionMap.entries || []).forEach(entry => {
                const kind = entry.kind || 'other';
                if (!groups.has(kind)) groups.set(kind, []);
                groups.get(kind).push(entry);
            });
            const sections = Array.from(groups.entries())
                .map(([kind, entries]) => renderSection(kind, entries))
                .join('');
            const wasDeferred = selected?.action === 'deferred';
            return `
                <div class="session-map-toolbar">
                    <label for="session-map-revision-select">Checkpoint</label>
                    <select id="session-map-revision-select" data-session-map-checkpoint>
                        ${revisionOptions}
                    </select>
                </div>
                <div class="session-map-boundary-note">
                    <p>This map reflects the conversation through message ${selected?.map_observed_through_sequence_index ?? 'unknown'}. Messages 1–${selected?.consumed_through_sequence_index ?? 'unknown'} have been replaced by this map in the assistant’s active context. The originals remain available below.${wasDeferred ? ' The prior map was reused at this checkpoint because no material change was detected.' : ''}</p>
                </div>
                <div class="session-map-provenance">
                    <span>${escapeHtml(formatDate(selected?.created_at))}</span>
                </div>
                <details class="session-map-content"${options.mapOpen === false ? '' : ' open'}>
                    <summary>View session map</summary>
                    ${renderTrajectory(sessionMap.trajectory)}
                    <div class="session-map-sections">
                        ${sections || '<p class="text-sm text-txt-secondary">This checkpoint contains no map entries.</p>'}
                    </div>
                </details>
                ${renderTranscript(payload.transcript, { open: options.transcriptOpen === true })}
            `;
        }

        async function loadCheckpoint(
            modal,
            sessionId,
            checkpointId = '',
            messagePage = 1,
            transcriptOpen = false,
            mapOpen = true
        ) {
            const body = modal.querySelector('#session-map-modal-body');
            if (!body) return;
            const requestId = (checkpointRequestIds.get(modal) || 0) + 1;
            checkpointRequestIds.set(modal, requestId);
            const isCurrent = () => modal.isConnected && checkpointRequestIds.get(modal) === requestId;
            body.innerHTML = '<p class="text-txt-secondary">Loading session map...</p>';
            try {
                const payload = await fetchMap(sessionId, checkpointId, messagePage);
                if (isCurrent()) {
                    body.innerHTML = renderMap(payload, { transcriptOpen, mapOpen });
                }
            } catch (error) {
                if (!isCurrent()) return;
                console.error('Error opening session map modal:', error);
                body.innerHTML = `<p class="text-sm state-error">Unable to load session map: ${escapeHtml(error.message)}</p>`;
            }
        }

        async function openModalForSession(session, options = {}) {
            if (!session?.session_id) return;
            closeModal({ restoreFocus: false });
            returnFocusTarget = options.returnFocusTarget || document.activeElement;
            const backLabel = String(options.backLabel || 'Sessions');
            const hasBackAction = typeof options.onBack === 'function';
            const modal = document.createElement('div');
            modal.id = 'session-map-modal';
            modal.className = 'app-modal-overlay fixed inset-0 z-50 flex bg-black/40';
            modal.innerHTML = `
                <div class="absolute inset-0" data-session-map-close="true"></div>
                <section class="app-modal-panel relative overflow-y-auto" role="dialog" aria-modal="true" aria-labelledby="session-map-modal-title" tabindex="-1" data-session-map-dialog>
                    <div class="app-modal-header sticky top-0">
                        <div class="app-modal-title-block">
                            <h2 id="session-map-modal-title" class="text-lg font-semibold text-txt-primary inline-flex items-center gap-2">
                                <span class="app-modal-title-icon" aria-hidden="true">${icons.MAP_ICON_SVG}</span>
                                <span>Session Map</span>
                            </h2>
                            <p class="mt-1 text-xs text-txt-secondary cell-mono">${escapeHtml(session.session_id)}</p>
                        </div>
                        <div class="app-modal-actions">
                            ${hasBackAction ? `
                                <button type="button" class="app-modal-back-button" data-session-map-back="true" aria-label="Back to ${escapeHtml(backLabel)}" title="Back to ${escapeHtml(backLabel)}">
                                    ${icons.ARROW_LEFT_ICON_SVG}
                                </button>
                            ` : ''}
                            <button type="button" class="ui-icon-button is-compact" data-session-map-close="true" aria-label="Close" title="Close">
                                ${icons.X_ICON_SVG}
                            </button>
                        </div>
                    </div>
                    <div id="session-map-modal-body" class="p-4 text-sm text-txt-primary">
                        <p class="text-txt-secondary">Loading session map...</p>
                    </div>
                </section>
            `;
            modal.addEventListener('click', async event => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                if (target.closest('[data-session-map-back="true"]')) {
                    closeModal({ restoreFocus: false });
                    options.onBack();
                    return;
                }
                if (target.closest('[data-session-map-close="true"]')) closeModal();
                const forkTarget = target.closest('[data-session-map-fork]');
                if (
                    forkTarget instanceof HTMLButtonElement
                    && typeof callbacks.forkSession === 'function'
                ) {
                    const sequenceIndex = Number.parseInt(
                        forkTarget.getAttribute('data-session-map-fork') || '',
                        10
                    );
                    if (Number.isInteger(sequenceIndex) && sequenceIndex >= 0) {
                        const forkSessionId = await callbacks.forkSession({
                            sessionId: session.session_id,
                            sequenceIndex,
                            button: forkTarget,
                        });
                        if (forkSessionId) closeModal();
                    }
                    return;
                }
                const toolCall = target.closest('[data-session-map-tool-call]');
                if (toolCall instanceof HTMLButtonElement && typeof callbacks.openToolCall === 'function') {
                    const mapOpen = modal.querySelector('.session-map-content')?.open !== false;
                    const mapReturnFocusTarget = returnFocusTarget;
                    const checkpointId = toolCall.getAttribute('data-session-map-tool-checkpoint') || '';
                    const messagePage = Number.parseInt(
                        toolCall.getAttribute('data-session-map-tool-page') || '1',
                        10
                    );
                    const tokenValue = toolCall.getAttribute('data-session-map-tool-tokens') || '';
                    const tokenCount = tokenValue === '' ? null : Number.parseInt(tokenValue, 10);
                    closeModal({ restoreFocus: false });
                    callbacks.openToolCall({
                        toolId: toolCall.getAttribute('data-session-map-tool-call') || '',
                        toolName: toolCall.getAttribute('data-session-map-tool-name') || 'Tool call',
                        state: toolCall.getAttribute('data-session-map-tool-state') || 'interrupted',
                        tokenCount: Number.isInteger(tokenCount) ? tokenCount : null,
                        checkpointId,
                        sessionId: session.session_id,
                        contextLabel: 'Preserved in the canonical transcript for this session-map checkpoint.',
                        backLabel: 'Session map',
                        returnFocusTarget: mapReturnFocusTarget,
                        onBack: () => openModalForSession(session, {
                            checkpointId,
                            messagePage: Number.isInteger(messagePage) ? messagePage : 1,
                            transcriptOpen: true,
                            mapOpen,
                            returnFocusTarget: mapReturnFocusTarget,
                            backLabel: options.backLabel,
                            onBack: options.onBack,
                        }),
                    });
                    return;
                }
                const transcriptPage = target.closest('[data-session-map-transcript-page]');
                if (transcriptPage instanceof HTMLButtonElement && !transcriptPage.disabled) {
                    const page = Number.parseInt(
                        transcriptPage.getAttribute('data-session-map-transcript-page') || '',
                        10
                    );
                    const checkpointId = transcriptPage.getAttribute('data-session-map-transcript-checkpoint') || '';
                    if (Number.isInteger(page) && page > 0) {
                        const mapOpen = modal.querySelector('.session-map-content')?.open !== false;
                        loadCheckpoint(modal, session.session_id, checkpointId, page, true, mapOpen);
                    }
                }
            });
            modal.addEventListener('change', event => {
                const target = event.target;
                if (!(target instanceof HTMLSelectElement) || !target.matches('[data-session-map-checkpoint]')) return;
                const transcriptOpen = modal.querySelector('.session-map-transcript')?.open === true;
                const mapOpen = modal.querySelector('.session-map-content')?.open !== false;
                loadCheckpoint(modal, session.session_id, target.value, 1, transcriptOpen, mapOpen);
            });
            document.body.appendChild(modal);
            activeModal = modal;
            modal.addEventListener('keydown', handleModalKeydown);
            modal.querySelector('[data-session-map-dialog]')?.focus();
            await loadCheckpoint(
                modal,
                session.session_id,
                String(options.checkpointId || ''),
                Number.isInteger(options.messagePage) ? options.messagePage : 1,
                options.transcriptOpen === true,
                options.mapOpen !== false
            );
        }

        return Object.freeze({ closeModal, openModalForSession });
    }

    window.SessionMap = Object.freeze({ create: createSessionMapController });
})(window, document);
