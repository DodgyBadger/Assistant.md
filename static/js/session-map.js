(function sessionMapModule(window, document) {
    function createSessionMapController({ elements, icons, utils }) {
        const { escapeHtml } = utils;

        function closeModal() {
            document.getElementById('session-map-modal')?.remove();
        }

        async function fetchMap(sessionId, checkpointId = '') {
            const vault = elements.vaultSelector?.value || '';
            if (!vault || !sessionId) throw new Error('A vault and session are required.');
            const checkpointQuery = checkpointId
                ? `&checkpoint_id=${encodeURIComponent(checkpointId)}`
                : '';
            const response = await fetch(
                `api/chat/sessions/${encodeURIComponent(sessionId)}/map?vault_name=${encodeURIComponent(vault)}${checkpointQuery}`
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
            return `
                <div class="session-map-refs" aria-label="Canonical transcript sources">
                    ${sources.map(source => `
                        <span class="session-map-ref" title="Canonical transcript range">
                            messages ${escapeHtml(String(source.start))}–${escapeHtml(String(source.end))}
                        </span>
                    `).join('')}
                </div>
            `;
        }

        function renderEntry(entry) {
            return `
                <article class="session-map-entry">
                    <div class="session-map-entry-heading">
                        <p>${escapeHtml(entry.text || '')}</p>
                        <span class="session-map-entry-id">${escapeHtml(entry.id || '')}</span>
                    </div>
                    <div class="session-map-badges">
                        <span>${escapeHtml(humanize(entry.state))}</span>
                        <span>${escapeHtml(humanize(entry.basis))}</span>
                    </div>
                    ${renderSources(entry)}
                </article>
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

        function renderMap(payload) {
            const sessionMap = payload?.session_map;
            if (!sessionMap) {
                return '<p class="text-sm text-txt-secondary">No stepped session map exists for this session.</p>';
            }
            const revisions = Array.isArray(payload.revisions) ? payload.revisions : [];
            const selected = selectedRevision(payload);
            const revisionOptions = revisions.map(item => `
                <option value="${escapeHtml(item.checkpoint_id)}"${item.checkpoint_id === payload.selected_checkpoint_id ? ' selected' : ''}>
                    Revision ${item.revision} · observed through ${item.map_observed_through_sequence_index} · evicted through ${item.consumed_through_sequence_index}
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
            const isCurrent = payload.selected_checkpoint_id === payload.latest_checkpoint_id;
            const wasDeferred = selected?.action === 'deferred';
            return `
                <div class="session-map-toolbar">
                    <label for="session-map-revision-select">Checkpoint</label>
                    <select id="session-map-revision-select" data-session-map-checkpoint>
                        ${revisionOptions}
                    </select>
                    <span class="${isCurrent ? 'session-map-current' : 'session-map-historical'}">${isCurrent ? 'Current' : 'Historical'}</span>
                </div>
                <div class="session-map-boundary-note">
                    <p><strong>Map observed through message ${selected?.map_observed_through_sequence_index ?? 'unknown'}.</strong> The map author considered transcript evidence through this point.</p>
                    <p>Raw history is evicted through message ${selected?.consumed_through_sequence_index ?? 'unknown'}; newer messages remain verbatim in active context.</p>
                    ${wasDeferred ? '<p>This checkpoint advanced eviction after the classifier found no material map change, so the map itself was not rewritten.</p>' : ''}
                </div>
                <div class="session-map-provenance">
                    <span>${escapeHtml(formatDate(selected?.created_at))}</span>
                    <span>${escapeHtml(humanize(selected?.action))}</span>
                    ${selected?.classification_score !== null && selected?.classification_score !== undefined
                        ? `<span>Gate score: ${escapeHtml(String(selected.classification_score))}</span>`
                        : ''}
                    <span>Contract: ${escapeHtml(selected?.prompt_contract_version || 'unknown')}</span>
                </div>
                <div class="session-map-sections">
                    ${sections || '<p class="text-sm text-txt-secondary">This checkpoint contains no map entries.</p>'}
                </div>
            `;
        }

        async function loadCheckpoint(modal, sessionId, checkpointId = '') {
            const body = modal.querySelector('#session-map-modal-body');
            if (!body) return;
            body.innerHTML = '<p class="text-txt-secondary">Loading session map...</p>';
            try {
                const payload = await fetchMap(sessionId, checkpointId);
                if (modal.isConnected) body.innerHTML = renderMap(payload);
            } catch (error) {
                console.error('Error opening session map modal:', error);
                if (modal.isConnected) {
                    body.innerHTML = `<p class="text-sm state-error">Unable to load session map: ${escapeHtml(error.message)}</p>`;
                }
            }
        }

        async function openModalForSession(session, options = {}) {
            if (!session?.session_id) return;
            closeModal();
            const backLabel = String(options.backLabel || 'Sessions');
            const hasBackAction = typeof options.onBack === 'function';
            const modal = document.createElement('div');
            modal.id = 'session-map-modal';
            modal.className = 'app-modal-overlay fixed inset-0 z-50 flex bg-black/40';
            modal.innerHTML = `
                <div class="absolute inset-0" data-session-map-close="true"></div>
                <section class="app-modal-panel relative overflow-y-auto" role="dialog" aria-modal="true" aria-labelledby="session-map-modal-title">
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
            modal.addEventListener('click', event => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                if (target.closest('[data-session-map-back="true"]')) {
                    closeModal();
                    options.onBack();
                    return;
                }
                if (target.closest('[data-session-map-close="true"]')) closeModal();
            });
            modal.addEventListener('change', event => {
                const target = event.target;
                if (!(target instanceof HTMLSelectElement) || !target.matches('[data-session-map-checkpoint]')) return;
                loadCheckpoint(modal, session.session_id, target.value);
            });
            document.body.appendChild(modal);
            await loadCheckpoint(modal, session.session_id);
        }

        return Object.freeze({ closeModal, openModalForSession });
    }

    window.SessionMap = Object.freeze({ create: createSessionMapController });
})(window, document);
