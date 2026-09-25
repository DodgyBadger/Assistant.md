(function configurationModelsFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, elements, helpers, state } = runtime;
    const { escapeHtml, iconButton, iconSvg, notifyConfigChanged, safeJson, setStatus, withRestartNotice } = helpers;

    async function loadModels() {
        if (!elements.modelList || state.isLoadingModels) return;

        state.isLoadingModels = true;
        setStatus(elements.modelFeedback, 'Loading models…', 'info');

        try {
            const response = await fetch('api/system/models');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            state.models = Array.isArray(data) ? data : [];
            state.modelsLoadFailed = false;

            if (state.modelEdit && state.modelEdit.mode === 'existing') {
                const stillExists = state.models.some(m => m.name === state.modelEdit.key);
                if (!stillExists) {
                    state.modelEdit = null;
                    state.modelDraft = null;
                }
            }

            renderModels();
            setStatus(elements.modelFeedback, '', 'info');
        } catch (error) {
            state.modelsLoadFailed = true;
            renderModels(true);
            setStatus(elements.modelFeedback, `Failed to load models: ${error.message}`, 'error');
        } finally {
            state.isLoadingModels = false;
        }
    }

    function renderModels(emptyOnError = state.modelsLoadFailed) {
        if (!elements.modelList) return;

        const cards = [];
        const editing = state.modelEdit;
        const draft = state.modelDraft || {};

        if (editing && editing.mode === 'new') {
            cards.push(renderModelEditCard(draft, { isNew: true }));
        }

        if (!state.models.length) {
            const message = emptyOnError ? 'Unable to load model mappings.' : 'No models configured.';
            cards.push(`
                <div class="rounded-lg border border-border-primary bg-app-card px-4 py-3 text-sm text-txt-secondary text-center shadow-sm">
                    ${escapeHtml(message)}
                </div>
            `);
        } else {
            state.models.forEach((model) => {
                if (editing && editing.mode === 'existing' && editing.key === model.name) {
                    cards.push(renderModelEditCard(draft, { isNew: false }));
                } else {
                    cards.push(renderModelViewCard(model));
                }
            });
        }

        elements.modelList.innerHTML = cards.join('');
    }


    function buildProviderOptions(selected, { embeddingOnly = false } = {}) {
        const providers = embeddingOnly
            ? state.providers.filter((provider) => provider.name === 'openai')
            : state.providers;
        if (!providers.length) {
            return '<option value="">No providers available</option>';
        }

        return providers.map((provider) => {
            const isSelected = provider.name === selected ? 'selected' : '';
            const label = provider.user_editable === false
                ? `${provider.name} (built-in)`
                : provider.name;
            return `<option value="${escapeHtml(provider.name)}" ${isSelected}>${escapeHtml(label)}</option>`;
        }).join('');
    }

    function capabilitiesIncludeEmbedding(capabilities) {
        const values = Array.isArray(capabilities)
            ? capabilities
            : String(capabilities || '').split(',');
        return values.some((item) => String(item).trim().toLowerCase() === 'embedding');
    }

    function renderEmbeddingProviderNotice() {
        return `
            <p class="text-xs state-warning mt-1">
                Embedding models currently support only the OpenAI provider.
            </p>
        `;
    }

    function renderModelViewCard(model) {
        const editable = model.user_editable !== false;
        const availabilityBadge = model.available
            ? '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-success border">Available</span>'
            : '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-error border">Unavailable</span>';

        const reasonLine = model.status_message && !model.available
            ? `<div class="text-xs state-error mt-1">${escapeHtml(model.status_message)}</div>`
            : '';
        const capabilities = Array.isArray(model.capabilities) && model.capabilities.length
            ? model.capabilities.join(', ')
            : 'text';
        const embeddingNotice = capabilitiesIncludeEmbedding(model.capabilities)
            ? renderEmbeddingProviderNotice()
            : '';

        const actions = editable
            ? `
                <button data-action="edit" data-model="${escapeHtml(model.name)}" ${iconButton('edit', 'Edit model', 'is-primary')}>${iconSvg('edit')}</button>
                <button data-action="delete" data-model="${escapeHtml(model.name)}" ${iconButton('trash', 'Delete model', 'is-danger')}>${iconSvg('trash')}</button>
            `
            : `<span class="inline-block px-2 py-0.5 text-xs text-txt-secondary bg-app-elevated rounded border border-border-primary">Read-only</span>`;

        return `
            <div class="model-card rounded-lg border border-border-primary bg-app-card px-5 py-4 shadow-sm hover:shadow transition-shadow" data-row="${escapeHtml(model.name)}" data-mode="view" style="max-width: 1400px;">
                <div class="space-y-4">
                    <div class="flex items-start justify-between gap-4">
                        <div class="min-w-0">
                            <div class="font-semibold text-txt-primary text-sm">${escapeHtml(model.name)}</div>
                            <div class="flex items-center gap-2 mt-1">
                                <div class="w-fit">${availabilityBadge}</div>
                            </div>
                            ${reasonLine}
                        </div>
                        <div class="flex gap-2 shrink-0">
                            ${actions}
                        </div>
                    </div>
                    <div class="flex gap-6 items-start flex-wrap">
                        <div>
                            <div class="text-xs font-medium text-txt-secondary mb-1">Provider</div>
                            <div class="text-sm text-txt-primary">${escapeHtml(model.provider)}</div>
                            ${embeddingNotice}
                        </div>
                        <div>
                            <div class="text-xs font-medium text-txt-secondary mb-1">Model Identifier</div>
                            <div class="text-xs font-mono text-txt-primary bg-app-elevated px-2 py-1 rounded border border-border-primary inline-block max-w-xs break-words">${escapeHtml(model.model_string)}</div>
                        </div>
                        <div>
                            <div class="text-xs font-medium text-txt-secondary mb-1">Capabilities</div>
                            <div class="text-xs text-txt-primary bg-app-elevated px-2 py-1 rounded border border-border-primary inline-block max-w-xs break-words">${escapeHtml(capabilities)}</div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    function renderModelEditCard(draft, { isNew }) {
        const rowKey = isNew ? '__new' : (state.modelEdit?.key || draft.name || '');
        const isEmbeddingModel = capabilitiesIncludeEmbedding(draft.capabilities || 'text');
        const selectedProvider = isEmbeddingModel
            ? 'openai'
            : draft.provider !== undefined ? draft.provider : (state.providers[0]?.name || '');
        const providerOptions = buildProviderOptions(selectedProvider, { embeddingOnly: isEmbeddingModel });

        const providerDisabled = state.providers.length === 0 ? 'disabled' : '';
        const providerHelp = isEmbeddingModel
            ? 'Only the OpenAI provider is supported for embedding model aliases.'
            : state.providers.length ? 'Select the provider powering this model.' : 'Add a provider before creating models.';
        const embeddingNotice = isEmbeddingModel ? renderEmbeddingProviderNotice() : '';

        const renameHint = isNew
            ? '<p class="text-xs text-txt-secondary mt-1">Lowercase alias used in assistant files.</p>'
            : '<p class="text-xs text-txt-secondary mt-1">Renaming updates the model alias used in assistants.</p>';

        return `
            <div class="model-card rounded-lg border border-border-primary bg-app-card editing-highlight px-5 py-4 shadow-sm" data-row="${escapeHtml(rowKey)}" data-mode="edit" style="max-width: 1400px;">
                <div class="space-y-4">
                    <div class="grid gap-4 md:grid-cols-2">
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Model Name</label>
                            <input data-field="name" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="e.g. planning" value="${escapeHtml(draft.name || '')}" />
                            ${renameHint}
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Provider</label>
                            <select data-field="provider" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" ${providerDisabled}>
                                ${providerOptions}
                            </select>
                            <p class="text-xs text-txt-secondary mt-1">${providerHelp}</p>
                            ${embeddingNotice}
                        </div>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-txt-primary mb-1.5">Model Identifier</label>
                        <input data-field="model_string" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary font-mono text-sm transition-colors" placeholder="e.g. claude-sonnet-4-5" value="${escapeHtml(draft.model_string || '')}" />
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-txt-primary mb-1.5">Capabilities</label>
                        <input data-field="capabilities" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="e.g. text, vision" value="${escapeHtml(draft.capabilities || 'text')}" />
                        <p class="text-xs text-txt-secondary mt-1">Comma-separated values. Examples: <code>text, vision</code>, <code>embedding</code>, or <code>decision</code>.</p>
                    </div>
                    <div class="flex justify-end gap-2">
                        <button data-action="cancel-model" ${iconButton('circleX', 'Cancel model edit')}>${iconSvg('circleX')}</button>
                        <button data-action="save-model" ${iconButton('save', 'Save model', 'is-primary')}>${iconSvg('save')}</button>
                    </div>
                </div>
            </div>
        `;
    }

    function focusModelInput(field) {
        requestAnimationFrame(() => {
            const el = elements.modelList?.querySelector(`[data-row][data-mode="edit"] [data-field="${field}"]`);
            if (el) {
                el.focus();
            }
        });
    }

    function handleModelTableClick(event) {
        const actionButton = event.target.closest('[data-action]');
        if (!actionButton) return;

        const rowEl = actionButton.closest('[data-row]');
        const rowKey = rowEl?.dataset.row;
        const action = actionButton.dataset.action;

        if (action === 'edit' && rowKey) {
            startModelEdit(rowKey);
        } else if (action === 'delete' && rowKey) {
            deleteModel(rowKey);
        } else if (action === 'cancel-model') {
            cancelModelEdit();
        } else if (action === 'save-model' && rowKey) {
            saveModelRow(rowKey);
        }
    }

    function handleModelInputChange(event) {
        if (!state.modelEdit || !event.target.dataset.field) {
            return;
        }
        const field = event.target.dataset.field;
        if (!state.modelDraft) {
            state.modelDraft = {};
        }
        const wasEmbeddingModel = capabilitiesIncludeEmbedding(state.modelDraft.capabilities || 'text');
        if (field === 'name') {
            const normalized = event.target.value.trim().toLowerCase();
            state.modelDraft[field] = normalized;
            if (event.target.value !== normalized) {
                event.target.value = normalized;
            }
        } else {
            state.modelDraft[field] = event.target.value;
        }
        const isEmbeddingModel = capabilitiesIncludeEmbedding(state.modelDraft.capabilities);
        if (field === 'capabilities' && !wasEmbeddingModel && isEmbeddingModel) {
            state.modelDraft.provider = 'openai';
        }
        if (field === 'capabilities' && wasEmbeddingModel !== isEmbeddingModel) {
            renderModels();
            focusModelInput('capabilities');
        }
    }

    function startModelEdit(modelName) {
        if (state.isSavingModel) return;
        if (state.modelEdit && state.modelEdit.mode === 'new') {
            setStatus(elements.modelFeedback, 'Finish creating the new model before editing another.', 'warning');
            return;
        }

        const model = state.models.find(m => m.name === modelName);
        if (!model || model.user_editable === false) {
            setStatus(elements.modelFeedback, 'This model is read-only.', 'warning');
            return;
        }

        state.modelEdit = { mode: 'existing', key: model.name };
        state.modelDraft = {
            name: model.name,
            provider: model.provider,
            model_string: model.model_string,
            capabilities: Array.isArray(model.capabilities) && model.capabilities.length
                ? model.capabilities.join(', ')
                : 'text'
        };

        renderModels();
        focusModelInput('name');
        setStatus(elements.modelFeedback, `Editing '${model.name}'.`, 'info');
    }

    function startNewModel() {
        if (state.isSavingModel) return;
        if (state.modelEdit) {
            setStatus(elements.modelFeedback, 'Finish editing the current row before adding a new model.', 'warning');
            return;
        }

        const defaultProvider = state.providers[0]?.name || '';
        state.modelEdit = { mode: 'new', key: '__new' };
        state.modelDraft = {
            name: '',
            provider: defaultProvider,
            model_string: '',
            capabilities: 'text'
        };

        renderModels();
        focusModelInput('name');
        setStatus(elements.modelFeedback, 'Enter details for the new model row and click Save.', 'info');
    }

    function cancelModelEdit(message = true) {
        state.modelEdit = null;
        state.modelDraft = null;
        renderModels();
        if (message) {
            setStatus(elements.modelFeedback, 'Editing cancelled.', 'info');
        }
    }

    async function saveModelRow(rowKey) {
        if (state.isSavingModel || !state.modelEdit || !state.modelDraft) return;

        const draft = state.modelDraft;
        let alias = (draft.name || '').trim().toLowerCase();
        const provider = (draft.provider || '').trim();
        const modelString = (draft.model_string || '').trim();
        const capabilitiesInput = (draft.capabilities || '').trim();
        const capabilities = capabilitiesInput
            .split(',')
            .map((item) => item.trim().toLowerCase())
            .filter((item) => item.length > 0);

        const isNew = state.modelEdit.mode === 'new' || rowKey === '__new';
        const originalName = state.modelEdit.mode === 'existing' ? state.modelEdit.key : null;

        if (!alias) {
            setStatus(elements.modelFeedback, 'Model name is required.', 'error');
            return;
        }

        if (!provider || !modelString) {
            setStatus(elements.modelFeedback, 'Provider and model identifier are required.', 'error');
            return;
        }
        if (!capabilities.length) {
            setStatus(elements.modelFeedback, 'At least one capability is required (e.g. text).', 'error');
            return;
        }
        if (capabilities.includes('embedding') && provider !== 'openai') {
            setStatus(elements.modelFeedback, 'Embedding models currently support only the OpenAI provider.', 'error');
            return;
        }

        if ((isNew || alias !== originalName) && state.models.some(m => m.name === alias)) {
            setStatus(elements.modelFeedback, `Model '${alias}' already exists.`, 'error');
            return;
        }

        state.modelDraft.name = alias;
        state.isSavingModel = true;
        setStatus(elements.modelFeedback, 'Saving model…', 'info');

        try {
            const payload = {
                provider: provider,
                model_string: modelString,
                capabilities: capabilities
            };

            const response = await fetch(`api/system/models/${encodeURIComponent(alias)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            let restartRequired = Boolean(result && result.restart_required);

            if (originalName && alias !== originalName) {
                const deleteResponse = await fetch(`api/system/models/${encodeURIComponent(originalName)}`, {
                    method: 'DELETE'
                });
                if (!deleteResponse.ok) {
                    const deleteError = await safeJson(deleteResponse);
                    throw new Error(deleteError?.message || `Failed to remove old alias '${originalName}'.`);
                }

                const deleteResult = await safeJson(deleteResponse);
                restartRequired = restartRequired || Boolean(deleteResult && deleteResult.restart_required);
            }

            cancelModelEdit(false);
            await loadModels();
            await notifyConfigChanged();
            const resultMessage = withRestartNotice(`Saved model '${alias}'.`, { restart_required: restartRequired });
            setStatus(elements.modelFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.modelFeedback, `Failed to save model: ${error.message}`, 'error');
        } finally {
            state.isSavingModel = false;
        }
    }

    async function deleteModel(modelName) {
        if (!window.confirm(`Delete model '${modelName}'?`)) return;

        try {
            const response = await fetch(`api/system/models/${encodeURIComponent(modelName)}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const apiResult = await response.json();

            if (state.modelEdit && state.modelEdit.key === modelName) {
                cancelModelEdit(false);
            }
            await loadModels();
            await notifyConfigChanged();
            const resultMessage = withRestartNotice(`Removed model '${modelName}'.`, apiResult);
            setStatus(elements.modelFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.modelFeedback, `Failed to delete model: ${error.message}`, 'error');
        }
    }

    Object.assign(actions, {
        loadModels,
        renderModels,
        buildProviderOptions,
        capabilitiesIncludeEmbedding,
        renderEmbeddingProviderNotice,
        renderModelViewCard,
        renderModelEditCard,
        focusModelInput,
        handleModelTableClick,
        handleModelInputChange,
        startModelEdit,
        startNewModel,
        cancelModelEdit,
        saveModelRow,
        deleteModel
    });
}(window, document));
