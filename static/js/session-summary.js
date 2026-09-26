(function sessionSummaryModule(window, document) {
    function createSessionSummaryController({ state, elements, icons, utils, callbacks }) {
        const {
            ARROW_LEFT_ICON_SVG,
            CIRCLE_X_ICON_SVG,
            EDIT_ICON_SVG,
            SAVE_ICON_SVG,
            SESSION_SUMMARY_ICON_SVG,
            TRASH_ICON_SVG,
            X_ICON_SVG,
        } = icons;
        const { escapeHtml, truncateText, formatShortDate } = utils;

        function cacheKey(vault, sessionId) {
            return `${vault || ''}::${sessionId || ''}`;
        }

        function selectedSessionWithSummary() {
            if (!state.sessionId) return null;
            const session = state.sessions.find((item) => item.session_id === state.sessionId);
            return session?.has_summary ? session : null;
        }

        async function fetchPreview(session) {
            if (!session) return null;

            const vault = elements.vaultSelector?.value || '';
            const key = cacheKey(vault, session.session_id);
            const cached = state.sessionSummaryPreviewCache[key];
            if (cached) {
                return cached;
            }
            if (state.sessionSummaryPreviewInFlight[key]) {
                return state.sessionSummaryPreviewInFlight[key];
            }

            state.sessionSummaryPreviewInFlight[key] = (async () => {
                const response = await fetch(
                    `api/chat/sessions/${encodeURIComponent(session.session_id)}/summary?vault_name=${encodeURIComponent(vault)}`
                );
                if (!response.ok) {
                    throw new Error('Failed to fetch session summary');
                }
                const data = await response.json();
                state.sessionSummaryPreviewCache[key] = data;
                return data;
            })();

            try {
                return await state.sessionSummaryPreviewInFlight[key];
            } finally {
                delete state.sessionSummaryPreviewInFlight[key];
            }
        }

        async function warmPreview(session) {
            try {
                await fetchPreview(session);
            } catch (error) {
                console.error('Error fetching session summary preview:', error);
            }
        }

        function renderPreview(summary) {
            if (!summary?.has_summary) {
                return '<p class="session-summary-preview-text text-txt-secondary">No summary record found.</p>';
            }
            const rawSummaryText = String(summary.summary || summary.user_intent || '').trim();
            const summaryText = rawSummaryText
                ? truncateText(rawSummaryText, 520)
                : 'No summary text captured.';
            const workspacePath = String(summary.workspace_path || '').trim();
            return `
                <div class="session-summary-preview-title">Session Summary</div>
                <div class="session-summary-preview-text">${escapeHtml(summaryText)}</div>
                <div class="session-summary-preview-workspace">
                    <span class="font-semibold text-txt-primary">Workspace:</span>
                    ${escapeHtml(workspacePath || 'Not set')}
                </div>
            `;
        }

        function positionPreview(popover, anchor) {
            const anchorRect = anchor.getBoundingClientRect();
            const margin = 8;
            const popoverRect = popover.getBoundingClientRect();
            let left = anchorRect.right + margin;
            let top = anchorRect.top;
            if (left + popoverRect.width > window.innerWidth - margin) {
                left = anchorRect.left - popoverRect.width - margin;
            }
            if (left < margin) {
                left = margin;
            }
            if (top + popoverRect.height > window.innerHeight - margin) {
                top = window.innerHeight - popoverRect.height - margin;
            }
            if (top < margin) {
                top = margin;
            }
            popover.style.left = `${left}px`;
            popover.style.top = `${top}px`;
        }

        function closePreview() {
            document.getElementById('session-summary-preview-popover')?.remove();
        }

        async function openPreview(anchor, session) {
            if (!anchor || !session) return;
            closePreview();
            const popover = document.createElement('div');
            popover.id = 'session-summary-preview-popover';
            popover.className = 'session-summary-preview-popover';
            popover.innerHTML = '<p class="session-summary-preview-text text-txt-secondary">Loading summary...</p>';
            document.body.appendChild(popover);
            positionPreview(popover, anchor);
            try {
                const summary = await fetchPreview(session);
                if (!document.body.contains(popover)) return;
                popover.innerHTML = renderPreview(summary);
                positionPreview(popover, anchor);
            } catch (error) {
                console.error('Error fetching session summary preview:', error);
                if (document.body.contains(popover)) {
                    popover.innerHTML = '<p class="session-summary-preview-text state-error">Unable to load summary preview.</p>';
                    positionPreview(popover, anchor);
                }
            }
        }

        function editableFields() {
            return [
                { key: 'summary', label: 'Summary', rows: 6 },
                { key: 'user_intent', label: 'User Intent', rows: 3 },
                { key: 'domain', label: 'Domain', rows: 2 },
                { key: 'work_product', label: 'Work Product', rows: 2 },
                { key: 'workspace_path', label: 'Workspace', rows: 2 },
                { key: 'named_entities', label: 'Named Entities', rows: 2 },
                { key: 'source_summary', label: 'Source Summary', rows: 6 },
            ];
        }

        function renderReadonlyField(summary, field) {
            const value = String(summary?.[field.key] || '').trim();
            return `
                <div>
                    <p class="font-semibold text-txt-primary">${escapeHtml(field.label)}</p>
                    ${value
                        ? `<p class="text-sm text-txt-primary whitespace-pre-wrap">${escapeHtml(value)}</p>`
                        : '<p class="text-sm text-txt-secondary">Not captured.</p>'}
                </div>
            `;
        }

        function renderVectorIndex(vectorIndex) {
            const status = vectorIndex && typeof vectorIndex === 'object' ? vectorIndex : {};
            const indexedFields = Number(status.indexed_fields || 0);
            const expectedFields = Number(status.expected_fields || 0);
            const indexedTypes = Array.isArray(status.indexed_field_types) ? status.indexed_field_types : [];
            const missingTypes = Array.isArray(status.missing_field_types) ? status.missing_field_types : [];
            const detail = [
                indexedTypes.length ? `indexed: ${indexedTypes.join(', ')}` : '',
                missingTypes.length ? `missing: ${missingTypes.join(', ')}` : '',
            ].filter(Boolean).join(' · ');
            return `
                <div class="mt-1 mb-2 rounded-md border border-border-primary bg-app-bg p-3 text-xs text-txt-secondary">
                    <span class="font-semibold text-txt-primary">Vector index:</span>
                    ${escapeHtml(String(indexedFields))}/${escapeHtml(String(expectedFields))} fields
                    ${detail ? `<span class="block mt-1">${escapeHtml(detail)}</span>` : ''}
                </div>
            `;
        }

        function renderDetails(summary) {
            const fields = editableFields();
            const artifacts = Array.isArray(summary.artifacts) ? summary.artifacts : [];
            const metadata = summary.metadata && typeof summary.metadata === 'object' ? summary.metadata : {};
            return `
                <div class="space-y-4">
                    <div class="state-surface-warning p-3 rounded border text-sm">
                        Manual edits update this derived session summary only. If this chat session is continued later, session summarization may replace these edits.
                    </div>
                    ${fields.map(field => renderReadonlyField(summary, field)).join('')}
                    <div>
                        <p class="font-semibold text-txt-primary">Artifacts</p>
                        ${artifacts.length
                            ? `<ul class="mt-1 list-disc list-inside text-sm text-txt-primary">${artifacts.map(artifact => `<li><span class="cell-mono">${escapeHtml(artifact.path || '')}</span>${artifact.artifact_role ? ` <span class="text-txt-secondary">(${escapeHtml(artifact.artifact_role)})</span>` : ''}</li>`).join('')}</ul>`
                            : '<p class="text-sm text-txt-secondary">No artifacts linked.</p>'}
                    </div>
                    <div>
                        <p class="font-semibold text-txt-primary">Metadata</p>
                        ${renderVectorIndex(summary.vector_index)}
                        <pre class="mt-1 whitespace-pre-wrap rounded-md border border-border-primary bg-app-bg p-3 text-xs text-txt-secondary">${escapeHtml(JSON.stringify(metadata, null, 2))}</pre>
                    </div>
                    <div class="text-xs text-txt-secondary">
                        Created ${escapeHtml(formatShortDate(summary.created_at || ''))} · Updated ${escapeHtml(formatShortDate(summary.updated_at || ''))}
                    </div>
                </div>
            `;
        }

        function renderEditForm(summary) {
            return `
                <div class="space-y-4">
                    <div class="state-surface-warning p-3 rounded border text-sm">
                        Manual edits update this derived session summary only. If this chat session is continued later, session summarization may replace these edits.
                    </div>
                    ${editableFields().map(field => `
                        <label class="block">
                            <span class="font-semibold text-txt-primary">${escapeHtml(field.label)}</span>
                            <textarea
                                data-session-summary-field="${escapeHtml(field.key)}"
                                rows="${field.rows}"
                                class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-bg text-txt-primary text-sm focus:outline-none focus:ring-2 focus:ring-accent"
                            >${escapeHtml(summary?.[field.key] || '')}</textarea>
                        </label>
                    `).join('')}
                    <div>
                        <p class="font-semibold text-txt-primary">Metadata</p>
                        <pre class="mt-1 whitespace-pre-wrap rounded-md border border-border-primary bg-app-bg p-3 text-xs text-txt-secondary">${escapeHtml(JSON.stringify(summary?.metadata || {}, null, 2))}</pre>
                    </div>
                </div>
            `;
        }

        function closeModal() {
            document.getElementById('session-summary-modal')?.remove();
        }

        function currentModalSummary() {
            return document.getElementById('session-summary-modal')?._sessionSummary || null;
        }

        function setModalEditing(modal, summary) {
            const body = modal.querySelector('#session-summary-modal-body');
            const editButton = modal.querySelector('[data-session-summary-edit]');
            const saveButton = modal.querySelector('[data-session-summary-save]');
            const cancelButton = modal.querySelector('[data-session-summary-cancel-edit]');
            if (body) {
                body.innerHTML = renderEditForm(summary);
            }
            editButton?.classList.add('hidden');
            saveButton?.classList.remove('hidden');
            cancelButton?.classList.remove('hidden');
        }

        function setModalReadonly(modal, summary) {
            const body = modal.querySelector('#session-summary-modal-body');
            const editButton = modal.querySelector('[data-session-summary-edit]');
            const saveButton = modal.querySelector('[data-session-summary-save]');
            const cancelButton = modal.querySelector('[data-session-summary-cancel-edit]');
            if (body) {
                body.innerHTML = summary?.has_summary
                    ? renderDetails(summary)
                    : '<p class="text-sm text-txt-secondary">No summary record found for this session.</p>';
            }
            editButton?.classList.remove('hidden');
            saveButton?.classList.add('hidden');
            cancelButton?.classList.add('hidden');
        }

        function cancelEdit(modal) {
            setModalReadonly(modal, currentModalSummary());
        }

        async function saveModal(modal, triggerButton) {
            const vault = elements.vaultSelector?.value || '';
            const existing = currentModalSummary();
            const sessionId = existing?.session_id || '';
            if (!sessionId || !vault) return;
            triggerButton.disabled = true;
            icons.setIconButtonLabel(triggerButton, 'Saving summary...');
            const payload = {};
            editableFields().forEach(field => {
                const input = modal.querySelector(`[data-session-summary-field="${field.key}"]`);
                payload[field.key] = input ? input.value : '';
            });
            payload.metadata = existing?.metadata || {};
            try {
                const response = await fetch(
                    `api/chat/sessions/${encodeURIComponent(sessionId)}/summary?vault_name=${encodeURIComponent(vault)}`,
                    {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload),
                    }
                );
                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.message || `HTTP ${response.status}`);
                }
                const summary = await response.json();
                state.sessionSummaryPreviewCache[cacheKey(vault, sessionId)] = summary;
                modal._sessionSummary = summary;
                callbacks.renderSessionSelector();
                setModalReadonly(modal, summary);
            } catch (error) {
                console.error('Error saving session summary:', error);
                const body = modal.querySelector('#session-summary-modal-body');
                if (body) {
                    body.insertAdjacentHTML('afterbegin', `<p class="mb-3 state-error">Unable to save summary: ${escapeHtml(error.message)}</p>`);
                }
            } finally {
                triggerButton.disabled = false;
                icons.setIconButtonLabel(triggerButton, 'Save summary');
            }
        }

        async function deleteFromModal(modal, triggerButton) {
            const vault = elements.vaultSelector?.value || '';
            const summary = currentModalSummary();
            const sessionId = summary?.session_id || '';
            if (!sessionId || !vault) return;
            const confirmed = window.confirm('Delete this derived summary record? The chat session will not be deleted.');
            if (!confirmed) return;
            triggerButton.disabled = true;
            icons.setIconButtonLabel(triggerButton, 'Deleting summary...');
            try {
                const response = await fetch(
                    `api/chat/sessions/${encodeURIComponent(sessionId)}/summary?vault_name=${encodeURIComponent(vault)}`,
                    { method: 'DELETE' }
                );
                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.message || `HTTP ${response.status}`);
                }
                delete state.sessionSummaryPreviewCache[cacheKey(vault, sessionId)];
                closeModal();
                await callbacks.fetchSessions(vault, state.sessionId || sessionId);
            } catch (error) {
                console.error('Error deleting session summary:', error);
                const body = modal.querySelector('#session-summary-modal-body');
                if (body) {
                    body.insertAdjacentHTML('afterbegin', `<p class="mb-3 state-error">Unable to delete summary: ${escapeHtml(error.message)}</p>`);
                }
                triggerButton.disabled = false;
                icons.setIconButtonLabel(triggerButton, 'Delete summary');
            }
        }

        async function openModalForSession(session, options = {}) {
            if (!session) return;

            closeModal();
            const backLabel = String(options.backLabel || 'Sessions');
            const hasBackAction = typeof options.onBack === 'function';
            const popover = document.createElement('div');
            popover.id = 'session-summary-modal';
            popover.className = 'app-modal-overlay fixed inset-0 z-50 flex bg-black/40';
            popover.innerHTML = `
                <div class="absolute inset-0" data-session-summary-close="true"></div>
                <section class="app-modal-panel relative overflow-y-auto" role="dialog" aria-modal="true" aria-labelledby="session-summary-modal-title">
                    <div class="app-modal-header sticky top-0">
                        <div class="app-modal-title-block">
                            <h2 id="session-summary-modal-title" class="text-lg font-semibold text-txt-primary inline-flex items-center gap-2">
                                <span class="app-modal-title-icon" aria-hidden="true">${SESSION_SUMMARY_ICON_SVG}</span>
                                <span>Session Summary</span>
                            </h2>
                            <p class="mt-1 text-xs text-txt-secondary cell-mono">${escapeHtml(session.session_id)}</p>
                        </div>
                        <div class="app-modal-actions">
                            ${hasBackAction ? `
                                <button type="button" class="app-modal-back-button" data-session-summary-back="true" aria-label="Back to ${escapeHtml(backLabel)}" title="Back to ${escapeHtml(backLabel)}">
                                    ${ARROW_LEFT_ICON_SVG}
                                </button>
                            ` : ''}
                            <button type="button" class="ui-icon-button is-danger is-compact" data-session-summary-delete="true" aria-label="Delete summary" title="Delete summary">
                                ${TRASH_ICON_SVG}
                            </button>
                            <button type="button" class="ui-icon-button is-compact" data-session-summary-edit="true" aria-label="Edit summary" title="Edit summary">
                                ${EDIT_ICON_SVG}
                            </button>
                            <button type="button" class="hidden ui-icon-button is-primary is-compact" data-session-summary-save="true" aria-label="Save summary" title="Save summary">
                                ${SAVE_ICON_SVG}
                            </button>
                            <button type="button" class="hidden ui-icon-button is-compact" data-session-summary-cancel-edit="true" aria-label="Cancel edit" title="Cancel edit">
                                ${CIRCLE_X_ICON_SVG}
                            </button>
                            <button type="button" class="ui-icon-button is-compact" data-session-summary-close="true" aria-label="Close" title="Close">
                                ${X_ICON_SVG}
                            </button>
                        </div>
                    </div>
                    <div id="session-summary-modal-body" class="p-4 text-sm text-txt-primary">
                        <p class="text-txt-secondary">Loading summary details...</p>
                    </div>
                </section>
            `;
            popover.addEventListener('click', (event) => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                if (target.closest('[data-session-summary-back="true"]')) {
                    closeModal();
                    options.onBack();
                    return;
                }
                if (target.closest('[data-session-summary-close="true"]')) {
                    closeModal();
                    return;
                }
                if (target.closest('[data-session-summary-edit="true"]')) {
                    setModalEditing(popover, currentModalSummary());
                    return;
                }
                const saveButton = target.closest('[data-session-summary-save="true"]');
                if (saveButton instanceof HTMLElement) {
                    saveModal(popover, saveButton);
                    return;
                }
                if (target.closest('[data-session-summary-cancel-edit="true"]')) {
                    cancelEdit(popover);
                    return;
                }
                const deleteButton = target.closest('[data-session-summary-delete="true"]');
                if (deleteButton instanceof HTMLElement) {
                    deleteFromModal(popover, deleteButton);
                }
            });
            document.body.appendChild(popover);

            const body = popover.querySelector('#session-summary-modal-body');
            try {
                const summary = await fetchPreview(session);
                if (!body) return;
                popover._sessionSummary = summary;
                body.innerHTML = summary?.has_summary
                    ? renderDetails(summary)
                    : '<p class="text-sm text-txt-secondary">No summary record found for this session.</p>';
            } catch (error) {
                console.error('Error opening session summary modal:', error);
                if (body) {
                    body.innerHTML = '<p class="text-sm state-error">Unable to load summary details.</p>';
                }
            }
        }

        return Object.freeze({
            selectedSessionWithSummary,
            warmPreview,
            openPreview,
            closePreview,
            openModalForSession,
        });
    }

    window.SessionSummary = Object.freeze({
        create: createSessionSummaryController,
    });
})(window, document);
