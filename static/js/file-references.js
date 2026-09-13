(function fileReferencesModule(window, document) {
    function createFileReferencesController({ state, elements, icons, utils, callbacks }) {
        const { escapeHtml, formatShortDate } = utils;
        let pickerOpen = false;
        let activeFileCanClose = null;
        const resolutionCache = new Map();

        function selectedVault() {
            return elements.vaultSelector?.value || '';
        }

        function workspacePath() {
            return (elements.workspacePathInput?.value || '').trim();
        }

        function interactionLocked() {
            return Boolean(callbacks.isInteractionLocked?.());
        }

        function insertReference(path) {
            const input = elements.chatInput;
            if (interactionLocked() || !(input instanceof HTMLTextAreaElement) || !path) return;

            const token = `@${path}`;
            const start = input.selectionStart ?? input.value.length;
            const end = input.selectionEnd ?? start;
            const before = input.value.slice(0, start);
            const after = input.value.slice(end);
            const prefix = before && !/\s$/.test(before) ? ' ' : '';
            const suffix = after && !/^\s/.test(after) ? ' ' : '';
            input.value = `${before}${prefix}${token}${suffix}${after}`;
            const cursor = before.length + prefix.length + token.length + suffix.length;
            input.focus();
            input.setSelectionRange(cursor, cursor);
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }

        function openExplorer({
            revealPath = '',
            vaultName = '',
            workspaceSelection = false,
        } = {}) {
            const vault = vaultName || selectedVault();
            const savedWorkspace = workspacePath();
            const workspaceRecovery = Boolean(savedWorkspace)
                && state.workspaceExists === false;
            const workspaceSelectionMode = workspaceRecovery || workspaceSelection;
            pickerOpen = true;
            callbacks.openPathPicker?.({
                id: 'vault-explorer-modal',
                title: 'Vault Explorer',
                mode: 'files',
                vaultName: vault,
                subtitle: vault,
                missingVaultMessage: 'Select a vault before opening the explorer.',
                initialScope: workspaceSelectionMode || revealPath || vaultName
                    ? 'vault'
                    : (savedWorkspace ? 'workspace' : 'vault'),
                revealInitialPath: workspaceRecovery ? '' : revealPath,
                selectedLabel: workspaceRecovery
                    ? 'Workspace folder not found'
                    : (workspaceSelectionMode && savedWorkspace ? 'Current workspace' : ''),
                selectedPath: workspaceSelectionMode ? savedWorkspace : '',
                workspaceRecovery,
                workspaceSelectionMode,
                explorer: true,
                showPath: true,
                expandDirectoriesOnSelect: true,
                closeOnSelect: false,
                isReadOnly: interactionLocked,
                onSelect: ({ path, kind }) => {
                    if (kind === 'file') openFile(path, { vaultName: vault, onBack: () => {} });
                },
                onOpenFile: (path) => openFile(path, { vaultName: vault, onBack: () => {} }),
                onAddReference: insertReference,
                onSetWorkspace: async (path) => {
                    if (interactionLocked()) return;
                    const saved = await callbacks.setWorkspace?.(path);
                    if (workspaceSelectionMode && saved === true) closePicker();
                },
                onMutate: (payload) => mutatePath(payload, vault),
                onUpload: (file, path) => uploadFile(file, path, vault),
                onClose: () => {
                    pickerOpen = false;
                },
            });
        }

        function openPicker() {
            openExplorer();
        }

        function closePicker() {
            callbacks.closePathPicker?.();
            pickerOpen = false;
        }

        async function openFile(path, {
            allowCreate = false,
            onBack = null,
            vaultName = '',
            initialMode = '',
            initialRevisionId = '',
        } = {}) {
            const vault = vaultName || selectedVault();
            if (!vault || !path) return;
            if (!closeFileModal()) return;
            const overlay = document.createElement('div');
            overlay.id = 'vault-file-modal';
            overlay.className = 'app-modal-overlay fixed inset-0 z-50 flex bg-black/40';
            overlay.innerHTML = `
                <div class="absolute inset-0" data-vault-file-close="true"></div>
                <section class="app-modal-panel relative flex flex-col" role="dialog" aria-modal="true" aria-labelledby="vault-file-modal-title">
                    <div class="app-modal-header flex-none">
                        ${typeof onBack === 'function' ? `
                            <button type="button" class="ui-icon-button is-compact" data-vault-file-back="true" aria-label="Back to files" title="Back to files">${icons.ARROW_LEFT_ICON_SVG}</button>
                        ` : ''}
                        <div class="app-modal-title-block">
                            <h2 id="vault-file-modal-title" class="text-lg font-semibold text-txt-primary">${escapeHtml(path.split('/').pop() || path)}</h2>
                            <p id="vault-file-modal-path" class="mt-1 text-xs text-txt-secondary cell-mono">${escapeHtml(path)}</p>
                        </div>
                        <div class="app-modal-actions">
                            <div class="vault-file-mode-toggle hidden" data-vault-file-mode-toggle role="group" aria-label="File view">
                                <button type="button" data-vault-file-mode="preview" aria-label="Preview Markdown" title="Preview Markdown">${icons.EYE_ICON_SVG}</button>
                                <button type="button" data-vault-file-mode="edit" aria-label="Edit Markdown" title="Edit Markdown">${icons.EDIT_ICON_SVG}</button>
                                <button type="button" data-vault-file-mode="history" aria-label="Revision history" title="Revision history">${icons.HISTORY_ICON_SVG}</button>
                            </div>
                            <button type="button" class="ui-icon-button is-primary is-compact hidden" data-vault-file-save="true" aria-label="Save file" title="Save file" disabled>${icons.SAVE_ICON_SVG}</button>
                            <button type="button" class="ui-icon-button is-compact" data-vault-file-close="true" aria-label="Close" title="Close">${icons.X_ICON_SVG}</button>
                        </div>
                    </div>
                    <div class="vault-file-modal-content flex-1 min-h-0 flex flex-col">
                        <div id="vault-file-modal-status" class="text-sm text-txt-secondary">Loading file...</div>
                        <div id="vault-file-modal-properties" class="vault-file-properties hidden flex-none">
                            <details>
                                <summary>Properties</summary>
                                <pre data-vault-file-properties-content></pre>
                            </details>
                        </div>
                        <div id="vault-file-modal-preview" class="vault-file-preview prose prose-sm max-w-none hidden flex-1 min-h-0 overflow-y-auto"></div>
                        <div id="vault-file-modal-history" class="vault-file-history hidden flex-1 min-h-0">
                            <div class="vault-file-history-list" data-vault-file-history-list></div>
                            <div class="vault-file-history-preview" data-vault-file-history-preview>
                                <p class="text-sm text-txt-secondary">Select a revision to preview.</p>
                            </div>
                        </div>
                        <textarea
                            id="vault-file-modal-editor"
                            class="w-full flex-1 min-h-0 px-3 py-2 border border-border-secondary rounded-md bg-app-bg text-txt-primary font-mono text-sm focus:outline-none focus:ring-2 focus:ring-accent"
                            spellcheck="false"
                            disabled
                        ></textarea>
                    </div>
                </section>
            `;
            document.body.appendChild(overlay);

            const editor = overlay.querySelector('#vault-file-modal-editor');
            const statusLabel = overlay.querySelector('#vault-file-modal-status');
            const saveButton = overlay.querySelector('[data-vault-file-save]');
            const preview = overlay.querySelector('#vault-file-modal-preview');
            const history = overlay.querySelector('#vault-file-modal-history');
            const historyList = overlay.querySelector('[data-vault-file-history-list]');
            const historyPreview = overlay.querySelector('[data-vault-file-history-preview]');
            const properties = overlay.querySelector('#vault-file-modal-properties');
            const propertiesContent = overlay.querySelector('[data-vault-file-properties-content]');
            const modeToggle = overlay.querySelector('[data-vault-file-mode-toggle]');
            let sha256 = '';
            let createIfMissing = false;
            let savedContent = '';
            let mode = 'edit';
            let historyLoaded = false;
            let currentFileExists = false;
            let currentFileHashKnown = false;
            const supportsPreview = /\.(md|markdown)$/i.test(path);
            const lockMessage = 'Available when the active response finishes.';

            function isDirty() {
                return editor instanceof HTMLTextAreaElement && editor.value !== savedContent;
            }

            function confirmDiscard() {
                return !isDirty() || window.confirm('Discard unsaved changes to this file?');
            }
            activeFileCanClose = confirmDiscard;

            function renderPreview() {
                if (!(preview instanceof HTMLElement) || !(editor instanceof HTMLTextAreaElement)) return;
                const parts = splitMarkdownFrontmatter(editor.value);
                callbacks.renderMarkdownPreview?.(preview, parts.body);
                if (propertiesContent instanceof HTMLElement) {
                    propertiesContent.textContent = parts.frontmatter;
                }
                properties?.classList.toggle('hidden', !parts.frontmatter);
            }

            function showHistoryOnly(message) {
                if (statusLabel) statusLabel.textContent = message;
                modeToggle?.classList.remove('hidden');
                modeToggle?.querySelector('[data-vault-file-mode="preview"]')?.classList.add('hidden');
                modeToggle?.querySelector('[data-vault-file-mode="edit"]')?.classList.add('hidden');
                setMode('history');
            }

            function setMode(nextMode) {
                if (supportsPreview && nextMode === 'edit' && interactionLocked()) {
                    nextMode = 'preview';
                }
                mode = nextMode === 'history'
                    ? 'history'
                    : (supportsPreview && nextMode === 'preview' ? 'preview' : 'edit');
                if (mode === 'preview') renderPreview();
                if (mode === 'history' && !historyLoaded) loadRevisionHistory();
                preview?.classList.toggle('hidden', mode !== 'preview');
                if (mode !== 'preview') properties?.classList.add('hidden');
                editor?.classList.toggle('hidden', mode !== 'edit');
                history?.classList.toggle('hidden', mode !== 'history');
                saveButton?.classList.toggle('hidden', mode !== 'edit' || interactionLocked());
                modeToggle?.querySelectorAll('[data-vault-file-mode]').forEach((button) => {
                    button.classList.toggle('is-active', button.getAttribute('data-vault-file-mode') === mode);
                    button.setAttribute('aria-pressed', button.getAttribute('data-vault-file-mode') === mode ? 'true' : 'false');
                });
            }

            function applyInteractionLock() {
                const locked = interactionLocked();
                if (editor instanceof HTMLTextAreaElement) {
                    editor.readOnly = locked;
                    editor.title = locked ? lockMessage : '';
                }
                const editButton = modeToggle?.querySelector('[data-vault-file-mode="edit"]');
                if (editButton instanceof HTMLButtonElement) {
                    editButton.disabled = locked;
                    editButton.title = locked ? lockMessage : 'Edit Markdown';
                }
                if (saveButton instanceof HTMLButtonElement) {
                    saveButton.classList.toggle('hidden', locked || mode !== 'edit');
                    saveButton.disabled = locked || (!isDirty() && !createIfMissing);
                    saveButton.title = locked ? lockMessage : 'Save file';
                }
                overlay.querySelectorAll('[data-vault-file-restore]').forEach((button) => {
                    if (!(button instanceof HTMLButtonElement)) return;
                    button.disabled = locked;
                    button.title = locked ? lockMessage : 'Restore this revision';
                });
                if (locked && supportsPreview && mode === 'edit') setMode('preview');
            }

            overlay.addEventListener('vault-explorer-lock-change', applyInteractionLock);

            overlay.addEventListener('click', async (event) => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                if (target.closest('[data-vault-file-back="true"]')) {
                    if (!closeFileModal()) return;
                    onBack?.();
                    return;
                }
                if (target.closest('[data-vault-file-close="true"]')) {
                    closeFileModal();
                    return;
                }
                const modeButton = target.closest('[data-vault-file-mode]');
                if (modeButton instanceof HTMLButtonElement) {
                    if (modeButton.dataset.vaultFileMode === 'edit' && interactionLocked()) return;
                    setMode(modeButton.dataset.vaultFileMode || 'edit');
                    return;
                }
                const revisionButton = target.closest('[data-vault-file-revision]');
                if (revisionButton instanceof HTMLButtonElement) {
                    await previewRevision(revisionButton);
                    return;
                }
                const restoreButton = target.closest('[data-vault-file-restore]');
                if (restoreButton instanceof HTMLButtonElement) {
                    await restoreRevision(restoreButton);
                    return;
                }
                if (target.closest('[data-vault-file-save="true"]') && editor instanceof HTMLTextAreaElement) {
                    if (interactionLocked()) return;
                    const saved = await saveFile(path, vault, editor, statusLabel, saveButton, () => createIfMissing, (nextHash) => {
                        sha256 = nextHash;
                        createIfMissing = false;
                        currentFileExists = true;
                        currentFileHashKnown = true;
                    }, () => sha256);
                    if (saved) {
                        savedContent = editor.value;
                        historyLoaded = false;
                        if (historyList instanceof HTMLElement) historyList.innerHTML = '';
                        if (saveButton instanceof HTMLButtonElement) saveButton.disabled = true;
                        if (supportsPreview) setMode('preview');
                    }
                }
            });
            editor?.addEventListener('input', () => {
                if (saveButton instanceof HTMLButtonElement) {
                    saveButton.disabled = interactionLocked() || (!isDirty() && !createIfMissing);
                }
            });

            try {
                const data = await fetchVaultFile(path, vault);
                sha256 = data.sha256 || '';
                currentFileExists = true;
                currentFileHashKnown = true;
                if (editor instanceof HTMLTextAreaElement) {
                    editor.value = data.content || '';
                    savedContent = editor.value;
                    editor.disabled = false;
                }
                statusLabel?.classList.add('hidden');
                if (saveButton instanceof HTMLButtonElement) {
                    saveButton.disabled = true;
                }
                modeToggle?.classList.remove('hidden');
                modeToggle?.querySelector('[data-vault-file-mode="preview"]')?.classList.toggle('hidden', !supportsPreview);
                setMode(initialMode === 'history' ? 'history' : (supportsPreview ? 'preview' : 'edit'));
                applyInteractionLock();
            } catch (error) {
                if (error.errorType === 'VaultFileNotFound') {
                    currentFileExists = false;
                    currentFileHashKnown = true;
                    if (initialMode === 'history') {
                        showHistoryOnly(`${path} no longer exists.`);
                        return;
                    }
                    if (!allowCreate) {
                        if (statusLabel) {
                            statusLabel.textContent = `${path} no longer exists.`;
                        }
                        return;
                    }
                    createIfMissing = true;
                    if (editor instanceof HTMLTextAreaElement) {
                        editor.value = '';
                        savedContent = '';
                        editor.disabled = false;
                    }
                    if (statusLabel) {
                        statusLabel.textContent = `${path} does not exist yet. Add content and save to create it.`;
                    }
                    if (saveButton instanceof HTMLButtonElement) {
                        saveButton.disabled = false;
                    }
                    modeToggle?.classList.remove('hidden');
                    modeToggle?.querySelector('[data-vault-file-mode="preview"]')?.classList.toggle('hidden', !supportsPreview);
                    setMode('edit');
                    applyInteractionLock();
                    return;
                }
                if (error.errorType === 'VaultFileNotText') {
                    currentFileExists = true;
                    currentFileHashKnown = false;
                    if (initialMode === 'history') {
                        showHistoryOnly('This file is not editable as plain text.');
                        return;
                    }
                    if (statusLabel) statusLabel.textContent = 'This file is not editable as plain text.';
                    return;
                }
                if (statusLabel) {
                    statusLabel.innerHTML = `<span class="state-error">Error: ${escapeHtml(error.message)}</span>`;
                }
            }

            async function loadRevisionHistory() {
                if (!(historyList instanceof HTMLElement)) return;
                historyList.innerHTML = '<p class="text-sm text-txt-secondary">Loading revisions...</p>';
                try {
                    const response = await fetch(`api/vaults/${encodeURIComponent(vault)}/files/revisions?path=${encodeURIComponent(path)}&limit=100`);
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    const data = await response.json();
                    const revisions = data.revisions || [];
                    historyLoaded = true;
                    if (!revisions.length) {
                        historyList.innerHTML = '<p class="text-sm text-txt-secondary">No retained Assistant.md revisions. External edits are not snapshotted.</p>';
                        return;
                    }
                    historyList.innerHTML = revisions.map((revision) => `
                        <button
                            type="button"
                            class="vault-file-revision-row"
                            data-vault-file-revision="${escapeHtml(String(revision.snapshot_id))}"
                            data-vault-file-revision-available="${revision.snapshot_available ? 'true' : 'false'}"
                            data-vault-file-revision-exists="${revision.exists ? 'true' : 'false'}"
                        >
                            <span class="vault-file-revision-title">${escapeHtml(revision.activity_label || revision.operation)}</span>
                            <span class="vault-file-revision-meta">${escapeHtml(revisionKindLabel(revision.activity_kind))} · ${escapeHtml(formatShortDate(revision.created_at))}</span>
                        </button>
                    `).join('');
                    const requestedRevision = initialRevisionId
                        ? historyList.querySelector(`[data-vault-file-revision="${CSS.escape(String(initialRevisionId))}"]`)
                        : null;
                    if (requestedRevision instanceof HTMLButtonElement) {
                        await previewRevision(requestedRevision);
                    }
                } catch (error) {
                    historyList.innerHTML = `<p class="state-error text-sm">Unable to load revisions: ${escapeHtml(error.message)}</p>`;
                }
            }

            async function previewRevision(button) {
                if (!(historyPreview instanceof HTMLElement)) return;
                historyList?.querySelectorAll('[data-vault-file-revision]').forEach((row) => {
                    row.classList.toggle('is-active', row === button);
                });
                if (button.dataset.vaultFileRevisionExists !== 'true') {
                    historyPreview.innerHTML = `
                        ${renderRestoreAction(button)}
                        <p class="text-sm text-txt-secondary">The file did not exist before this operation.</p>
                    `;
                    return;
                }
                if (button.dataset.vaultFileRevisionAvailable !== 'true') {
                    historyPreview.innerHTML = '<p class="text-sm text-txt-secondary">This revision is no longer available.</p>';
                    return;
                }
                historyPreview.innerHTML = '<p class="text-sm text-txt-secondary">Loading revision...</p>';
                try {
                    const snapshotId = button.dataset.vaultFileRevision || '';
                    const response = await fetch(`api/vault-state/snapshots/${encodeURIComponent(snapshotId)}/content`);
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    const mediaType = response.headers.get('content-type') || '';
                    if (mediaType && !isPreviewableTextMediaType(mediaType)) {
                        historyPreview.innerHTML = '<p class="text-sm text-txt-secondary">This revision is not previewable as plain text.</p>';
                        return;
                    }
                    const content = await response.text();
                    if (supportsPreview) {
                        const parts = splitMarkdownFrontmatter(content);
                        historyPreview.innerHTML = `
                            ${renderRestoreAction(button)}
                            ${parts.frontmatter ? `<details class="vault-file-revision-properties"><summary>Properties</summary><pre>${escapeHtml(parts.frontmatter)}</pre></details>` : ''}
                            <div class="vault-file-preview prose prose-sm max-w-none" data-vault-file-revision-markdown></div>
                        `;
                        const revisionMarkdown = historyPreview.querySelector('[data-vault-file-revision-markdown]');
                        if (revisionMarkdown instanceof HTMLElement) {
                            callbacks.renderMarkdownPreview?.(revisionMarkdown, parts.body);
                        }
                    } else {
                        historyPreview.innerHTML = `${renderRestoreAction(button)}<pre>${escapeHtml(content)}</pre>`;
                    }
                } catch (error) {
                    historyPreview.innerHTML = `<p class="state-error text-sm">Unable to load revision: ${escapeHtml(error.message)}</p>`;
                }
            }

            function renderRestoreAction(button) {
                if (!currentFileHashKnown) return '';
                return `
                    <div class="vault-file-revision-actions">
                        <button
                            type="button"
                            class="ui-button-secondary vault-file-revision-action-button"
                            data-vault-file-restore="${escapeHtml(button.dataset.vaultFileRevision || '')}"
                            data-vault-file-restore-exists="${escapeHtml(button.dataset.vaultFileRevisionExists || 'false')}"
                            title="${interactionLocked() ? 'Available when the active response finishes.' : 'Restore this revision'}"
                            ${interactionLocked() ? 'disabled' : ''}
                        >
                            ${icons.RESTORE_ICON_SVG}
                            <span>Restore this revision</span>
                        </button>
                    </div>
                `;
            }

            async function restoreRevision(button) {
                if (interactionLocked() || !currentFileHashKnown) return;
                if (!confirmDiscard()) return;
                const snapshotId = button.dataset.vaultFileRestore || '';
                const restoresFile = button.dataset.vaultFileRestoreExists === 'true';
                const prompt = restoresFile
                    ? `Restore this revision to ${path}? The current state will remain available in history.`
                    : `Restore the earlier absent state by deleting ${path}? The current file will remain available in history.`;
                if (!window.confirm(prompt)) return;
                button.disabled = true;
                try {
                    const response = await fetch(
                        `api/vaults/${encodeURIComponent(vault)}/files/revisions/${encodeURIComponent(snapshotId)}/restore`,
                        {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                expected_sha256: currentFileExists ? sha256 : null,
                            }),
                        }
                    );
                    if (!response.ok) {
                        const errorData = await response.json().catch(() => ({}));
                        throw new Error(errorData.message || `HTTP ${response.status}`);
                    }
                    const result = await response.json();
                    if (editor instanceof HTMLTextAreaElement) {
                        savedContent = editor.value;
                    }
                    await openFile(path, {
                        vaultName: vault,
                        onBack,
                        initialMode: result.exists && supportsPreview ? 'preview' : 'history',
                    });
                } catch (error) {
                    button.disabled = false;
                    historyPreview.innerHTML = `<p class="state-error text-sm">Unable to restore revision: ${escapeHtml(error.message)}</p>`;
                }
            }
        }

        function revisionKindLabel(kind) {
            const normalized = String(kind || '').trim().toLowerCase();
            if (normalized === 'explorer') return 'Vault Explorer';
            if (normalized === 'chat') return 'Chat';
            if (normalized === 'workflow') return 'Workflow';
            if (normalized === 'ingestion') return 'Ingestion';
            return 'Assistant.md';
        }

        function splitMarkdownFrontmatter(content) {
            const normalized = String(content || '').replace(/\r\n?/g, '\n');
            const lines = normalized.split('\n');
            if (lines[0]?.trim() !== '---') {
                return { frontmatter: '', body: normalized };
            }
            const closingIndex = lines.findIndex((line, index) => index > 0 && line.trim() === '---');
            if (closingIndex < 0) {
                return { frontmatter: '', body: normalized };
            }
            return {
                frontmatter: lines.slice(1, closingIndex).join('\n').trim(),
                body: lines.slice(closingIndex + 1).join('\n').replace(/^\n+/, ''),
            };
        }

        function openDirectory(path) {
            if (!path) return;
            openExplorer({ revealPath: path });
        }

        async function mutatePath(payload, vaultName = '') {
            if (interactionLocked()) {
                throw new Error('Wait for the active response to finish.');
            }
            const vault = vaultName || selectedVault();
            const response = await fetch(`api/vaults/${encodeURIComponent(vault)}/paths/mutate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || errorData.detail || `HTTP ${response.status}`);
            }
            return response.json();
        }

        async function uploadFile(file, path, vaultName = '') {
            if (interactionLocked()) {
                throw new Error('Wait for the active response to finish.');
            }
            const vault = vaultName || selectedVault();
            const body = new FormData();
            body.append('file', file, file.name);
            const response = await fetch(
                `api/vaults/${encodeURIComponent(vault)}/files/upload?path=${encodeURIComponent(path)}`,
                { method: 'POST', body }
            );
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || errorData.detail || `HTTP ${response.status}`);
            }
            return response.json();
        }

        async function fetchVaultFile(path, vaultName = '') {
            const vault = vaultName || selectedVault();
            const response = await fetch(`api/vaults/${encodeURIComponent(vault)}/files?path=${encodeURIComponent(path)}`);
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                const error = new Error(errorData.message || `HTTP ${response.status}`);
                error.errorType = errorData.error || errorData.details?.error_type || '';
                throw error;
            }
            return response.json();
        }

        async function saveFile(path, vault, editor, statusLabel, saveButton, shouldCreate, setHash, getHash) {
            if (!(editor instanceof HTMLTextAreaElement)) return;
            if (saveButton instanceof HTMLButtonElement) saveButton.disabled = true;
            const createIfMissing = shouldCreate();
            if (statusLabel) {
                statusLabel.classList.remove('hidden');
                statusLabel.textContent = createIfMissing ? 'Creating...' : 'Saving...';
            }
            try {
                const response = await fetch(`api/vaults/${encodeURIComponent(vault)}/files?path=${encodeURIComponent(path)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        content: editor.value,
                        expected_sha256: getHash(),
                        create_if_missing: createIfMissing,
                    }),
                });
                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.message || `HTTP ${response.status}`);
                }
                const data = await response.json();
                setHash(data.sha256 || '');
                if (statusLabel) statusLabel.textContent = data.message || 'Saved.';
                return data;
            } catch (error) {
                if (statusLabel) {
                    statusLabel.innerHTML = `<span class="state-error">Error: ${escapeHtml(error.message)}</span>`;
                }
                return null;
            } finally {
                if (saveButton instanceof HTMLButtonElement) saveButton.disabled = false;
            }
        }

        function closeFileModal({ force = false } = {}) {
            if (!force && activeFileCanClose && !activeFileCanClose()) return false;
            document.getElementById('vault-file-modal')?.remove();
            activeFileCanClose = null;
            return true;
        }

        function syncInteractionLocks() {
            callbacks.syncPathPickerLocks?.();
            document.getElementById('vault-file-modal')?.dispatchEvent(
                new CustomEvent('vault-explorer-lock-change')
            );
        }

        function enhanceFileLinks(container) {
            if (!container) return;
            markStandaloneCandidates(container);
            const textNodes = [];
            const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
                acceptNode(node) {
                    const parent = node.parentElement;
                    if (!parent || parent.closest('a, button, code, pre, textarea, [data-vault-reference-candidate]')) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return candidateMatches(node.textContent || '').length
                        ? NodeFilter.FILTER_ACCEPT
                        : NodeFilter.FILTER_REJECT;
                },
            });
            while (walker.nextNode()) {
                textNodes.push(walker.currentNode);
            }
            textNodes.forEach(markTextNodeCandidates);
            container.querySelectorAll('a[href]').forEach((link) => {
                if (!(link instanceof HTMLAnchorElement)) return;
                if (link.dataset.vaultFileEnhanced === 'true') return;
                const candidate = standaloneCandidate(
                    link.getAttribute('href') || link.textContent || ''
                );
                if (candidate) link.dataset.vaultReferenceCandidate = candidate;
            });
            resolveMarkedCandidates(container).catch((error) => {
                console.error('Unable to resolve vault references:', error);
            });
        }

        function markStandaloneCandidates(container) {
            container.querySelectorAll('code').forEach((code) => {
                if (!(code instanceof HTMLElement) || code.closest('pre')) return;
                if (code.dataset.vaultFileEnhanced === 'true') return;
                const candidate = standaloneCandidate(code.textContent || '');
                if (candidate) code.dataset.vaultReferenceCandidate = candidate;
            });
        }

        function markTextNodeCandidates(node) {
            const text = node.textContent || '';
            const matches = candidateMatches(text);
            if (!matches.length) return;
            let cursor = 0;
            const fragment = document.createDocumentFragment();
            matches.forEach(({ start, end, raw, candidate }) => {
                if (start > cursor) {
                    fragment.appendChild(document.createTextNode(text.slice(cursor, start)));
                }
                const marker = document.createElement('span');
                marker.textContent = raw;
                marker.dataset.vaultReferenceCandidate = candidate;
                fragment.appendChild(marker);
                cursor = end;
            });
            if (cursor < text.length) {
                fragment.appendChild(document.createTextNode(text.slice(cursor)));
            }
            node.parentNode?.replaceChild(fragment, node);
        }

        async function resolveMarkedCandidates(container) {
            const marked = Array.from(
                container.querySelectorAll('[data-vault-reference-candidate]')
            ).filter((element) => element instanceof HTMLElement);
            if (!marked.length) return;
            const paths = marked.map((element) => element.dataset.vaultReferenceCandidate || '');
            const resolutions = await resolveCandidates(paths);
            marked.forEach((element) => {
                const candidate = element.dataset.vaultReferenceCandidate || '';
                const resolution = resolutions.get(candidate);
                delete element.dataset.vaultReferenceCandidate;
                if (!resolution || resolution.kind === 'missing') {
                    if (element instanceof HTMLAnchorElement) {
                        element.replaceWith(document.createTextNode(element.textContent || candidate));
                    }
                    return;
                }
                const link = document.createElement('a');
                link.href = '#';
                link.className = element instanceof HTMLElement && element.tagName === 'CODE'
                    ? 'vault-file-link vault-file-link-code'
                    : 'vault-file-link';
                link.textContent = `@${resolution.path}`;
                link.dataset.vaultFileEnhanced = 'true';
                link.dataset.vaultFilePath = resolution.path;
                link.dataset.vaultFileKind = resolution.kind;
                link.title = resolution.kind === 'directory'
                    ? `Browse ${resolution.path}`
                    : `Open ${resolution.path}`;
                link.addEventListener('click', (event) => {
                    event.preventDefault();
                    if (resolution.kind === 'directory') {
                        openDirectory(resolution.path);
                    } else {
                        openFile(resolution.path, {
                            onBack: () => openExplorer({ revealPath: resolution.path }),
                        });
                    }
                });
                element.replaceWith(link);
            });
        }

        async function resolveCandidates(paths) {
            const vault = selectedVault();
            const workspace = workspacePath();
            const normalized = [...new Set(paths.map(normalizeDisplayPath).filter(Boolean))];
            const resolved = new Map();
            const unresolved = [];
            normalized.forEach((path) => {
                const cached = resolutionCache.get(resolutionCacheKey(vault, workspace, path));
                if (cached) {
                    resolved.set(path, cached);
                } else {
                    unresolved.push(path);
                }
            });
            if (!vault || !unresolved.length) return resolved;
            const response = await fetch(
                `api/vaults/${encodeURIComponent(vault)}/file-refs/resolve`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ paths: unresolved, workspace_path: workspace }),
                }
            );
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `HTTP ${response.status}`);
            }
            const payload = await response.json();
            const items = Array.isArray(payload.items) ? payload.items : [];
            items.forEach((item) => {
                const requestedPath = normalizeDisplayPath(item.requested_path || '');
                if (!requestedPath) return;
                resolved.set(requestedPath, item);
                if (item.kind !== 'missing') {
                    resolutionCache.set(
                        resolutionCacheKey(vault, workspace, requestedPath),
                        item
                    );
                }
            });
            return resolved;
        }

        function resolutionCacheKey(vault, workspace, path) {
            return `${vault}\u0000${workspace}\u0000${path}`;
        }

        function candidateMatches(text) {
            const patterns = [
                {
                    regex: /@([^@\n<>()\[\]{};:!?]*?\.(?:md|markdown|txt))/gi,
                    group: 0,
                    priority: 0,
                },
                {
                    regex: /@((?:[\w .,@-]+\/)+[\w.@-]+\/?)/gi,
                    group: 0,
                    priority: 0,
                },
                {
                    regex: /(^|[\s([`])([\w.@-]+\/[\w .@/-]+\.(?:md|markdown|txt))(?=$|[\s).,;:`\]])/gi,
                    group: 2,
                    priority: 1,
                },
                {
                    regex: /(^|[\s([`])([\w.@-]+(?:\/[\w.@-]+(?: [\w.@-]+)*)+\/?)(?=$|[\s).,;:`\]])/g,
                    group: 2,
                    priority: 2,
                },
                {
                    regex: /(^|[\s([`])([\w.@-]+(?: [\w.@-]+)*\/)(?=$|[\s).,;:`\]])/g,
                    group: 2,
                    priority: 3,
                },
            ];
            const found = [];
            patterns.forEach(({ regex, group, priority }) => {
                let match;
                while ((match = regex.exec(text)) !== null) {
                    const raw = match[group] || '';
                    const start = match.index + (group === 2 ? (match[1] || '').length : 0);
                    const candidate = normalizeDisplayPath(raw);
                    if (candidate) {
                        found.push({ start, end: start + raw.length, raw, candidate, priority });
                    }
                }
            });
            found.sort((left, right) => left.start - right.start || left.priority - right.priority);
            const selected = [];
            let cursor = -1;
            found.forEach((match) => {
                if (match.start < cursor) return;
                selected.push(match);
                cursor = match.end;
            });
            return selected;
        }

        function standaloneCandidate(value) {
            const original = String(value || '').trim();
            const raw = normalizeDisplayPath(original);
            if (!raw || /^https?:\/\//i.test(raw) || raw.startsWith('#')) {
                return '';
            }
            if (
                !original.startsWith('@')
                && !/\.(md|markdown|txt)$/i.test(raw)
                && !raw.includes('/')
            ) {
                return '';
            }
            return raw;
        }

        function normalizeDisplayPath(value) {
            return String(value || '')
                .trim()
                .replace(/^@/, '')
                .replace(/^\.?\//, '')
                .replace(/[),.;:]+$/, '')
                .replace(/\/+$/, '');
        }

        function isPreviewableTextMediaType(value) {
            const mediaType = String(value || '').split(';', 1)[0].trim().toLowerCase();
            return mediaType.startsWith('text/')
                || mediaType === 'application/json'
                || mediaType === 'application/javascript'
                || mediaType === 'application/xml'
                || mediaType.endsWith('+json')
                || mediaType.endsWith('+xml');
        }

        function debounce(fn, delayMs) {
            let timer = null;
            return (...args) => {
                if (timer) window.clearTimeout(timer);
                timer = window.setTimeout(() => fn(...args), delayMs);
            };
        }

        return Object.freeze({
            openPicker,
            openExplorer,
            closePicker,
            insertReference,
            openFile,
            closeFileModal,
            syncInteractionLocks,
            enhanceFileLinks,
            isPickerOpen: () => pickerOpen,
        });
    }

    window.FileReferences = Object.freeze({
        create: createFileReferencesController,
    });
})(window, document);
