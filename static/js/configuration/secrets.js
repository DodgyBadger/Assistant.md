(function configurationFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, constants, elements, helpers, resources, state, timers } = runtime;
    const { ACTIVITY_LOG_LEVELS, BUILT_IN_PROVIDER_NAMES, DEFAULT_IMPORT_JOB_STATUSES, SECRET_METADATA } = constants;
    const { escapeHtml, formatDateTime, iconButton, iconSvg, notifyConfigChanged, safeJson, setIconButtonLabel, setStatus, withRestartNotice } = helpers;

    function renderSecretsTable() {
        if (!elements.secretsList) return;

        const cards = [];
        const editing = state.secretEdit;
        const draft = state.secretDraft || {};

        if (editing && editing.mode === 'new') {
            cards.push(renderSecretEditCard(draft, { isNew: true }));
        }

        if (!state.secrets.length) {
            cards.push(`
                <div class="rounded-lg border border-border-primary bg-app-card px-4 py-3 text-sm text-txt-secondary text-center shadow-sm">
                    No secrets registered yet.
                </div>
            `);
            elements.secretsList.innerHTML = cards.join('');
            focusSecretInput();
            return;
        }

        state.secrets.forEach((entry) => {
            if (editing && editing.mode === 'existing' && editing.key === entry.name) {
                cards.push(renderSecretEditCard(draft, { isNew: false }));
                return;
            }
            const metadata = SECRET_METADATA[entry.name] || null;
            const label = metadata?.label || entry.name;
            const hasValue = Boolean(entry.has_value);
            const stored = Boolean(entry.stored);
            const statusBadge = hasValue
                ? '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-success border">Set</span>'
                : '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-error border">Not set</span>';

            const description = metadata?.description
                ? `<div class="text-xs text-txt-secondary mt-1">${escapeHtml(metadata.description)}</div>`
                : '';

            const deleteButton = stored
                ? `<button data-secret-action="delete" ${iconButton('trash', 'Delete secret', 'is-danger')}>${iconSvg('trash')}</button>`
                : '';

            cards.push(`
                <div class="secret-card rounded-lg border border-border-primary bg-app-card px-5 py-4 shadow-sm hover:shadow transition-shadow" data-secret="${escapeHtml(entry.name)}" style="max-width: 1400px;">
                    <div class="space-y-4">
                        <div class="flex items-start justify-between gap-4">
                            <div class="min-w-0">
                                <div class="flex items-center gap-2 flex-wrap">
                                    <div class="font-medium text-txt-primary text-sm">${escapeHtml(label)}</div>
                                    <div class="w-fit">${statusBadge}</div>
                                </div>
                                <div class="font-mono text-xs text-txt-secondary mt-0.5 break-all">${escapeHtml(entry.name)}</div>
                                ${description}
                            </div>
                            <div class="flex items-center gap-2 justify-end shrink-0 flex-wrap">
                                <button data-secret-action="set" ${iconButton('edit', 'Update secret', 'is-primary')}>${iconSvg('edit')}</button>
                                <button data-secret-action="clear" ${iconButton('x', 'Clear secret', 'is-danger')}>${iconSvg('x')}</button>
                                ${deleteButton}
                            </div>
                        </div>
                    </div>
                </div>
            `);
        });

        elements.secretsList.innerHTML = cards.join('');
        focusSecretInput();
        actions.updateImportOcrAvailability();
    }

    function renderSecretEditCard(draft, { isNew }) {
        const rowKey = isNew ? '__new' : (state.secretEdit?.key || draft.name || '');
        const nameReadonly = isNew ? '' : 'readonly';
        const nameHelp = isNew
            ? 'Use uppercase letters, numbers, or underscores.'
            : 'Secret names are identities and cannot be renamed here.';
        return `
            <div class="secret-card rounded-lg border border-border-primary bg-app-card editing-highlight px-5 py-4 shadow-sm" data-secret="${escapeHtml(rowKey)}" data-mode="edit" style="max-width: 1400px;">
                <div class="space-y-4">
                    <div class="grid gap-4 md:grid-cols-2">
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Secret Name</label>
                            <input data-secret-field="name" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary font-mono text-sm transition-colors" placeholder="e.g. LOCAL_MODEL_TOKEN" value="${escapeHtml(draft.name || '')}" ${nameReadonly} />
                            <p class="text-xs text-txt-secondary mt-1">${nameHelp}</p>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Secret Value</label>
                            <input data-secret-field="value" type="password" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="Enter the credential" value="${escapeHtml(draft.value || '')}" />
                            <p class="text-xs text-txt-secondary mt-1">Values are encrypted at rest and owned by the current principal.</p>
                        </div>
                    </div>
                    <div class="flex justify-end gap-2">
                        <button data-secret-action="cancel-secret" ${iconButton('circleX', 'Cancel secret edit')}>${iconSvg('circleX')}</button>
                        <button data-secret-action="save-secret" ${iconButton('save', 'Save secret', 'is-primary')}>${iconSvg('save')}</button>
                    </div>
                </div>
            </div>
        `;
    }

    function focusSecretInput(field = 'name') {
        if (!state.secretEdit) return;
        requestAnimationFrame(() => {
            const editableName = state.secretEdit?.mode === 'new';
            const targetField = editableName ? field : (field === 'name' ? 'value' : field);
            const el = elements.secretsList?.querySelector(`[data-secret][data-mode="edit"] [data-secret-field="${targetField}"]`);
            if (el instanceof HTMLInputElement) {
                el.focus();
                el.select();
            }
        });
    }


    async function handleSecretsTableClick(event) {
        const actionBtn = event.target.closest('[data-secret-action]');
        if (!actionBtn) return;

        const row = actionBtn.closest('[data-secret]');
        const name = row?.dataset.secret;
        if (!name) return;

        const action = actionBtn.dataset.secretAction;

        if (action === 'set') {
            startSecretEdit(name);
        } else if (action === 'clear') {
            if (!window.confirm(`Clear the stored value for ${name}?`)) {
                return;
            }
            try {
                const result = await updateSecretValue(name, '');
                const resultMessage = withRestartNotice(`Cleared secret '${name}'.`, result);
                setStatus(elements.secretFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
            } catch (error) {
                setStatus(elements.secretFeedback, `Failed to clear secret: ${error.message}`, 'error');
            }
        } else if (action === 'delete') {
            if (!window.confirm(`Delete secret '${name}' from the system? This cannot be undone.`)) {
                return;
            }
            try {
                const result = await deleteSecret(name);
                const resultMessage = withRestartNotice(`Deleted secret '${name}'.`, result);
                setStatus(elements.secretFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
            } catch (error) {
                setStatus(elements.secretFeedback, `Failed to delete secret: ${error.message}`, 'error');
            }
        } else if (action === 'cancel-secret') {
            cancelSecretEdit();
        } else if (action === 'save-secret') {
            await saveSecretRow(actionBtn);
        }
    }

    function handleSecretInputChange(event) {
        const target = event.target;
        if (!state.secretEdit || !(target instanceof HTMLInputElement) || !target.dataset.secretField) {
            return;
        }
        if (!state.secretDraft) {
            state.secretDraft = {};
        }
        state.secretDraft[target.dataset.secretField] = target.value;
    }

    function startNewSecret() {
        if (state.secretEdit) {
            setStatus(elements.secretFeedback, 'Finish editing the current secret before adding another.', 'warning');
            return;
        }
        state.secretEdit = { mode: 'new', key: '__new' };
        state.secretDraft = {
            name: '',
            value: ''
        };
        renderSecretsTable();
        setStatus(elements.secretFeedback, 'Enter details for the new secret and click Save.', 'info');
        focusSecretInput('name');
    }

    function startSecretEdit(name) {
        if (state.secretEdit) {
            setStatus(elements.secretFeedback, 'Finish editing the current secret before editing another.', 'warning');
            return;
        }
        if (!state.secrets.some(secret => secret.name === name)) return;
        state.secretEdit = { mode: 'existing', key: name };
        state.secretDraft = {
            name,
            value: ''
        };
        renderSecretsTable();
        setStatus(elements.secretFeedback, `Updating '${name}'. Enter a new value and save.`, 'info');
        focusSecretInput('value');
    }

    function cancelSecretEdit(showStatus = true) {
        state.secretEdit = null;
        state.secretDraft = null;
        renderSecretsTable();
        if (showStatus) {
            setStatus(elements.secretFeedback, 'Editing cancelled.', 'info');
        }
    }

    async function saveSecretRow(button) {
        if (state.isSavingSecret || !state.secretEdit || !state.secretDraft) return;

        let name = (state.secretDraft.name || '').trim();
        const value = state.secretDraft.value || '';

        if (!name) {
            setStatus(elements.secretFeedback, 'Secret name is required.', 'error');
            return;
        }
        if (!value) {
            setStatus(elements.secretFeedback, 'Secret value is required.', 'error');
            return;
        }

        const normalized = normalizeSecretName(name);
        if (!normalized) {
            setStatus(elements.secretFeedback, 'Secret name must contain letters, numbers, or underscores.', 'error');
            return;
        }
        name = normalized;
        state.secretDraft.name = normalized;

        state.isSavingSecret = true;
        if (button) {
            button.disabled = true;
            setIconButtonLabel(button, 'Saving secret...');
        }
        setStatus(elements.secretFeedback, `Saving ${name}…`, 'info');

        try {
            const result = await updateSecretValue(name, value);
            cancelSecretEdit(false);
            const resultMessage = withRestartNotice(`Saved secret '${name}'.`, result);
            setStatus(elements.secretFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.secretFeedback, `Failed to save secret: ${error.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                setIconButtonLabel(button, 'Save secret');
            }
            state.isSavingSecret = false;
        }
    }

    function normalizeSecretName(name) {
        if (!name) return '';
        return name.replace(/\s+/g, '_').toUpperCase();
    }

    async function updateSecretValue(name, value) {
        const response = await fetch('api/system/secrets', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, value })
        });

        if (!response.ok) {
            const errorData = await safeJson(response);
            throw new Error(errorData?.message || `HTTP ${response.status}`);
        }

        const result = await response.json();
        await actions.loadSecrets();
        await actions.loadGoogleConnection();
        await actions.loadMcpConnections();
        await actions.loadProviders();
        await notifyConfigChanged();
        return result;
    }

    async function deleteSecret(name) {
        const response = await fetch(`api/system/secrets/${encodeURIComponent(name)}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const errorData = await safeJson(response);
            throw new Error(errorData?.message || `HTTP ${response.status}`);
        }

        const result = await response.json();
        await actions.loadSecrets();
        await actions.loadMcpConnections();
        await actions.loadProviders();
        await notifyConfigChanged();
        return result;
    }


    Object.assign(actions, {
        renderSecretsTable,
        renderSecretEditCard,
        focusSecretInput,
        handleSecretsTableClick,
        handleSecretInputChange,
        startNewSecret,
        startSecretEdit,
        cancelSecretEdit,
        saveSecretRow,
        normalizeSecretName,
        updateSecretValue,
        deleteSecret
    });
}(window, document));
