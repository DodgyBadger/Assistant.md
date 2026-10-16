(function deferredReviewsModule(window, document) {
    function createDeferredReviewsController({ state, elements, icons, utils, callbacks }) {
        const { escapeHtml } = utils;
        const renderedRefs = new WeakMap();

        function renderArtifact(container, artifactRef) {
            if (!(container instanceof HTMLElement) || !artifactRef) return;
            if (renderedRefs.get(container) === artifactRef) return;
            renderedRefs.set(container, artifactRef);
            container.innerHTML = '<div class="edit-proposal-card edit-proposal-loading">Loading review...</div>';
            fetchReview(artifactRef)
                .then((review) => {
                    container.innerHTML = renderReviewCard(review);
                    bindReviewCard(container, review);
                    autosizeTextareas(container);
                    syncPendingReviewWidth(container, review.status === 'pending');
                })
                .catch((error) => {
                    syncPendingReviewWidth(container, false);
                    container.innerHTML = `<div class="edit-proposal-card state-error">Unable to load review: ${escapeHtml(error.message)}</div>`;
                });
        }

        function renderReviewEvent(container, payload) {
            if (!(container instanceof HTMLElement) || !payload?.artifact_ref) return;
            container.innerHTML = renderReviewCard(payload);
            bindReviewCard(container, payload);
            autosizeTextareas(container);
            syncPendingReviewWidth(container, !payload.status || payload.status === 'pending');
        }

        async function fetchReview(artifactRef) {
            const vault = selectedVault();
            const sessionId = state.sessionId || '';
            if (!vault || !sessionId) {
                throw new Error('Missing active vault or chat session.');
            }
            const response = await fetch(
                `api/vaults/${encodeURIComponent(vault)}/chat/${encodeURIComponent(sessionId)}/deferred-reviews/${encodePathArtifactRef(artifactRef)}`
            );
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }
            return response.json();
        }

        function renderReviewCard(review) {
            const approvals = Array.isArray(review.approvals) ? review.approvals : [];
            const locked = review.status && review.status !== 'pending';
            const expanded = !locked;
            return `
                <section class="edit-proposal-card" data-deferred-review-card="${escapeHtml(review.artifact_ref || '')}">
                    <div class="edit-proposal-header">
                        <button type="button" class="edit-proposal-toggle" data-deferred-review-toggle aria-expanded="${expanded ? 'true' : 'false'}">
                            <span class="edit-proposal-chevron" aria-hidden="true">${expanded ? '▾' : '▸'}</span>
                        </button>
                        <span class="edit-proposal-status">${escapeHtml(reviewStatusLabel(review.status))}</span>
                        <button type="button" class="edit-proposal-title-toggle" data-deferred-review-toggle aria-expanded="${expanded ? 'true' : 'false'}">
                            <span class="edit-proposal-title-block">
                                <span class="edit-proposal-title">Review file changes</span>
                                <span class="edit-proposal-summary">${escapeHtml(approvals.length === 1 ? approvalSummary(approvals[0]) : `${approvals.length} tool calls`)}</span>
                            </span>
                        </button>
                        <div class="edit-proposal-header-actions">
                            <button type="button" class="edit-proposal-bulk-action is-approve" data-deferred-review-bulk-decision="approve" ${locked ? 'disabled' : ''}>Approve all</button>
                            <button type="button" class="edit-proposal-bulk-action is-deny" data-deferred-review-bulk-decision="deny" ${locked ? 'disabled' : ''}>Deny all</button>
                        </div>
                    </div>
                    <div class="edit-proposal-body" data-deferred-review-body ${expanded ? '' : 'hidden'}>
                        <div class="edit-proposal-edits">
                            ${approvals.map((call) => renderReviewCall(call, locked)).join('')}
                        </div>
                        <div class="edit-proposal-footer">
                            <div class="edit-proposal-feedback" data-deferred-review-feedback></div>
                            <div class="edit-proposal-actions">
                                <button type="button" class="ui-text-button is-primary edit-proposal-submit" data-deferred-review-submit disabled>
                                    <span>Choose decisions</span>
                                </button>
                            </div>
                        </div>
                    </div>
                </section>
            `;
        }

        function renderReviewCall(call, locked) {
            const toolCallId = String(call.tool_call_id || '');
            const toolName = String(call.tool_name || '');
            const args = normalizeArgs(call.args);
            const path = String(args.path || '');
            return `
                <article class="edit-proposal-edit" data-deferred-review-call="${escapeHtml(toolCallId)}" data-review-decision="pending" data-tool-name="${escapeHtml(toolName)}">
                    <div class="edit-proposal-path-wrap">
                        <div class="edit-proposal-path-line">
                            <span class="edit-proposal-operation-label">${escapeHtml(toolOperationLabel(toolName, args))}</span>
                            ${path ? `<button type="button" class="vault-file-link" data-deferred-review-open-file="${escapeHtml(path)}">@${escapeHtml(path)}</button>` : `<span class="edit-proposal-new-path">${escapeHtml(toolName)}</span>`}
                        </div>
                    </div>
                    <div class="edit-proposal-row-actions">
                        <button type="button" class="edit-proposal-decision-button is-approve" data-deferred-review-decision="approve" ${locked ? 'disabled' : ''}>
                            ${icons.CHECK_ICON_SVG || ''}<span>Approve</span>
                        </button>
                        <button type="button" class="edit-proposal-decision-button is-deny" data-deferred-review-decision="deny" ${locked ? 'disabled' : ''}>
                            ${icons.CIRCLE_X_ICON_SVG || icons.X_ICON_SVG || ''}<span>Deny</span>
                        </button>
                    </div>
                    ${renderCallFields(toolName, toolCallId, args, locked)}
                    <div class="edit-proposal-comment-block hidden" data-deferred-review-deny-block>
                        <label class="edit-proposal-label" for="deferred-review-deny-${escapeHtml(toolCallId)}">Reason (optional)</label>
                        <textarea id="deferred-review-deny-${escapeHtml(toolCallId)}" class="edit-proposal-comment" data-deferred-review-message="${escapeHtml(toolCallId)}" spellcheck="true" ${locked ? 'disabled' : ''}></textarea>
                    </div>
                </article>
            `;
        }

        function renderCallFields(toolName, toolCallId, args, locked) {
            if (toolName === 'file_write') {
                return renderFileOpsFields(toolCallId, args, locked);
            }
            return `<pre class="tool-status-block">${escapeHtml(JSON.stringify(args, null, 2))}</pre>`;
        }

        function renderFileOpsFields(toolCallId, args, locked) {
            const operation = String(args.operation || '').toLowerCase();
            if (operation === 'write') {
                return `
                    <div class="edit-proposal-diff is-single">
                        <div>
                            <div class="edit-proposal-label">${args.overwrite ? 'Replacement content' : 'Content'}</div>
                            <textarea data-deferred-review-arg="${escapeHtml(toolCallId)}" data-arg-name="content" spellcheck="false" ${locked ? 'disabled' : ''}>${escapeHtml(String(args.content || ''))}</textarea>
                        </div>
                    </div>
                `;
            }
            if (operation === 'append') {
                return `
                    <div class="edit-proposal-diff is-single">
                        <div>
                            <div class="edit-proposal-label">Append content</div>
                            <textarea data-deferred-review-arg="${escapeHtml(toolCallId)}" data-arg-name="content" spellcheck="false" ${locked ? 'disabled' : ''}>${escapeHtml(String(args.content || ''))}</textarea>
                        </div>
                    </div>
                `;
            }
            if (operation === 'replace_text') {
                return `
                    <div class="edit-proposal-diff is-single">
                        <div>
                            <div class="edit-proposal-label">Revision</div>
                            <textarea data-deferred-review-arg="${escapeHtml(toolCallId)}" data-arg-name="new_text" spellcheck="false" ${locked ? 'disabled' : ''}>${escapeHtml(String(args.new_text || ''))}</textarea>
                        </div>
                        <details class="edit-proposal-original">
                            <summary>Before</summary>
                            <pre>${escapeHtml(String(args.old_text || ''))}</pre>
                        </details>
                    </div>
                `;
            }
            if (operation === 'edit_line') {
                return `
                    <div class="edit-proposal-diff is-single">
                        <div>
                            <div class="edit-proposal-label">Revision</div>
                            <textarea data-deferred-review-arg="${escapeHtml(toolCallId)}" data-arg-name="new_text" spellcheck="false" ${locked ? 'disabled' : ''}>${escapeHtml(String(args.new_text || ''))}</textarea>
                        </div>
                        <details class="edit-proposal-original">
                            <summary>Before</summary>
                            <pre>${escapeHtml(String(args.old_text || ''))}</pre>
                        </details>
                    </div>
                `;
            }
            if (operation === 'move') {
                return `
                    <div class="edit-proposal-path-edit">
                        <label class="edit-proposal-label" for="deferred-review-destination-${escapeHtml(toolCallId)}">Destination</label>
                        <div class="edit-proposal-path-field">
                            <input id="deferred-review-destination-${escapeHtml(toolCallId)}" class="edit-proposal-path-input" type="text" data-deferred-review-arg="${escapeHtml(toolCallId)}" data-arg-name="destination" value="${escapeHtml(String(args.destination || ''))}" ${locked ? 'disabled' : ''} />
                            <button type="button" class="workspace-icon-button edit-proposal-path-picker-button" data-deferred-review-pick-destination="${escapeHtml(toolCallId)}" aria-label="Choose destination path" title="Choose destination path" ${locked ? 'disabled' : ''}>${icons.FOLDER_ICON_SVG || ''}</button>
                        </div>
                    </div>
                `;
            }
            if (operation === 'mkdir') {
                return `
                    <div class="edit-proposal-path-edit">
                        <label class="edit-proposal-label" for="deferred-review-folder-${escapeHtml(toolCallId)}">Folder path</label>
                        <input id="deferred-review-folder-${escapeHtml(toolCallId)}" class="edit-proposal-path-input" type="text" data-deferred-review-arg="${escapeHtml(toolCallId)}" data-arg-name="path" value="${escapeHtml(String(args.path || ''))}" ${locked ? 'disabled' : ''} />
                    </div>
                `;
            }
            if (operation === 'delete') {
                return `
                    <div class="edit-proposal-delete-note">${escapeHtml(fileOpsStaticDescription(operation, args))}</div>
                `;
            }
            return `
                <div class="edit-proposal-diff is-single">
                    <div>
                        <div class="edit-proposal-label">Arguments</div>
                        <textarea data-deferred-review-arg="${escapeHtml(toolCallId)}" data-arg-name="__json_args" data-arg-type="json-object" spellcheck="false" ${locked ? 'disabled' : ''}>${escapeHtml(JSON.stringify(args, null, 2))}</textarea>
                    </div>
                </div>
            `;
        }

        function pickMoveDestination(container, button) {
            if (typeof callbacks.openPathPicker !== 'function') return;
            const row = button.closest('[data-deferred-review-call]');
            if (!(row instanceof HTMLElement)) return;
            const toolCallId = row.dataset.deferredReviewCall || '';
            const input = row.querySelector(
                `[data-deferred-review-arg="${cssEscape(toolCallId)}"][data-arg-name="destination"]`
            );
            const sourcePath = row.querySelector('[data-deferred-review-open-file]')
                ?.getAttribute('data-deferred-review-open-file') || '';
            callbacks.openPathPicker({
                id: 'deferred-review-destination-picker-modal',
                title: 'Move Destination',
                mode: 'files',
                subtitle: sourcePath ? `Move ${sourcePath}` : 'Choose destination',
                initialScope: 'vault',
                onSelect: ({ path, kind }) => {
                    if (!(input instanceof HTMLInputElement)) return;
                    input.value = kind === 'directory'
                        ? joinVaultPath(path, basename(sourcePath))
                        : path;
                    updateSubmitButton(container);
                },
            });
        }

        function basename(path) {
            return String(path || '').split('/').filter(Boolean).pop() || '';
        }

        function joinVaultPath(directory, name) {
            const cleanDirectory = String(directory || '').replace(/\/+$/, '');
            const cleanName = String(name || '').replace(/^\/+/, '');
            return cleanDirectory ? `${cleanDirectory}/${cleanName}` : cleanName;
        }

        function bindReviewCard(container, review) {
            container.addEventListener('click', async (event) => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                const fileButton = target.closest('[data-deferred-review-open-file]');
                if (fileButton instanceof HTMLButtonElement) {
                    callbacks.openFile?.(fileButton.dataset.deferredReviewOpenFile || '');
                    return;
                }
                const destinationButton = target.closest('[data-deferred-review-pick-destination]');
                if (destinationButton instanceof HTMLButtonElement) {
                    pickMoveDestination(container, destinationButton);
                    return;
                }
                const toggle = target.closest('[data-deferred-review-toggle]');
                if (toggle instanceof HTMLButtonElement) {
                    toggleReviewBody(container, toggle);
                    return;
                }
                const decisionButton = target.closest('[data-deferred-review-decision]');
                if (decisionButton instanceof HTMLButtonElement) {
                    setRowDecision(container, decisionButton);
                    return;
                }
                const bulkButton = target.closest('[data-deferred-review-bulk-decision]');
                if (bulkButton instanceof HTMLButtonElement) {
                    setAllDecisions(container, bulkButton.getAttribute('data-deferred-review-bulk-decision') || 'pending');
                    return;
                }
                const submitButton = target.closest('[data-deferred-review-submit]');
                if (submitButton instanceof HTMLButtonElement) {
                    await submitReview(container, review, submitButton);
                }
            });
            container.addEventListener('input', (event) => {
                if (event.target instanceof HTMLTextAreaElement) {
                    autosizeTextarea(event.target);
                    updateSubmitButton(container);
                }
            });
            updateSubmitButton(container);
        }

        async function submitReview(container, review, button) {
            const feedback = container.querySelector('[data-deferred-review-feedback]');
            const decisions = collectDecisions(container);
            if (!decisions.length) {
                setFeedback(feedback, 'Choose at least one review action.', 'error');
                return;
            }
            button.disabled = true;
            setFeedback(feedback, 'Submitting review...', 'info');
            try {
                const result = await postReview(review, decisions);
                setFeedback(feedback, 'Review submitted.', 'success');
                setReviewLocked(container, 'Submitted');
                callbacks.reviewSubmitted?.(result);
                if (result?.task) {
                    const streamed = await callbacks.streamStartedTask?.(result);
                    if (streamed === false) {
                        setFeedback(feedback, 'Review submitted, but the follow-up response could not be streamed.', 'error');
                    }
                }
            } catch (error) {
                button.disabled = false;
                setFeedback(feedback, error.message || 'Unable to submit review.', 'error');
            }
        }

        async function postReview(review, decisions) {
            const response = await fetch(
                `api/vaults/${encodeURIComponent(selectedVault())}/chat/${encodeURIComponent(state.sessionId || '')}/deferred-reviews/${encodePathArtifactRef(review.artifact_ref || '')}/submit`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ decisions }),
                }
            );
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }
            return response.json();
        }

        function collectDecisions(container) {
            return Array.from(container.querySelectorAll('[data-deferred-review-call]'))
                .map((row) => {
                    if (!(row instanceof HTMLElement)) return null;
                    const toolCallId = row.getAttribute('data-deferred-review-call') || '';
                    const decision = row.getAttribute('data-review-decision') || 'pending';
                    if (decision === 'pending') return null;
                    const payload = { tool_call_id: toolCallId, decision };
                    if (decision === 'approve') {
                        payload.override_args = collectOverrideArgs(row, toolCallId);
                    } else {
                        const message = row.querySelector(`[data-deferred-review-message="${cssEscape(toolCallId)}"]`);
                        payload.message = message instanceof HTMLTextAreaElement ? message.value.trim() : '';
                    }
                    return payload;
                })
                .filter(Boolean);
        }

        function collectOverrideArgs(row, toolCallId) {
            const args = {};
            row.querySelectorAll(`[data-deferred-review-arg="${cssEscape(toolCallId)}"]`).forEach((field) => {
                if (!(field instanceof HTMLTextAreaElement || field instanceof HTMLInputElement)) return;
                const name = field.getAttribute('data-arg-name') || '';
                if (!name) return;
                if (name === '__json_args') {
                    Object.assign(args, parseJsonArg(field.value, {}));
                    return;
                }
                args[name] = parseTypedArg(field.value, field.getAttribute('data-arg-type') || 'string');
            });
            return args;
        }

        function setRowDecision(container, button) {
            const row = button.closest('[data-deferred-review-call]');
            if (!(row instanceof HTMLElement)) return;
            const requested = button.getAttribute('data-deferred-review-decision') || 'pending';
            const current = row.getAttribute('data-review-decision') || 'pending';
            const decision = current === requested ? 'pending' : requested;
            row.setAttribute('data-review-decision', decision);
            row.querySelectorAll('[data-deferred-review-decision]').forEach((item) => {
                if (item instanceof HTMLElement) {
                    item.classList.toggle('is-active', item.getAttribute('data-deferred-review-decision') === decision);
                }
            });
            const denyBlock = row.querySelector('[data-deferred-review-deny-block]');
            if (denyBlock instanceof HTMLElement) {
                denyBlock.classList.toggle('hidden', decision !== 'deny');
            }
            updateSubmitButton(container);
        }

        function setAllDecisions(container, decision) {
            if (!['approve', 'deny'].includes(decision)) return;
            container.querySelectorAll('[data-deferred-review-call]').forEach((row) => {
                if (!(row instanceof HTMLElement)) return;
                row.setAttribute('data-review-decision', decision);
                row.querySelectorAll('[data-deferred-review-decision]').forEach((item) => {
                    if (item instanceof HTMLElement) {
                        item.classList.toggle('is-active', item.getAttribute('data-deferred-review-decision') === decision);
                    }
                });
                const denyBlock = row.querySelector('[data-deferred-review-deny-block]');
                if (denyBlock instanceof HTMLElement) {
                    denyBlock.classList.toggle('hidden', decision !== 'deny');
                }
            });
            updateSubmitButton(container);
        }

        function toggleReviewBody(container, toggleButton) {
            const expanded = toggleButton.getAttribute('aria-expanded') !== 'false';
            setReviewCollapsed(container, expanded);
        }

        function setReviewCollapsed(container, collapsed) {
            const body = container.querySelector('[data-deferred-review-body]');
            if (body instanceof HTMLElement) body.hidden = collapsed;
            container.querySelectorAll('[data-deferred-review-toggle]').forEach((toggle) => {
                if (!(toggle instanceof HTMLButtonElement)) return;
                toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
                const chevron = toggle.querySelector('.edit-proposal-chevron');
                if (chevron) chevron.textContent = collapsed ? '▸' : '▾';
            });
        }

        function setReviewLocked(container, statusLabel) {
            const status = container.querySelector('.edit-proposal-status');
            if (status) status.textContent = statusLabel;
            syncPendingReviewWidth(container, false);
            setReviewCollapsed(container, true);
            container.querySelectorAll('textarea, input, [data-deferred-review-bulk-decision], [data-deferred-review-decision], [data-deferred-review-submit]').forEach((item) => {
                item.disabled = true;
            });
        }

        function updateSubmitButton(container) {
            const button = container.querySelector('[data-deferred-review-submit]');
            if (!(button instanceof HTMLButtonElement)) return;
            const rows = Array.from(container.querySelectorAll('[data-deferred-review-call]'))
                .filter((row) => row instanceof HTMLElement);
            const selected = rows.filter((row) => (row.getAttribute('data-review-decision') || 'pending') !== 'pending');
            button.disabled = rows.length === 0 || selected.length !== rows.length;
            const label = button.querySelector('span') || button;
            label.textContent = selected.length === rows.length && rows.length ? 'Submit choices' : 'Choose all decisions';
        }

        function syncPendingReviewWidth(container, pending) {
            container.closest('.message-bubble')?.classList.toggle(
                'message-has-pending-review',
                Boolean(pending)
            );
        }

        function autosizeTextareas(container) {
            container.querySelectorAll('textarea').forEach((textarea) => {
                if (textarea instanceof HTMLTextAreaElement) autosizeTextarea(textarea);
            });
        }

        function autosizeTextarea(textarea) {
            textarea.style.height = 'auto';
            textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 68), 260)}px`;
        }

        function normalizeArgs(args) {
            if (!args) return {};
            if (typeof args === 'string') {
                try {
                    const parsed = JSON.parse(args);
                    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
                } catch {
                    return {};
                }
            }
            return typeof args === 'object' && !Array.isArray(args) ? args : {};
        }

        function approvalSummary(call) {
            const args = normalizeArgs(call?.args);
            if (call?.tool_name === 'file_write') {
                const operation = String(args.operation || 'file_write').toUpperCase();
                return args.path ? `${operation} @${args.path}` : operation;
            }
            return call?.tool_name || 'Deferred tool call';
        }

        function toolOperationLabel(toolName, args = {}) {
            if (toolName === 'file_write') return String(args.operation || 'file_write').toUpperCase();
            return 'Review';
        }

        function fileOpsStaticDescription(operation, args) {
            if (operation === 'delete') return `Delete @${String(args.path || '')}`;
            if (operation === 'mkdir') return `Create folder @${String(args.path || '')}`;
            return operation || 'file operation';
        }

        function parseTypedArg(value, type) {
            if (type === 'boolean') return value === 'true';
            if (type === 'number') return Number(value);
            if (type === 'json') return parseJsonArg(value, []);
            if (type === 'json-object') return parseJsonArg(value, {});
            return value;
        }

        function parseJsonArg(value, fallback) {
            try {
                return JSON.parse(value);
            } catch {
                return fallback;
            }
        }

        function reviewStatusLabel(status) {
            if (status === 'resuming') return 'Resuming';
            if (status === 'completed') return 'Applied';
            if (status === 'failed') return 'Failed';
            if (status === 'cancelled') return 'Cancelled';
            if (status === 'submitted') return 'Submitted';
            return 'Pending';
        }

        function setFeedback(element, message, kind) {
            if (!element) return;
            element.textContent = message || '';
            element.className = `edit-proposal-feedback ${kind ? `state-${kind}` : ''}`;
        }

        function selectedVault() {
            return elements.vaultSelector?.value || '';
        }

        function encodePathArtifactRef(value) {
            return String(value || '')
                .split('/')
                .map((part) => encodeURIComponent(part))
                .join('/');
        }

        function cssEscape(value) {
            if (window.CSS && typeof window.CSS.escape === 'function') {
                return window.CSS.escape(value);
            }
            return String(value || '').replace(/["\\]/g, '\\$&');
        }

        return Object.freeze({
            renderArtifact,
            renderReviewEvent,
        });
    }

    window.DeferredReviews = Object.freeze({
        create: createDeferredReviewsController,
    });
})(window, document);
