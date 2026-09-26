(function sessionMapModule(window, document) {
    function createSessionMapController({ elements, icons, utils }) {
        const { escapeHtml } = utils;

        function escapeValue(value) {
            return escapeHtml(String(value ?? ''));
        }

        function closeModal() {
            document.getElementById('session-map-modal')?.remove();
        }

        async function fetchMap(sessionId, revision = null) {
            const vault = elements.vaultSelector?.value || '';
            if (!vault || !sessionId) {
                throw new Error('A vault and session are required.');
            }
            const revisionQuery = revision ? `&revision=${encodeURIComponent(revision)}` : '';
            const response = await fetch(
                `api/chat/sessions/${encodeURIComponent(sessionId)}/map?vault_name=${encodeURIComponent(vault)}${revisionQuery}`
            );
            if (!response.ok) {
                let message = `HTTP ${response.status}`;
                try {
                    const error = await response.json();
                    message = error.message || message;
                } catch (_error) {
                    // Keep the status fallback for non-JSON failures.
                }
                throw new Error(message);
            }
            return response.json();
        }

        function humanize(value) {
            return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase());
        }

        function formatDate(value) {
            if (!value) return 'Unknown';
            const parsed = new Date(value);
            return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
        }

        function renderRefs(entry) {
            const sourceRefs = Array.isArray(entry?.source_refs) ? entry.source_refs : [];
            const stateRefs = Array.isArray(entry?.state_source_refs) ? entry.state_source_refs : [];
            if (!sourceRefs.length && !stateRefs.length) return '';
            const renderRef = (ref, kind) => `
                <span class="session-map-ref" title="${escapeHtml(kind)} evidence">
                    ${escapeHtml(ref.role)} #${escapeValue(ref.sequence_index)}
                </span>
            `;
            return `
                <div class="session-map-refs" aria-label="Source evidence">
                    ${sourceRefs.map(ref => renderRef(ref, 'Source')).join('')}
                    ${stateRefs.map(ref => renderRef(ref, 'State change')).join('')}
                </div>
            `;
        }

        function renderEntry(entry) {
            const primary = entry.text || entry.ref || entry.id;
            const labels = [
                entry.status,
                entry.adoption_status,
                entry.verification_status,
                entry.epistemic_status,
                entry.relevance,
                entry.actor,
                entry.owner,
                entry.artifact_kind,
            ].filter(Boolean);
            const details = [
                Array.isArray(entry.goal_ids) && entry.goal_ids.length ? `Goals: ${entry.goal_ids.join(', ')}` : '',
                Array.isArray(entry.blocker_ids) && entry.blocker_ids.length ? `Blockers: ${entry.blocker_ids.join(', ')}` : '',
                entry.scope ? `Scope: ${entry.scope}` : '',
                entry.next_action ? `Next: ${entry.next_action}` : '',
                entry.next_action_owner ? `Owner: ${humanize(entry.next_action_owner)}` : '',
                entry.status_detail ? `Status detail: ${entry.status_detail}` : '',
                entry.answer_ref ? `Answer: ${entry.answer_ref}` : '',
                entry.active_from_sequence_index !== null && entry.active_from_sequence_index !== undefined ? `Active from message ${entry.active_from_sequence_index}` : '',
                entry.active_until_sequence_index !== null && entry.active_until_sequence_index !== undefined ? `Active through message ${entry.active_until_sequence_index}` : '',
                entry.last_state_change_sequence_index !== null && entry.last_state_change_sequence_index !== undefined ? `Last state change: message ${entry.last_state_change_sequence_index}` : '',
                entry.effective_time?.starts_at ? `Effective from: ${formatDate(entry.effective_time.starts_at)}` : '',
                entry.effective_time?.ends_at ? `Effective until: ${formatDate(entry.effective_time.ends_at)}` : '',
            ].filter(Boolean);
            return `
                <article class="session-map-entry">
                    <div class="session-map-entry-heading">
                        <p>${escapeHtml(primary)}</p>
                        <span class="session-map-entry-id">${escapeHtml(entry.id)}</span>
                    </div>
                    ${labels.length ? `<div class="session-map-badges">${labels.map(label => `<span>${escapeHtml(humanize(label))}</span>`).join('')}</div>` : ''}
                    ${details.map(detail => `<p class="session-map-entry-detail">${escapeHtml(detail)}</p>`).join('')}
                    ${renderRefs(entry)}
                </article>
            `;
        }

        function renderSection(title, entries) {
            if (!Array.isArray(entries) || entries.length === 0) return '';
            return `
                <section class="session-map-section">
                    <div class="session-map-section-heading">
                        <h3>${escapeHtml(title)}</h3>
                        <span>${entries.length}</span>
                    </div>
                    <div class="session-map-entry-list">${entries.map(renderEntry).join('')}</div>
                </section>
            `;
        }

        function renderAttention(sessionMap) {
            const attention = sessionMap?.attention || {};
            const goalIds = Array.isArray(attention.active_goal_ids) ? attention.active_goal_ids : [];
            if (!goalIds.length && !attention.active_work_item_id) return '';
            return `
                <section class="session-map-attention">
                    <p class="session-map-kicker">Current attention</p>
                    ${goalIds.length ? `<p><strong>Goals:</strong> ${goalIds.map(escapeHtml).join(', ')}</p>` : ''}
                    ${attention.active_work_item_id ? `<p><strong>Work item:</strong> ${escapeHtml(attention.active_work_item_id)}</p>` : ''}
                </section>
            `;
        }

        function renderMaintenance(maintenance, sessionMap) {
            if (!maintenance) return '';
            const lag = Math.max(
                0,
                Number(maintenance.observed_through_sequence_index || 0) - Number(sessionMap?.updated_through_sequence_index || 0)
            );
            return `
                <div class="session-map-maintenance">
                    <span class="session-map-status is-${escapeHtml(maintenance.status)}">${escapeHtml(humanize(maintenance.status))}</span>
                    <span>${escapeValue(maintenance.pending_turn_count)} pending turn${maintenance.pending_turn_count === 1 ? '' : 's'}</span>
                    <span>${lag} message${lag === 1 ? '' : 's'} beyond map coverage</span>
                    ${maintenance.last_error ? `<span class="state-error">Last attempt: ${escapeHtml(maintenance.last_error.error_type || 'failed')}</span>` : ''}
                </div>
            `;
        }

        function renderOperations(operations) {
            if (!Array.isArray(operations) || operations.length === 0) return '';
            return `
                <details class="session-map-operations">
                    <summary>Changes in this revision (${operations.length})</summary>
                    <ol>
                        ${operations.map(operation => {
                            const target = operation.entry_id || operation.entry?.id || operation.replacement?.id || '';
                            return `<li><span>${escapeHtml(humanize(operation.operation))}</span>${target ? ` <code>${escapeHtml(target)}</code>` : ''}</li>`;
                        }).join('')}
                    </ol>
                </details>
            `;
        }

        function renderMap(payload) {
            const sessionMap = payload?.session_map;
            if (!sessionMap) {
                return '<p class="text-sm text-txt-secondary">No committed session map exists for this session.</p>';
            }
            const revisions = Array.isArray(payload.revisions) ? payload.revisions : [];
            const revisionOptions = revisions.map(item => `
                <option value="${escapeValue(item.revision)}"${item.revision === payload.selected_revision ? ' selected' : ''}>
                    Revision ${escapeValue(item.revision)} · through message ${escapeValue(item.updated_through_sequence_index)}
                </option>
            `).join('');
            const sections = [
                ['Goals', sessionMap.goals],
                ['Work items', sessionMap.work_items],
                ['Decisions', sessionMap.decisions],
                ['Constraints', sessionMap.constraints],
                ['Commitments', sessionMap.commitments],
                ['Open questions', sessionMap.open_questions],
                ['Artifacts', sessionMap.artifacts],
                ['Observations', sessionMap.observations],
            ].map(([title, entries]) => renderSection(title, entries)).join('');
            return `
                <div class="session-map-toolbar">
                    <label for="session-map-revision-select">Revision</label>
                    <select id="session-map-revision-select" data-session-map-revision>
                        ${revisionOptions}
                    </select>
                    ${payload.selected_revision === payload.latest_revision ? '<span class="session-map-current">Current</span>' : '<span class="session-map-historical">Historical</span>'}
                </div>
                <div class="session-map-provenance">
                    <span>Updated through message ${escapeValue(sessionMap.updated_through_sequence_index)}</span>
                    <span>${escapeHtml(formatDate(sessionMap.updated_at))}</span>
                    ${sessionMap.authoring_model ? `<span>Author: ${escapeHtml(sessionMap.authoring_model)}</span>` : ''}
                    ${sessionMap.decision_model ? `<span>Gate: ${escapeHtml(sessionMap.decision_model)}</span>` : ''}
                    ${sessionMap.prompt_contract_version ? `<span>Contract: ${escapeHtml(sessionMap.prompt_contract_version)}</span>` : ''}
                </div>
                ${renderMaintenance(payload.maintenance, sessionMap)}
                ${renderAttention(sessionMap)}
                <div class="session-map-sections">
                    ${sections || '<p class="text-sm text-txt-secondary">This revision contains no map entries.</p>'}
                </div>
                ${renderOperations(payload.operations)}
            `;
        }

        async function loadRevision(modal, sessionId, revision = null) {
            const body = modal.querySelector('#session-map-modal-body');
            if (!body) return;
            body.innerHTML = '<p class="text-txt-secondary">Loading session map...</p>';
            try {
                const payload = await fetchMap(sessionId, revision);
                if (!modal.isConnected) return;
                body.innerHTML = renderMap(payload);
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
            modal.addEventListener('click', (event) => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                if (target.closest('[data-session-map-back="true"]')) {
                    closeModal();
                    options.onBack();
                    return;
                }
                if (target.closest('[data-session-map-close="true"]')) {
                    closeModal();
                }
            });
            modal.addEventListener('change', (event) => {
                const target = event.target;
                if (!(target instanceof HTMLSelectElement) || !target.matches('[data-session-map-revision]')) return;
                const revision = Number.parseInt(target.value, 10);
                if (Number.isInteger(revision)) loadRevision(modal, session.session_id, revision);
            });
            document.body.appendChild(modal);
            await loadRevision(modal, session.session_id);
        }

        return Object.freeze({ closeModal, openModalForSession });
    }

    window.SessionMap = Object.freeze({
        create: createSessionMapController,
    });
})(window, document);
