(function editProposalsModule(window, document) {
    function createEditProposalsController({ state, elements, icons, utils, callbacks }) {
        const { escapeHtml } = utils;
        const renderedRefs = new WeakMap();

        function renderArtifact(container, artifactRef, options = {}) {
            if (!(container instanceof HTMLElement) || !artifactRef) return;
            if (renderedRefs.get(container) === artifactRef) return;
            renderedRefs.set(container, artifactRef);
            container.innerHTML = '<div class="edit-proposal-card edit-proposal-loading">Loading edit proposal...</div>';
            fetchProposal(artifactRef, options)
                .then((proposal) => {
                    if (options.signal?.aborted) return;
                    container.innerHTML = renderProposalCard(proposal);
                    bindProposalCard(container, proposal);
                    autosizeReplacementTextareas(container);
                })
                .catch((error) => {
                    if (error?.name === 'AbortError') {
                        container.replaceChildren();
                        return;
                    }
                    container.innerHTML = `<div class="edit-proposal-card state-error">Unable to load edit proposal: ${escapeHtml(error.message)}</div>`;
                });
        }

        async function fetchProposal(artifactRef, options = {}) {
            const vault = options.vaultName || selectedVault();
            const sessionId = options.sessionId || state.sessionId || '';
            if (!vault || !sessionId) {
                throw new Error('Missing active vault or chat session.');
            }
            const response = await fetch(
                `api/vaults/${encodeURIComponent(vault)}/chat/${encodeURIComponent(sessionId)}/edit-proposals/${encodePathArtifactRef(artifactRef)}`,
                { cache: 'no-store', signal: options.signal }
            );
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }
            return response.json();
        }

        function renderProposalCard(proposal) {
            const edits = Array.isArray(proposal.edits) ? proposal.edits : [];
            const applied = proposal.status === 'applied';
            const denied = proposal.status === 'denied';
            const locked = true;
            const expanded = false;
            return `
                <section class="edit-proposal-card" data-edit-proposal-card="${escapeHtml(proposal.artifact_ref || '')}">
                    <div class="edit-proposal-header">
                        <button type="button" class="edit-proposal-toggle" data-edit-proposal-toggle aria-expanded="${expanded ? 'true' : 'false'}">
                            <span class="edit-proposal-chevron" aria-hidden="true">${expanded ? '▾' : '▸'}</span>
                        </button>
                        <span class="edit-proposal-status">${escapeHtml(proposalStatusLabel(proposal.status))}</span>
                        <button type="button" class="edit-proposal-title-toggle" data-edit-proposal-toggle aria-expanded="${expanded ? 'true' : 'false'}">
                            <span class="edit-proposal-title-block">
                                <span class="edit-proposal-title">${escapeHtml(proposal.title || 'Proposed file edits')}</span>
                                ${proposal.summary ? `<span class="edit-proposal-summary">${escapeHtml(proposal.summary)}</span>` : ''}
                            </span>
                        </button>
                        <div class="edit-proposal-header-actions">
                            <button type="button" class="edit-proposal-bulk-action is-approve" data-edit-proposal-bulk-decision="approve" ${locked ? 'disabled' : ''}>Approve all</button>
                            <button type="button" class="edit-proposal-bulk-action is-deny" data-edit-proposal-bulk-decision="deny" ${locked ? 'disabled' : ''}>Deny all</button>
                        </div>
                    </div>
                    <div class="edit-proposal-body" data-edit-proposal-body ${expanded ? '' : 'hidden'}>
                        <div class="edit-proposal-edits">
                            ${edits.map((edit, index) => renderEdit(edit, index, locked)).join('')}
                        </div>
                        <div class="edit-proposal-footer">
                            <div class="edit-proposal-feedback" data-edit-proposal-feedback></div>
                            <div class="edit-proposal-actions">
                                <button type="button" class="ui-text-button is-primary edit-proposal-submit" data-edit-proposal-submit disabled>
                                    <span>Choose decisions</span>
                                </button>
                            </div>
                        </div>
                    </div>
                </section>
            `;
        }

        function renderEdit(edit, index, applied) {
            const editId = String(edit.edit_id || `edit-${index + 1}`);
            const path = String(edit.path || '');
            const operation = editOperation(edit);
            const canOpenSource = operation !== 'create_file';
            return `
                <article class="edit-proposal-edit" data-edit-proposal-edit="${escapeHtml(editId)}" data-review-decision="pending">
                    <div class="edit-proposal-path-wrap">
                        <div class="edit-proposal-path-line">
                            <span class="edit-proposal-operation-label">${escapeHtml(operationLabel(operation))}</span>
                            ${canOpenSource
                                ? `<button type="button" class="vault-file-link" data-edit-proposal-open-file="${escapeHtml(path)}">@${escapeHtml(path)}</button>`
                                : `<span class="edit-proposal-new-path">@${escapeHtml(path)}</span>`}
                        </div>
                        ${edit.rationale ? `<span class="edit-proposal-rationale">${escapeHtml(edit.rationale)}</span>` : ''}
                    </div>
                    <div class="edit-proposal-row-actions">
                        <button type="button" class="edit-proposal-decision-button is-approve" data-edit-proposal-decision="approve" ${applied ? 'disabled' : ''}>
                            ${icons.CHECK_ICON_SVG || ''}<span>Approve</span>
                        </button>
                        <button type="button" class="edit-proposal-decision-button is-comment" data-edit-proposal-decision="comment" ${applied ? 'disabled' : ''}>
                            <span>Comment</span>
                        </button>
                        <button type="button" class="edit-proposal-decision-button is-deny" data-edit-proposal-decision="deny" ${applied ? 'disabled' : ''}>
                            ${icons.CIRCLE_X_ICON_SVG || icons.X_ICON_SVG || ''}<span>Deny</span>
                        </button>
                    </div>
                    ${renderOperationDetails(edit, editId, applied)}
                    <div class="edit-proposal-comment-block hidden" data-edit-proposal-comment-block>
                        <label class="edit-proposal-label" data-edit-proposal-comment-label for="edit-proposal-comment-${escapeHtml(editId)}">Comment</label>
                        <textarea id="edit-proposal-comment-${escapeHtml(editId)}" class="edit-proposal-comment" data-edit-proposal-comment="${escapeHtml(editId)}" spellcheck="true" ${applied ? 'disabled' : ''}></textarea>
                    </div>
                </article>
            `;
        }

        function renderOperationDetails(edit, editId, applied) {
            const operation = editOperation(edit);
            if (operation === 'create_file') {
                return `
                    <div class="edit-proposal-diff is-single">
                        <div>
                            <div class="edit-proposal-label">Content</div>
                            <textarea data-edit-proposal-replacement="${escapeHtml(editId)}" spellcheck="false" ${applied ? 'disabled' : ''}>${escapeHtml(edit.replacement_text || '')}</textarea>
                        </div>
                    </div>
                `;
            }
            if (operation === 'delete_file') {
                return `
                    <div class="edit-proposal-delete-note">This will delete the selected file.</div>
                `;
            }
            if (operation === 'move_file') {
                return `
                    <div class="edit-proposal-diff is-single">
                        <div>
                            <div class="edit-proposal-field-header">
                                <div class="edit-proposal-label">Destination</div>
                            </div>
                            <div class="edit-proposal-path-field">
                                <input type="text" class="edit-proposal-path-input" data-edit-proposal-replacement="${escapeHtml(editId)}" spellcheck="false" value="${escapeHtml(edit.destination || edit.replacement_text || '')}" ${applied ? 'disabled' : ''} />
                                <button type="button" class="workspace-icon-button edit-proposal-path-picker-button" data-edit-proposal-pick-destination="${escapeHtml(editId)}" aria-label="Choose destination path" title="Choose destination path" ${applied ? 'disabled' : ''}>${icons.FOLDER_ICON_SVG || ''}</button>
                            </div>
                        </div>
                    </div>
                `;
            }
            return `
                <div class="edit-proposal-diff is-single">
                    <div>
                        <div class="edit-proposal-label">Replacement</div>
                        <textarea data-edit-proposal-replacement="${escapeHtml(editId)}" spellcheck="false" ${applied ? 'disabled' : ''}>${escapeHtml(edit.replacement_text || '')}</textarea>
                    </div>
                </div>
                <details class="edit-proposal-original">
                    <summary>Original</summary>
                    <pre>${escapeHtml(edit.original_text || '')}</pre>
                </details>
            `;
        }

        function editOperation(edit) {
            return String(edit.operation || 'replace_text');
        }

        function operationLabel(operation) {
            if (operation === 'create_file') return 'Create';
            if (operation === 'delete_file') return 'Delete';
            if (operation === 'move_file') return 'Move';
            return 'Edit';
        }

        function pickMoveDestination(container, button) {
            if (typeof callbacks.openPathPicker !== 'function') return;
            const row = button.closest('[data-edit-proposal-edit]');
            if (!(row instanceof HTMLElement)) return;
            const editId = row.getAttribute('data-edit-proposal-edit') || '';
            const textarea = row.querySelector(`[data-edit-proposal-replacement="${cssEscape(editId)}"]`);
            const sourcePath = row.querySelector('[data-edit-proposal-open-file]')?.getAttribute('data-edit-proposal-open-file') || '';
            callbacks.openPathPicker({
                id: 'edit-proposal-destination-picker-modal',
                title: 'Move Destination',
                mode: 'files',
                subtitle: sourcePath ? `Move ${sourcePath}` : 'Choose destination',
                searchPlaceholder: 'Search destination folders or files...',
                initialScope: 'vault',
                onSelect: ({ path, kind }) => {
                    if (!(textarea instanceof HTMLTextAreaElement || textarea instanceof HTMLInputElement)) return;
                    textarea.value = kind === 'directory'
                        ? joinVaultPath(path, basename(sourcePath))
                        : path;
                    if (textarea instanceof HTMLTextAreaElement) {
                        autosizeTextarea(textarea);
                    }
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
            if (!cleanDirectory) return cleanName;
            if (!cleanName) return cleanDirectory;
            return `${cleanDirectory}/${cleanName}`;
        }

        function bindProposalCard(container, proposal) {
            container.addEventListener('click', async (event) => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                const fileButton = target.closest('[data-edit-proposal-open-file]');
                if (fileButton instanceof HTMLElement) {
                    callbacks.openFile?.(fileButton.getAttribute('data-edit-proposal-open-file') || '');
                    return;
                }
                const destinationButton = target.closest('[data-edit-proposal-pick-destination]');
                if (destinationButton instanceof HTMLButtonElement) {
                    pickMoveDestination(container, destinationButton);
                    return;
                }
                const toggleButton = target.closest('[data-edit-proposal-toggle]');
                if (toggleButton instanceof HTMLButtonElement) {
                    toggleProposalBody(container, toggleButton);
                    return;
                }
                const decisionButton = target.closest('[data-edit-proposal-decision]');
                if (decisionButton instanceof HTMLButtonElement) {
                    setRowDecision(container, decisionButton);
                    return;
                }
                const bulkDecisionButton = target.closest('[data-edit-proposal-bulk-decision]');
                if (bulkDecisionButton instanceof HTMLButtonElement) {
                    setAllDecisions(container, bulkDecisionButton.getAttribute('data-edit-proposal-bulk-decision') || 'pending');
                    return;
                }
                const submitButton = target.closest('[data-edit-proposal-submit]');
                if (submitButton instanceof HTMLButtonElement) {
                    await submitReview(container, proposal, submitButton);
                }
            });
            container.addEventListener('input', (event) => {
                const target = event.target;
                if (target instanceof HTMLTextAreaElement && target.matches('[data-edit-proposal-replacement], [data-edit-proposal-comment]')) {
                    autosizeTextarea(target);
                    updateSubmitButton(container);
                } else if (target instanceof HTMLInputElement && target.matches('[data-edit-proposal-replacement]')) {
                    updateSubmitButton(container);
                }
            });
            updateSubmitButton(container);
        }

        async function postApprovedEdits(proposal, decisions) {
            const approved = decisions.filter((decision) => decision.decision === 'approve');
            const selected = approved.map((decision) => decision.editId);
            const overrides = {};
            approved.forEach((decision) => {
                overrides[decision.editId] = decision.replacementText;
            });
            const response = await fetch(
                `api/vaults/${encodeURIComponent(selectedVault())}/chat/${encodeURIComponent(state.sessionId || '')}/edit-proposals/${encodePathArtifactRef(proposal.artifact_ref || '')}/apply`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        selected_edit_ids: selected,
                        replacement_overrides: overrides,
                    }),
                }
            );
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }
            return response.json();
        }

        async function applyApproved(container, proposal, decisions, button) {
            const feedback = container.querySelector('[data-edit-proposal-feedback]');
            button.disabled = true;
            setFeedback(feedback, 'Applying approved edits...', 'info');
            try {
                await postApprovedEdits(proposal, decisions);
                setFeedback(feedback, 'Applied approved edits.', 'success');
                setProposalLocked(container, 'Applied');
                callbacks.enhanceFileLinks?.(container);
            } catch (error) {
                button.disabled = false;
                setFeedback(feedback, error.message, 'error');
            }
        }

        async function submitReview(container, proposal, button) {
            const feedback = container.querySelector('[data-edit-proposal-feedback]');
            const decisions = collectDecisions(container, proposal);
            if (!decisions.length) {
                setFeedback(feedback, 'Choose at least one review action.', 'error');
                return;
            }
            const approvedDecisions = decisions.filter((decision) => decision.decision === 'approve');
            const reviewDecisions = decisions.filter((decision) => decision.decision !== 'approve');
            if (decisions.every((decision) => decision.decision === 'approve')) {
                await applyApproved(container, proposal, decisions, button);
                return;
            }
            if (typeof callbacks.submitReview !== 'function' || state.isLoading) {
                setFeedback(feedback, 'Wait for the current response to finish, then submit your choices.', 'error');
                return;
            }
            button.disabled = true;
            try {
                setFeedback(feedback, approvedDecisions.length ? 'Applying approved edits and sending review...' : 'Sending review...', 'info');
                const result = await callbacks.submitReview({ proposal, decisions });
                if (!result) {
                    throw new Error('Review could not be sent because chat is busy or unavailable.');
                }
                setFeedback(feedback, result.applied_edit_ids?.length ? 'Approved edits applied. Review sent.' : 'Review sent.', 'success');
                setProposalLocked(container, 'Reviewed');
            } catch (error) {
                button.disabled = false;
                setFeedback(feedback, error.message || 'Unable to send review.', 'error');
            }
        }

        function setProposalLocked(container, statusLabel) {
            const status = container.querySelector('.edit-proposal-status');
            if (status) status.textContent = statusLabel;
            setProposalCollapsed(container, true);
            container.querySelectorAll('textarea, input[data-edit-proposal-replacement], [data-edit-proposal-bulk-decision], [data-edit-proposal-decision], [data-edit-proposal-submit]').forEach((item) => {
                item.disabled = true;
            });
        }

        function setRowDecision(container, button) {
            const row = button.closest('[data-edit-proposal-edit]');
            if (!(row instanceof HTMLElement)) return;
            const requested = button.getAttribute('data-edit-proposal-decision') || 'pending';
            const current = row.getAttribute('data-review-decision') || 'pending';
            const decision = current === requested ? 'pending' : requested;
            row.setAttribute('data-review-decision', decision);
            row.querySelectorAll('[data-edit-proposal-decision]').forEach((item) => {
                if (item instanceof HTMLElement) {
                    item.classList.toggle('is-active', item.getAttribute('data-edit-proposal-decision') === decision);
                }
            });
            const commentBlock = row.querySelector('[data-edit-proposal-comment-block]');
            if (commentBlock instanceof HTMLElement) {
                commentBlock.classList.toggle('hidden', !['comment', 'deny'].includes(decision));
            }
            updateCommentLabel(row, decision);
            updateSubmitButton(container);
        }

        function setAllDecisions(container, decision) {
            if (!['approve', 'deny'].includes(decision)) return;
            container.querySelectorAll('[data-edit-proposal-edit]').forEach((row) => {
                if (!(row instanceof HTMLElement)) return;
                row.setAttribute('data-review-decision', decision);
                row.querySelectorAll('[data-edit-proposal-decision]').forEach((item) => {
                    if (item instanceof HTMLElement) {
                        item.classList.toggle('is-active', item.getAttribute('data-edit-proposal-decision') === decision);
                    }
                });
                const commentBlock = row.querySelector('[data-edit-proposal-comment-block]');
                if (commentBlock instanceof HTMLElement) {
                    commentBlock.classList.toggle('hidden', decision !== 'deny');
                }
                updateCommentLabel(row, decision);
            });
            updateSubmitButton(container);
        }

        function toggleProposalBody(container, toggleButton) {
            const body = container.querySelector('[data-edit-proposal-body]');
            if (!(body instanceof HTMLElement)) return;
            const expanded = toggleButton.getAttribute('aria-expanded') !== 'false';
            setProposalCollapsed(container, expanded);
        }

        function setProposalCollapsed(container, collapsed) {
            const body = container.querySelector('[data-edit-proposal-body]');
            if (body instanceof HTMLElement) {
                body.hidden = collapsed;
            }
            container.querySelectorAll('[data-edit-proposal-toggle]').forEach((toggle) => {
                if (!(toggle instanceof HTMLButtonElement)) return;
                toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
                const chevron = toggle.querySelector('.edit-proposal-chevron');
                if (chevron) chevron.textContent = collapsed ? '▸' : '▾';
            });
        }

        function autosizeReplacementTextareas(container) {
            container.querySelectorAll('[data-edit-proposal-replacement], [data-edit-proposal-comment]').forEach((textarea) => {
                if (textarea instanceof HTMLTextAreaElement) {
                    autosizeTextarea(textarea);
                }
            });
        }

        function autosizeTextarea(textarea) {
            textarea.style.height = 'auto';
            textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 68), 260)}px`;
        }

        function collectDecisions(container, proposal) {
            const editsById = new Map((proposal.edits || []).map((edit) => [String(edit.edit_id || ''), edit]));
            return Array.from(container.querySelectorAll('[data-edit-proposal-edit]'))
                .map((row) => {
                    if (!(row instanceof HTMLElement)) return null;
                    const editId = row.getAttribute('data-edit-proposal-edit') || '';
                    const decision = row.getAttribute('data-review-decision') || 'pending';
                    if (decision === 'pending') return null;
                    const replacement = row.querySelector(`[data-edit-proposal-replacement="${cssEscape(editId)}"]`);
                    const comment = row.querySelector(`[data-edit-proposal-comment="${cssEscape(editId)}"]`);
                    return {
                        editId,
                        decision,
                        edit: editsById.get(editId) || {},
                        replacementText: replacement instanceof HTMLTextAreaElement || replacement instanceof HTMLInputElement ? replacement.value : '',
                        comment: comment instanceof HTMLTextAreaElement ? comment.value.trim() : '',
                    };
                })
                .filter(Boolean);
        }

        function updateSubmitButton(container) {
            const button = container.querySelector('[data-edit-proposal-submit]');
            if (!(button instanceof HTMLButtonElement)) return;
            const decisions = Array.from(container.querySelectorAll('[data-edit-proposal-edit]'))
                .map((row) => row instanceof HTMLElement ? row.getAttribute('data-review-decision') || 'pending' : 'pending')
                .filter((decision) => decision !== 'pending');
            button.disabled = decisions.length === 0;
            const label = button.querySelector('span') || button;
            if (!decisions.length) {
                label.textContent = 'Choose decisions';
            } else {
                label.textContent = 'Submit choices';
            }
        }

        function rowDecisionLabel(decision) {
            if (decision === 'approve') return 'Approved';
            if (decision === 'comment') return 'Comment';
            if (decision === 'deny') return 'Denied';
            return 'Pending';
        }

        function updateCommentLabel(row, decision) {
            const label = row.querySelector('[data-edit-proposal-comment-label]');
            if (label) {
                label.textContent = decision === 'deny' ? 'Reason (optional)' : 'Comment';
            }
        }

        function cssEscape(value) {
            if (window.CSS && typeof window.CSS.escape === 'function') {
                return window.CSS.escape(value);
            }
            return String(value || '').replace(/["\\]/g, '\\$&');
        }

        function setFeedback(element, message, kind) {
            if (!element) return;
            element.textContent = message || '';
            element.className = `edit-proposal-feedback ${kind ? `state-${kind}` : ''}`;
        }

        function proposalStatusLabel(status) {
            if (status === 'applied') return 'Applied';
            if (status === 'denied') return 'Denied';
            return 'Historical';
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

        return Object.freeze({
            renderArtifact,
        });
    }

    window.EditProposals = Object.freeze({
        create: createEditProposalsController,
    });
})(window, document);
