/** Tool-call detail modal state, loading, and structured presentation. */
(function chatToolDetailsModule(window, document) {
    function createChatToolDetailsController({ state, elements, icons, utils, callbacks }) {
        let activeToolDetailEntry = null;

            function formatToolDetail(value) {
                value = normalizeToolDisplayValue(value);
                if (value === undefined || value === null) return '';
                return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
            }

            function normalizeToolDisplayValue(value) {
                if (typeof value !== 'string') return value;
                const trimmed = value.trim();
                if (!trimmed) return '';
                if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return value;
                try {
                    return JSON.parse(trimmed);
                } catch {
                    return value;
                }
            }

            function isEmptyToolValue(value) {
                value = normalizeToolDisplayValue(value);
                if (value === undefined || value === null) return true;
                if (typeof value === 'string') return value.trim() === '';
                if (Array.isArray(value)) return value.every(isEmptyToolValue);
                if (typeof value === 'object') {
                    const entries = Object.entries(value);
                    return entries.length === 0 || entries.every(([, item]) => isEmptyToolValue(item));
                }
                return false;
            }

            function updateToolDetail(entry) {
                if (!entry) return;
                entry.line.innerHTML = '';
                const name = document.createElement('span');
                name.className = 'tool-status-name';
                name.textContent = entry.toolName;
                entry.line.appendChild(name);
                if (entry.tokenCount !== null) {
                    const count = document.createElement('span');
                    count.className = 'tool-status-token-count';
                    count.textContent = ` (${entry.tokenCount.toLocaleString()} ${entry.tokenCount === 1 ? 'token' : 'tokens'})`;
                    entry.line.appendChild(count);
                }
                entry.container.title = 'Open tool details';
            }

            function normalizeToolTokenCount(value) {
                if (value === null || value === undefined || value === '') return null;
                const count = Number(value);
                return Number.isInteger(count) && count >= 0 ? count : null;
            }

            function setToolEntryTokenCount(entry, value) {
                if (!entry) return;
                entry.tokenCount = normalizeToolTokenCount(value);
                updateToolDetail(entry);
            }

            function openToolCallDetails(entry) {
                if (!entry) return;
                closeToolCallDetails();
                activeToolDetailEntry = entry;
                entry.modalAbortController = new AbortController();

                const overlay = document.createElement('div');
                overlay.id = 'chat-tool-call-modal';
                overlay.className = 'app-modal-overlay fixed inset-0 z-50 flex bg-black/40';
                overlay.innerHTML = `
                    <div class="absolute inset-0" data-tool-call-close="true"></div>
                    <section class="app-modal-panel relative overflow-y-auto" role="dialog" aria-modal="true" aria-labelledby="chat-tool-call-modal-title">
                        <div class="app-modal-header sticky top-0">
                            <div class="app-modal-title-block">
                                <h2 id="chat-tool-call-modal-title" class="text-lg font-semibold text-txt-primary">${utils.escapeHtml(entry.toolName || 'Tool call')}</h2>
                                <p class="mt-1 text-xs text-txt-secondary cell-mono">${utils.escapeHtml(entry.toolId || '')}</p>
                            </div>
                            <div class="app-modal-actions">
                                <button type="button" class="ui-icon-button is-compact" data-tool-call-close="true" aria-label="Close" title="Close">
                                    ${icons.X_ICON_SVG}
                                </button>
                            </div>
                        </div>
                        <div class="p-4" data-tool-call-modal-body></div>
                    </section>
                `;
                overlay.addEventListener('click', (event) => {
                    const target = event.target;
                    if (!(target instanceof Element)) return;
                    if (target.closest('[data-tool-call-close="true"]')) {
                        closeToolCallDetails();
                    }
                });
                document.addEventListener('keydown', handleToolCallModalKeydown);
                document.body.appendChild(overlay);
                refreshToolCallDetails(entry);
                if (entry.persisted) {
                    void loadToolCallDetail(entry);
                }
            }

            async function loadToolCallDetail(entry, options = {}) {
                const vault = elements.vaultSelector?.value || '';
                const sessionId = state.sessionId || '';
                if (
                    !entry
                    || !entry.persisted
                    || entry.detailUnavailable
                    || !vault
                    || !sessionId
                    || (entry.detailLoaded && !options.force)
                ) return;

                entry.detailAbortController?.abort();
                const abortController = new AbortController();
                const requestId = entry.detailRequestId + 1;
                entry.detailRequestId = requestId;
                entry.detailAbortController = abortController;
                entry.detailLoading = true;
                entry.detailError = '';
                refreshToolCallDetails(entry);
                try {
                    const response = await fetch(
                        `api/chat/sessions/${encodeURIComponent(sessionId)}/tools/${encodeURIComponent(entry.toolId)}?vault_name=${encodeURIComponent(vault)}`,
                        { cache: 'no-store', signal: abortController.signal }
                    );
                    if (!response.ok) {
                        if (response.status === 404 && entry.state === 'running') return;
                        throw new Error(`Full tool detail is unavailable (HTTP ${response.status}).`);
                    }
                    const payload = await response.json();
                    if (entry.detailRequestId !== requestId) return;
                    entry.detailArgs = payload.args ?? null;
                    if (payload.result_text !== undefined && payload.result_text !== null) {
                        entry.detailResult = {
                            text: payload.result_text,
                            ...(payload.result_metadata && Object.keys(payload.result_metadata).length > 0
                                ? { metadata: payload.result_metadata }
                                : {}),
                            ...(payload.artifact_ref ? { artifact_ref: payload.artifact_ref } : {})
                        };
                    } else {
                        entry.detailResult = null;
                    }
                    entry.detailMetadata = payload.result_metadata || {};
                    entry.detailArtifactRef = payload.artifact_ref || '';
                    entry.detailVault = vault;
                    entry.detailSessionId = sessionId;
                    entry.detailEvents = Array.isArray(payload.events) ? payload.events : [];
                    entry.detailLoaded = true;
                } catch (error) {
                    if (error?.name !== 'AbortError' && entry.detailRequestId === requestId) {
                        entry.detailError = error.message || 'Full tool detail is unavailable.';
                    }
                } finally {
                    if (entry.detailRequestId === requestId) {
                        entry.detailAbortController = null;
                        entry.detailLoading = false;
                        refreshToolCallDetails(entry);
                    }
                }
            }

            function refreshToolCallDetails(entry) {
                if (!entry || activeToolDetailEntry !== entry) return;
                const body = document.querySelector('#chat-tool-call-modal [data-tool-call-modal-body]');
                if (!body) return;

                const sections = [
                    { label: 'Tool', value: entry.toolName || 'Tool call' },
                    { label: 'Tool call ID', value: entry.toolId || '' },
                    { label: 'Status', value: callbacks.toolStateLabel(entry) },
                    { label: 'Elapsed', value: callbacks.formatToolElapsed(entry), elapsed: true },
                    { label: 'Context', value: 'Retained in active chat context.' }
                ];
                if (!isEmptyToolValue(entry.detailArgs)) {
                    sections.push({ label: 'Args', value: entry.detailArgs, kind: 'args' });
                }
                if (!isEmptyToolValue(entry.detailResult)) {
                    sections.push({ label: 'Result', value: entry.detailResult, kind: 'result' });
                }
                if (entry.state === 'failed' && !isEmptyToolValue(entry.detailMetadata)) {
                    sections.push({ label: 'Failure', value: entry.detailMetadata });
                }
                if (entry.detailLoading) {
                    sections.push({ label: 'Full detail', value: 'Loading…' });
                } else if (entry.detailError) {
                    sections.push({ label: 'Full detail', value: entry.detailError });
                } else if (entry.detailUnavailable) {
                    sections.push({ label: 'Full detail', value: 'No execution detail is available for this tool call.' });
                } else if (!entry.persisted) {
                    sections.push({ label: 'Full detail', value: 'Available when this response finishes.' });
                }
                if (entry.detailEvents.length > 0) {
                    sections.push({ label: 'Events', value: entry.detailEvents });
                }

                body.replaceChildren();
                sections.forEach(({ label, value, kind, elapsed }) => {
                    const section = createToolDetailSection(label, value, { kind });
                    if (elapsed) section.dataset.toolCallElapsed = 'true';
                    body.appendChild(section);
                });
                if (
                    entry.detailLoaded
                    && entry.toolName === 'propose_file_edits'
                    && entry.detailArtifactRef
                    && callbacks.renderEditProposalArtifact
                ) {
                    const artifactContainer = document.createElement('div');
                    callbacks.renderEditProposalArtifact(
                        artifactContainer,
                        entry.detailArtifactRef,
                        {
                            signal: entry.modalAbortController?.signal,
                            vaultName: entry.detailVault,
                            sessionId: entry.detailSessionId
                        }
                    );
                    body.appendChild(artifactContainer);
                }
            }

            function closeToolCallDetails() {
                const entry = activeToolDetailEntry;
                activeToolDetailEntry = null;
                const modal = document.getElementById('chat-tool-call-modal');
                if (modal) {
                    modal.remove();
                }
                clearToolCallDetail(entry);
                document.removeEventListener('keydown', handleToolCallModalKeydown);
            }

            function getActiveToolDetailId() {
                return activeToolDetailEntry?.toolId || '';
            }

            function clearToolCallDetail(entry) {
                if (!entry) return;
                entry.detailAbortController?.abort();
                entry.detailAbortController = null;
                entry.modalAbortController?.abort();
                entry.modalAbortController = null;
                entry.detailRequestId += 1;
                entry.detailArgs = null;
                entry.detailResult = null;
                entry.detailMetadata = {};
                entry.detailArtifactRef = '';
                entry.detailVault = '';
                entry.detailSessionId = '';
                entry.detailEvents = [];
                entry.detailLoaded = false;
                entry.detailLoading = false;
                entry.detailError = '';
            }

            function handleToolCallModalKeydown(event) {
                if (event.key === 'Escape') {
                    closeToolCallDetails();
                }
            }

            function createToolDetailSection(label, value, options = {}) {
                const section = document.createElement('div');
                section.className = 'tool-status-section';

                const heading = document.createElement('div');
                heading.className = 'tool-status-label';
                heading.textContent = label;
                section.appendChild(heading);

                if (options.kind === 'args') {
                    renderToolArgsValue(section, value);
                } else if (options.kind === 'result') {
                    renderToolResultValue(section, value);
                } else {
                    section.appendChild(createToolDetailBlock(formatToolDetail(value)));
                }

                return section;
            }

            function renderToolArgsValue(section, value) {
                const normalized = normalizeToolDisplayValue(value);
                if (!normalized || typeof normalized !== 'object' || Array.isArray(normalized)) {
                    section.appendChild(createToolDetailBlock(formatToolDetail(normalized)));
                    return;
                }

                const handled = new Set();
                if (typeof normalized.code === 'string' && normalized.code.trim()) {
                    section.appendChild(createToolDetailSubsection('code', normalized.code, { kind: 'code' }));
                    handled.add('code');
                }

                const remaining = Object.fromEntries(
                    Object.entries(normalized).filter(([key, item]) => !handled.has(key) && !isEmptyToolValue(item))
                );
                if (Object.keys(remaining).length > 0) {
                    section.appendChild(createToolDetailSubsection('other args', remaining, { kind: 'json' }));
                }
            }

            function renderToolResultValue(section, value) {
                const normalized = normalizeToolDisplayValue(value);
                if (!normalized || typeof normalized !== 'object' || Array.isArray(normalized)) {
                    section.appendChild(createToolDetailBlock(formatToolDetail(normalized)));
                    return;
                }

                const handled = new Set();
                ['text', 'return_value', 'content', 'message'].forEach((key) => {
                    if (!isEmptyToolValue(normalized[key])) {
                        section.appendChild(createToolDetailSubsection(key, normalized[key]));
                        handled.add(key);
                    }
                });
                ['metadata', 'items', 'artifact_ref'].forEach((key) => {
                    if (!isEmptyToolValue(normalized[key])) {
                        section.appendChild(createToolDetailSubsection(key, normalized[key]));
                        handled.add(key);
                    }
                });

                const remaining = Object.fromEntries(
                    Object.entries(normalized).filter(([key, item]) => !handled.has(key) && !isEmptyToolValue(item))
                );
                if (Object.keys(remaining).length > 0) {
                    section.appendChild(createToolDetailSubsection('other', remaining));
                }
            }

            function createToolDetailSubsection(label, value, options = {}) {
                const wrapper = document.createElement('div');
                wrapper.className = 'tool-status-subsection';

                const subheading = document.createElement('div');
                subheading.className = 'tool-status-sublabel';
                subheading.textContent = label;

                wrapper.appendChild(subheading);
                const formattedValue = options.kind === 'code' && typeof value === 'string'
                    ? value
                    : formatToolDetail(value);
                wrapper.appendChild(createToolDetailBlock(formattedValue, options));
                return wrapper;
            }

            function createToolDetailBlock(value, options = {}) {
                const block = document.createElement('pre');
                block.className = options.kind === 'code'
                    ? 'tool-status-block tool-status-block-code'
                    : 'tool-status-block';
                const fullText = String(value ?? '');
                const displayLimit = 4000;
                let expanded = fullText.length <= displayLimit;

                const renderBlock = (focusToggle = false) => {
                    block.textContent = expanded
                        ? fullText
                        : `${fullText.slice(0, displayLimit).trimEnd()}\n… [display collapsed]`;
                    if (fullText.length > displayLimit) {
                        const toggle = document.createElement('button');
                        toggle.type = 'button';
                        toggle.className = 'copy-button tool-detail-toggle';
                        toggle.textContent = expanded ? 'Show less' : 'Show all';
                        toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
                        toggle.addEventListener('click', (event) => {
                            event.stopPropagation();
                            expanded = !expanded;
                            renderBlock(true);
                        });
                        block.appendChild(toggle);
                        if (focusToggle) toggle.focus();
                    }
                    const copyButton = callbacks.createCopyButton(() => fullText, 'code-copy-button');
                    block.appendChild(copyButton);
                };
                renderBlock();
                return block;
            }

        return Object.freeze({
            close: closeToolCallDetails,
            getActiveEntry: () => activeToolDetailEntry,
            getActiveId: getActiveToolDetailId,
            load: loadToolCallDetail,
            normalizeTokenCount: normalizeToolTokenCount,
            open: openToolCallDetails,
            refresh: refreshToolCallDetails,
            setEntryTokenCount: setToolEntryTokenCount,
            updateEntry: updateToolDetail,
        });
    }

    window.ChatToolDetails = Object.freeze({
        create: createChatToolDetailsController,
    });
})(window, document);
