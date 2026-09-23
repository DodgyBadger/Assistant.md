(function configurationFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, constants, elements, helpers, resources, state, timers } = runtime;
    const { ACTIVITY_LOG_LEVELS, BUILT_IN_PROVIDER_NAMES, DEFAULT_IMPORT_JOB_STATUSES, SECRET_METADATA } = constants;
    const { escapeHtml, formatDateTime, iconButton, iconSvg, notifyConfigChanged, safeJson, setIconButtonLabel, setStatus, withRestartNotice } = helpers;

    async function loadProviders() {
        if (!elements.providerList || state.isLoadingProviders) return;

        state.isLoadingProviders = true;
        setStatus(elements.providerFeedback, 'Loading providers…', 'info');

        try {
            const response = await fetch('api/system/providers');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            state.providers = Array.isArray(data) ? data : [];
            if (state.providerEdit && state.providerEdit.mode === 'existing') {
                const stillExists = state.providers.some(provider => provider.name === state.providerEdit.key);
                if (!stillExists) {
                    state.providerEdit = null;
                    state.providerDraft = null;
                }
            }

            renderProviders();
            populateProviderOptions();
            renderModels();
            setStatus(elements.providerFeedback, '', 'info');
        } catch (error) {
            renderProviders(true);
            setStatus(elements.providerFeedback, `Failed to load providers: ${error.message}`, 'error');
        } finally {
            state.isLoadingProviders = false;
        }
    }

    function renderProviders(emptyOnError = false) {
        if (!elements.providerList) return;

        const cards = [];
        const editing = state.providerEdit;
        const draft = state.providerDraft || {};

        if (editing && editing.mode === 'new') {
            cards.push(renderProviderEditCard(draft, { isNew: true }));
        }

        if (!state.providers.length) {
            const message = emptyOnError
                ? 'Unable to load providers.'
                : 'No custom providers configured.';
            cards.push(`
                <div class="rounded-lg border border-border-primary bg-app-card px-4 py-3 text-sm text-txt-secondary text-center shadow-sm">
                    ${escapeHtml(message)}
                </div>
            `);
            elements.providerList.innerHTML = cards.join('');
            focusProviderInput();
            return;
        }

        state.providers.forEach((provider) => {
            if (editing && editing.mode === 'existing' && editing.key === provider.name) {
                cards.push(renderProviderEditCard(draft, { isNew: false }));
                return;
            }
            const editable = provider.user_editable === true;
            const isBuiltIn = isBuiltInProviderName(provider.name);
            const isOpenAi = provider.name === 'openai';

            const apiKeyDisplay = provider.api_key
                ? provider.api_key_has_value
                    ? `<div class="flex items-center gap-2">
                        <div class="w-fit"><span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-success border">${'Set'}</span></div>
                        <div class="text-xs text-txt-secondary font-mono">${escapeHtml(provider.api_key)}</div>
                    </div>`
                    : `<div class="flex items-center gap-2">
                        <div class="w-fit"><span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-error border">Not set</span></div>
                        <div class="text-xs state-error">Configure ${escapeHtml(provider.api_key)}</div>
                    </div>`
                : `<div class="flex items-center gap-2"><span class="text-sm text-txt-secondary">No key required</span></div>`;

            const baseUrlDisplay = provider.base_url
                ? provider.base_url_has_value
                    ? `<div class="flex items-center gap-2">
                        <div class="w-fit"><span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-success border">Set</span></div>
                        <div class="text-xs text-txt-secondary font-mono">${escapeHtml(provider.base_url)}</div>
                    </div>`
                    : `<div class="flex items-center gap-2">
                        <div class="w-fit"><span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-error border">Not set</span></div>
                        <div class="text-xs state-error">Configure ${escapeHtml(provider.base_url)}</div>
                    </div>`
                : `<div class="flex items-center gap-2"><span class="text-sm text-txt-secondary">No base URL configured</span></div>`;

            const actions = [
                editable
                    ? `<button data-action="edit" data-provider="${escapeHtml(provider.name)}" ${iconButton('edit', 'Edit provider', 'is-primary')}>${iconSvg('edit')}</button>`
                    : '',
                editable && !isBuiltIn
                    ? `<button data-action="delete" data-provider="${escapeHtml(provider.name)}" ${iconButton('trash', 'Delete provider', 'is-danger')}>${iconSvg('trash')}</button>`
                    : '',
            ].join('');

            const providerMeta = isBuiltIn
                ? '<div class="text-xs text-txt-secondary mt-0.5">Built-in provider</div>'
                : '';
            const openAiOAuthPanel = isOpenAi ? renderOpenAiOAuthPanel(provider) : '';

            cards.push(`
                <div class="provider-card rounded-lg border border-border-primary bg-app-card px-5 py-4 shadow-sm hover:shadow transition-shadow" data-provider-row="${escapeHtml(provider.name)}" style="max-width: 1400px;">
                    <div class="space-y-4">
                        <div class="flex items-center justify-between gap-4">
                            <div>
                                <div class="font-semibold text-txt-primary text-sm">${escapeHtml(provider.name)}</div>
                                ${providerMeta}
                            </div>
                            <div class="flex gap-2 shrink-0">
                                ${actions}
                            </div>
                        </div>
                        <div class="grid gap-6 md:grid-cols-2">
                            <div>
                                <div class="text-xs font-medium text-txt-secondary mb-2">API Key</div>
                                <div>${apiKeyDisplay}</div>
                            </div>
                            <div>
                                <div class="text-xs font-medium text-txt-secondary mb-2">Base URL</div>
                                <div>${baseUrlDisplay}</div>
                            </div>
                        </div>
                        ${openAiOAuthPanel}
                    </div>
                </div>
            `);
        });

        elements.providerList.innerHTML = cards.join('');
        focusProviderInput();
    }

    function populateProviderOptions() {
        // provider options are built per-row during render; nothing to do here.
    }

    function isBuiltInProviderName(name) {
        return BUILT_IN_PROVIDER_NAMES.has(String(name || '').toLowerCase());
    }

    function renderOpenAiOAuthUnsupportedWarning(extraClass = '') {
        const classSuffix = extraClass ? ` ${extraClass}` : '';
        return `
            <div class="rounded-md border border-border-secondary bg-app-card px-3 py-2 text-xs state-warning${classSuffix}">
                OpenAI OAuth is experimental and is not officially supported by OpenAI for Assistant.md. Use it at your own risk: it could break if OpenAI changes the flow, and your account could be disabled if OpenAI decides to restrict this access.
            </div>
        `;
    }

    function renderOpenAiOAuthPanel(provider) {
        const oauthEnabled = provider.oauth_enabled === true;
        const oauthStatus = provider.oauth_status || 'disabled';
        const connected = oauthStatus === 'connected';
        const pending = oauthStatus === 'pending';
        const canClearOAuth = connected || pending;
        const pendingFlow = provider.oauth_pending_flow || '';
        const disabledReason = provider.oauth_disabled_reason || '';
        const accountText = provider.oauth_account_id ? `Account ${provider.oauth_account_id}` : 'No account connected';
        const expiresText = provider.oauth_expires_at ? `Expires ${formatDateTime(provider.oauth_expires_at)}` : 'No token expiry recorded';
        const refreshText = provider.oauth_last_refresh_at ? `Last refresh ${formatDateTime(provider.oauth_last_refresh_at)}` : 'No refresh recorded';
        const fallbackText = provider.oauth_api_key_fallback_enabled
            ? provider.oauth_api_key_fallback_available
                ? 'API key fallback enabled and available'
                : 'API key fallback enabled but no key is set'
            : 'API key fallback disabled';
        const selectedMode = formatAuthMode(provider.configured_auth_mode);
        const activeMode = formatAuthMode(provider.effective_auth_mode);
        const selectedModeText = provider.configured_auth_mode && provider.configured_auth_mode !== provider.effective_auth_mode
            ? `Selected ${selectedMode}`
            : '';
        const providerStatusMessage = provider.status_message || '';
        const statusTone = connected ? 'pill-success' : pending ? 'pill-warning' : 'pill-error';
        const disableControls = state.isOpenAiOauthBusy || !oauthEnabled;
        const disabledAttr = disableControls ? 'disabled' : '';
        const pasteValue = escapeHtml(state.openAiOauthPaste || '');
        const authUrl = state.openAiOauthAuthUrl || '';
        const authUrlValue = escapeHtml(authUrl);
        const deviceVerificationUrl = provider.oauth_device_verification_url || state.openAiOauthDeviceVerificationUrl || '';
        const deviceUserCode = provider.oauth_device_user_code || state.openAiOauthDeviceUserCode || '';
        const deviceExpiresAt = provider.oauth_pending_expires_at || state.openAiOauthDeviceExpiresAt || '';
        const devicePollInterval = provider.oauth_device_poll_interval_seconds || state.openAiOauthDevicePollIntervalSeconds;
        const authUrlPanel = authUrl
            ? `<div class="mt-3 space-y-2">
                    <div class="text-xs font-medium text-txt-secondary">OpenAI auth URL</div>
                    <textarea readonly class="w-full min-h-[76px] px-3 py-2 border border-border-secondary rounded-md bg-app-card text-txt-primary text-xs font-mono resize-y">${authUrlValue}</textarea>
                    <a href="${authUrlValue}" target="_blank" rel="noopener" class="inline-flex text-xs text-accent hover:text-accent-hover">Open in browser</a>
                </div>`
            : '';
        const devicePanel = deviceVerificationUrl || deviceUserCode
            ? `<div class="mt-3 rounded-md border border-border-secondary bg-app-card px-3 py-3 space-y-2">
                    <div class="flex flex-wrap items-center justify-between gap-2">
                        <div class="text-xs font-medium text-txt-secondary">Device code</div>
                        ${pendingFlow ? `<div class="text-xs text-txt-secondary">${escapeHtml(pendingFlow === 'device_code' ? 'Device flow pending' : 'Browser flow pending')}</div>` : ''}
                    </div>
                    ${deviceVerificationUrl ? `<a href="${escapeHtml(deviceVerificationUrl)}" target="_blank" rel="noopener" class="inline-flex text-xs text-accent hover:text-accent-hover">${escapeHtml(deviceVerificationUrl)}</a>` : ''}
                    ${deviceUserCode ? `<div class="inline-flex items-center rounded-md border border-border-secondary bg-app-elevated px-3 py-2 font-mono text-sm tracking-wide text-txt-primary">${escapeHtml(deviceUserCode)}</div>` : ''}
                    <div class="text-xs text-txt-secondary">${deviceExpiresAt ? `Expires ${escapeHtml(formatDateTime(deviceExpiresAt))}` : ''}${devicePollInterval ? ` · Check every ${escapeHtml(String(devicePollInterval))}s` : ''}</div>
                </div>`
            : '';

        return `
            <div class="rounded-lg border border-border-secondary bg-app-elevated px-4 py-3">
                <div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div class="space-y-2">
                        <div class="flex flex-wrap items-center gap-2">
                            <span class="text-xs font-medium text-txt-secondary">OpenAI OAuth</span>
                            <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium ${statusTone} border">${escapeHtml(formatOAuthStatus(oauthStatus))}</span>
                        </div>
                        <div class="text-xs text-txt-secondary">Using ${escapeHtml(activeMode)}${selectedModeText ? ` · ${escapeHtml(selectedModeText)}` : ''}</div>
                        <div class="text-xs text-txt-secondary">${escapeHtml(accountText)} · ${escapeHtml(expiresText)} · ${escapeHtml(refreshText)}</div>
                        <div class="text-xs text-txt-secondary">${escapeHtml(fallbackText)}</div>
                        ${providerStatusMessage ? `<div class="text-xs state-warning">${escapeHtml(providerStatusMessage)}</div>` : ''}
                        ${provider.oauth_last_refresh_error ? `<div class="text-xs state-error">${escapeHtml(provider.oauth_last_refresh_error)}</div>` : ''}
                        ${!oauthEnabled ? `<div class="text-xs state-warning">${escapeHtml(disabledReason || 'Disabled by openai_oauth_enabled.')}</div>` : ''}
                    </div>
                    <div class="flex flex-wrap gap-2 justify-end">
                        <button type="button" data-action="openai-oauth-start" class="px-3 py-2 rounded-md border border-border-secondary bg-app-card text-xs font-medium text-txt-primary hover:border-border-secondary disabled:opacity-50 disabled:cursor-not-allowed" ${disabledAttr}>${connected ? 'Reconnect' : 'Connect'}</button>
                        <button type="button" data-action="openai-oauth-device-start" class="px-3 py-2 rounded-md border border-border-secondary bg-app-card text-xs font-medium text-txt-primary hover:border-border-secondary disabled:opacity-50 disabled:cursor-not-allowed" ${disabledAttr}>Device Code</button>
                        <button type="button" data-action="openai-oauth-device-check" class="px-3 py-2 rounded-md border border-border-secondary bg-app-card text-xs font-medium text-txt-primary hover:border-border-secondary disabled:opacity-50 disabled:cursor-not-allowed" ${state.isOpenAiOauthBusy || !oauthEnabled || pendingFlow !== 'device_code' ? 'disabled' : ''}>Check Status</button>
                        <button type="button" data-action="openai-oauth-disconnect" class="px-3 py-2 rounded-md border border-border-secondary bg-app-card text-xs font-medium text-txt-primary hover:border-border-secondary disabled:opacity-50 disabled:cursor-not-allowed" ${state.isOpenAiOauthBusy || !canClearOAuth ? 'disabled' : ''}>${pending ? 'Cancel' : 'Disconnect'}</button>
                    </div>
                </div>
                ${renderOpenAiOAuthUnsupportedWarning('mt-3')}
                <div class="mt-3 flex flex-col gap-2 md:flex-row">
                    <input data-openai-oauth-paste class="w-full flex-1 px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="Paste the final redirect URL or code" value="${pasteValue}" ${disabledAttr} />
                    <button type="button" data-action="openai-oauth-complete" class="shrink-0 px-3 py-2 rounded-md bg-accent text-white text-xs font-medium hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed" ${disableControls || !state.openAiOauthPaste.trim() ? 'disabled' : ''}>Complete OAuth</button>
                </div>
                ${authUrlPanel}
                ${devicePanel}
            </div>
        `;
    }

    function formatOAuthStatus(status) {
        const labels = {
            connected: 'Connected',
            pending: 'Pending',
            disconnected: 'Disconnected',
            disabled: 'Disabled',
        };
        return labels[status] || status || 'Unknown';
    }

    function formatAuthMode(mode) {
        return mode === 'oauth' ? 'OAuth' : 'API key';
    }


    function renderProviderEditCard(draft, { isNew }) {
        const rowKey = isNew ? '__new' : (state.providerEdit?.key || draft.name || '');
        const nameReadonly = isNew ? '' : 'readonly';
        const nameHelp = isNew
            ? 'Provider names identify custom endpoints used by models.'
            : 'Provider names are identities and cannot be renamed here.';
        const isOpenAiDraft = !isNew && draft.name === 'openai';
        const openAiAuthControls = isOpenAiDraft
            ? `
                <div class="rounded-lg border border-border-secondary bg-app-elevated px-4 py-3">
                    <div class="grid gap-4 md:grid-cols-2">
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Auth Mode</label>
                            <select data-provider-field="auth_mode" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors">
                                <option value="api_key" ${(draft.auth_mode || 'api_key') === 'api_key' ? 'selected' : ''}>API key</option>
                                <option value="oauth" ${draft.auth_mode === 'oauth' ? 'selected' : ''}>OAuth</option>
                            </select>
                            <p class="text-xs text-txt-secondary mt-1">OAuth requires openai_oauth_enabled and a connected account.</p>
                            ${draft.auth_mode === 'oauth' ? renderOpenAiOAuthUnsupportedWarning('mt-2') : ''}
                        </div>
                        <label class="flex items-start gap-3 rounded-md border border-border-secondary px-3 py-2">
                            <input data-provider-field="oauth_api_key_fallback_enabled" type="checkbox" class="mt-1 h-4 w-4 rounded border-border-secondary text-accent focus:ring-accent" ${draft.oauth_api_key_fallback_enabled ? 'checked' : ''} />
                            <span>
                                <span class="block text-xs font-medium text-txt-primary">Allow API key fallback</span>
                                <span class="block text-xs text-txt-secondary mt-1">Only use the API key when OAuth cannot be used.</span>
                            </span>
                        </label>
                    </div>
                </div>
            `
            : '';
        return `
            <div class="provider-card rounded-lg border border-border-primary bg-app-card editing-highlight px-5 py-4 shadow-sm" data-provider-row="${escapeHtml(rowKey)}" data-mode="edit" style="max-width: 1400px;">
                <div class="space-y-4">
                    <div class="grid gap-4 md:grid-cols-3">
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Provider Name</label>
                            <input data-provider-field="name" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="e.g. local-ollama" value="${escapeHtml(draft.name || '')}" ${nameReadonly} />
                            <p class="text-xs text-txt-secondary mt-1">${nameHelp}</p>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">API Key Secret</label>
                            <input data-provider-field="api_key" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="SECRET_NAME (optional)" value="${escapeHtml(draft.api_key || '')}" />
                            <p class="text-xs text-txt-secondary mt-1">Enter the secret name; set the value in Secrets.</p>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Base URL Secret</label>
                            <input data-provider-field="base_url" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="SECRET_NAME (optional)" value="${escapeHtml(draft.base_url || '')}" />
                            <p class="text-xs text-txt-secondary mt-1">Store base URLs as secrets; enter the secret name here.</p>
                        </div>
                    </div>
                    ${openAiAuthControls}
                    <div class="flex justify-end gap-2">
                        <button data-action="cancel-provider" ${iconButton('circleX', 'Cancel provider edit')}>${iconSvg('circleX')}</button>
                        <button data-action="save-provider" ${iconButton('save', 'Save provider', 'is-primary')}>${iconSvg('save')}</button>
                    </div>
                </div>
            </div>
        `;
    }

    function focusProviderInput(field = 'name') {
        if (!state.providerEdit) return;
        requestAnimationFrame(() => {
            const editableName = state.providerEdit?.mode === 'new';
            const targetField = editableName ? field : (field === 'name' ? 'api_key' : field);
            const el = elements.providerList?.querySelector(`[data-provider-row][data-mode="edit"] [data-provider-field="${targetField}"]`);
            if (el instanceof HTMLInputElement) {
                el.focus();
                el.select();
            }
        });
    }

    async function loadModels() {
        if (!elements.modelList || state.isLoadingModels) return;

        state.isLoadingModels = true;
        setStatus(elements.modelFeedback, 'Loading models…', 'info');

        try {
            const response = await fetch('api/system/models');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            state.models = Array.isArray(data) ? data : [];

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
            renderModels(true);
            setStatus(elements.modelFeedback, `Failed to load models: ${error.message}`, 'error');
        } finally {
            state.isLoadingModels = false;
        }
    }

    function renderModels(emptyOnError = false) {
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
                        <p class="text-xs text-txt-secondary mt-1">Comma-separated values. Example: <code>text, vision</code>.</p>
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

    async function handleProviderTableClick(event) {
        const actionButton = event.target.closest('[data-action]');
        if (!actionButton) return;

        const providerName = actionButton.getAttribute('data-provider');
        const action = actionButton.dataset.action;

        if (action === 'edit') {
            startProviderEdit(providerName);
        } else if (action === 'delete') {
            await deleteProvider(providerName);
        } else if (action === 'cancel-provider') {
            cancelProviderEdit();
        } else if (action === 'save-provider') {
            await saveProviderRow(actionButton);
        } else if (action === 'openai-oauth-start') {
            await startOpenAiOAuth(actionButton);
        } else if (action === 'openai-oauth-device-start') {
            await startOpenAiOAuthDevice(actionButton);
        } else if (action === 'openai-oauth-device-check') {
            await checkOpenAiOAuthDevice(actionButton);
        } else if (action === 'openai-oauth-complete') {
            await completeOpenAiOAuth(actionButton);
        } else if (action === 'openai-oauth-disconnect') {
            await disconnectOpenAiOAuth(actionButton);
        }
    }

    function handleProviderInputChange(event) {
        const target = event.target;
        if (target instanceof HTMLInputElement && target.matches('[data-openai-oauth-paste]')) {
            state.openAiOauthPaste = target.value;
            const completeButton = elements.providerList?.querySelector('[data-action="openai-oauth-complete"]');
            if (completeButton instanceof HTMLButtonElement) {
                completeButton.disabled = state.isOpenAiOauthBusy || !target.value.trim();
            }
            return;
        }
        if (!state.providerEdit || !target.dataset.providerField) return;
        if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLSelectElement)) return;

        if (!state.providerDraft) {
            state.providerDraft = {};
        }
        if (target instanceof HTMLInputElement && target.type === 'checkbox') {
            state.providerDraft[target.dataset.providerField] = target.checked;
        } else {
            state.providerDraft[target.dataset.providerField] = target.value;
        }
    }

    function startNewProvider() {
        if (state.providerEdit) {
            setStatus(elements.providerFeedback, 'Finish editing the current provider before adding another.', 'warning');
            return;
        }
        state.providerEdit = { mode: 'new', key: '__new' };
        state.providerDraft = {
            name: '',
            api_key: '',
            base_url: ''
        };
        renderProviders();
        setStatus(elements.providerFeedback, 'Enter details for the new provider and click Save.', 'info');
        focusProviderInput('name');
    }

    function startProviderEdit(providerName) {
        if (state.providerEdit) {
            setStatus(elements.providerFeedback, 'Finish editing the current provider before editing another.', 'warning');
            return;
        }
        const provider = state.providers.find(p => p.name === providerName);
        if (!provider) return;

        if (provider.user_editable === false) {
            setStatus(elements.providerFeedback, 'Built-in providers cannot be edited.', 'warning');
            return;
        }

        state.providerEdit = { mode: 'existing', key: provider.name };
        state.providerDraft = {
            name: provider.name,
            api_key: provider.api_key || '',
            base_url: provider.base_url || '',
            auth_mode: provider.configured_auth_mode || 'api_key',
            oauth_api_key_fallback_enabled: provider.oauth_api_key_fallback_enabled === true
        };
        renderProviders();
        setStatus(elements.providerFeedback, `Editing '${provider.name}'.`, 'info');
        focusProviderInput('api_key');
    }

    function cancelProviderEdit(showStatus = true) {
        state.providerEdit = null;
        state.providerDraft = null;
        renderProviders();
        if (showStatus) {
            setStatus(elements.providerFeedback, 'Editing cancelled.', 'info');
        }
    }

    async function saveProviderRow(button) {
        if (state.isSavingProvider || !state.providerEdit || !state.providerDraft) return;

        const draft = state.providerDraft;
        const name = (draft.name || '').trim();
        const apiKeyInput = (draft.api_key || '').trim();
        const baseUrlInput = (draft.base_url || '').trim();

        if (!name) {
            setStatus(elements.providerFeedback, 'Provider name is required.', 'error');
            return;
        }

        if (baseUrlInput && baseUrlInput.includes('://')) {
            setStatus(elements.providerFeedback, 'Base URL must reference a secret name; store the actual URL via the Secrets form.', 'error');
            return;
        }

        state.isSavingProvider = true;
        if (button) {
            button.disabled = true;
            setIconButtonLabel(button, 'Saving provider...');
        }
        setStatus(elements.providerFeedback, 'Saving provider…', 'info');

        try {
            const payload = {
                api_key: apiKeyInput ? actions.normalizeSecretName(apiKeyInput) : '',
                base_url: baseUrlInput ? actions.normalizeSecretName(baseUrlInput) : ''
            };
            if (name === 'openai') {
                payload.auth_mode = draft.auth_mode === 'oauth' ? 'oauth' : 'api_key';
                payload.oauth_api_key_fallback_enabled = draft.oauth_api_key_fallback_enabled === true;
            }

            const response = await fetch(`api/system/providers/${encodeURIComponent(name)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const providerResult = await response.json();
            cancelProviderEdit(false);
            await loadProviders();
            await loadModels(); // provider availability can update model availability
            await actions.loadSecrets();
            await notifyConfigChanged();
            const resultMessage = withRestartNotice(`Saved provider '${name}'.`, providerResult);
            setStatus(elements.providerFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to save provider: ${error.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                setIconButtonLabel(button, 'Save provider');
            }
            state.isSavingProvider = false;
        }
    }

    async function deleteProvider(providerName) {
        if (!window.confirm(`Delete provider '${providerName}'? Models referencing it must be removed first.`)) {
            return;
        }

        try {
            const response = await fetch(`api/system/providers/${encodeURIComponent(providerName)}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();

            await loadProviders();
            await loadModels();
            await actions.loadSecrets();
            await notifyConfigChanged();
            if (state.providerEdit?.key === providerName) {
                cancelProviderEdit(false);
            }
            const resultMessage = withRestartNotice(`Removed provider '${providerName}'.`, result);
            setStatus(elements.providerFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to delete provider: ${error.message}`, 'error');
        }
    }

    async function startOpenAiOAuth(button) {
        if (state.isOpenAiOauthBusy) return;

        state.isOpenAiOauthBusy = true;
        setOpenAiOauthButtonBusy(button, 'Starting OAuth...');
        setStatus(elements.providerFeedback, 'Starting OpenAI OAuth…', 'info');

        try {
            const response = await fetch('api/system/providers/openai/oauth/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            const opened = window.open(result.auth_url, '_blank', 'noopener');
            state.openAiOauthPaste = '';
            state.openAiOauthAuthUrl = result.auth_url || '';
            clearOpenAiOauthDeviceDisplay();
            await loadProviders();
            const popupText = opened
                ? 'OpenAI OAuth URL opened. The URL is also shown in the OpenAI provider panel for manual copy/paste.'
                : 'Open the OAuth URL shown in the OpenAI provider panel, then paste the final redirect URL here.';
            setStatus(elements.providerFeedback, popupText, 'info');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to start OpenAI OAuth: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            renderProviders();
            setOpenAiOauthButtonIdle(button, 'Connect');
        }
    }

    async function startOpenAiOAuthDevice(button) {
        if (state.isOpenAiOauthBusy) return;

        state.isOpenAiOauthBusy = true;
        setOpenAiOauthButtonBusy(button, 'Starting...');
        setStatus(elements.providerFeedback, 'Starting OpenAI OAuth device code…', 'info');

        try {
            const response = await fetch('api/system/providers/openai/oauth/device/start', {
                method: 'POST'
            });
            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            state.openAiOauthPaste = '';
            state.openAiOauthAuthUrl = '';
            state.openAiOauthDeviceVerificationUrl = result.verification_url || '';
            state.openAiOauthDeviceUserCode = result.user_code || '';
            state.openAiOauthDeviceExpiresAt = result.expires_at || '';
            state.openAiOauthDevicePollIntervalSeconds = result.poll_interval_seconds || null;
            await loadProviders();
            setStatus(elements.providerFeedback, 'Open the device URL and enter the code, then check status.', 'info');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to start OpenAI OAuth device code: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            renderProviders();
            setOpenAiOauthButtonIdle(button, 'Device Code');
        }
    }

    async function checkOpenAiOAuthDevice(button) {
        if (state.isOpenAiOauthBusy) return;

        state.isOpenAiOauthBusy = true;
        setOpenAiOauthButtonBusy(button, 'Checking...');
        setStatus(elements.providerFeedback, 'Checking OpenAI OAuth device code…', 'info');

        try {
            const response = await fetch('api/system/providers/openai/oauth/device/check', {
                method: 'POST'
            });
            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            if (result.status === 'connected') {
                clearOpenAiOauthDeviceDisplay();
                await loadProviders();
                await loadModels();
                await notifyConfigChanged();
                setStatus(elements.providerFeedback, 'OpenAI OAuth connected.', 'success');
            } else {
                await loadProviders();
                setStatus(elements.providerFeedback, 'OpenAI OAuth device code is still pending.', 'info');
            }
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to check OpenAI OAuth device code: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            renderProviders();
            setOpenAiOauthButtonIdle(button, 'Check Status');
        }
    }

    async function completeOpenAiOAuth(button) {
        if (state.isOpenAiOauthBusy) return;
        const pasted = (state.openAiOauthPaste || '').trim();
        if (!pasted) {
            setStatus(elements.providerFeedback, 'Paste the final redirect URL or code first.', 'warning');
            return;
        }

        state.isOpenAiOauthBusy = true;
        setOpenAiOauthButtonBusy(button, 'Completing OAuth...');
        setStatus(elements.providerFeedback, 'Completing OpenAI OAuth…', 'info');

        try {
            const payload = pasted.includes('://')
                ? { redirect_url: pasted }
                : { code: pasted };
            const response = await fetch('api/system/providers/openai/oauth/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            await response.json();
            state.openAiOauthPaste = '';
            state.openAiOauthAuthUrl = '';
            clearOpenAiOauthDeviceDisplay();
            await loadProviders();
            await loadModels();
            await notifyConfigChanged();
            setStatus(elements.providerFeedback, 'OpenAI OAuth connected.', 'success');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to complete OpenAI OAuth: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            renderProviders();
            setOpenAiOauthButtonIdle(button, 'Complete OAuth');
        }
    }

    async function disconnectOpenAiOAuth(button) {
        if (state.isOpenAiOauthBusy) return;
        if (!window.confirm('Disconnect the OpenAI OAuth account?')) return;

        state.isOpenAiOauthBusy = true;
        setOpenAiOauthButtonBusy(button, 'Disconnecting...');
        setStatus(elements.providerFeedback, 'Disconnecting OpenAI OAuth…', 'info');

        try {
            const response = await fetch('api/system/providers/openai/oauth', {
                method: 'DELETE'
            });
            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            await response.json();
            state.openAiOauthPaste = '';
            state.openAiOauthAuthUrl = '';
            clearOpenAiOauthDeviceDisplay();
            await loadProviders();
            await loadModels();
            await notifyConfigChanged();
            setStatus(elements.providerFeedback, 'OpenAI OAuth disconnected.', 'success');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to disconnect OpenAI OAuth: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            renderProviders();
            setOpenAiOauthButtonIdle(button, 'Disconnect');
        }
    }

    function clearOpenAiOauthDeviceDisplay() {
        state.openAiOauthDeviceVerificationUrl = '';
        state.openAiOauthDeviceUserCode = '';
        state.openAiOauthDeviceExpiresAt = '';
        state.openAiOauthDevicePollIntervalSeconds = null;
    }

    function setOpenAiOauthButtonBusy(button, label) {
        if (button instanceof HTMLButtonElement) {
            button.disabled = true;
            button.textContent = label;
        }
    }

    function setOpenAiOauthButtonIdle(button, label) {
        if (button instanceof HTMLButtonElement) {
            button.disabled = false;
            button.textContent = label;
        }
    }


    Object.assign(actions, {
        loadProviders,
        renderProviders,
        populateProviderOptions,
        isBuiltInProviderName,
        renderOpenAiOAuthUnsupportedWarning,
        renderOpenAiOAuthPanel,
        formatOAuthStatus,
        formatAuthMode,
        renderProviderEditCard,
        focusProviderInput,
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
        deleteModel,
        handleProviderTableClick,
        handleProviderInputChange,
        startNewProvider,
        startProviderEdit,
        cancelProviderEdit,
        saveProviderRow,
        deleteProvider,
        startOpenAiOAuth,
        startOpenAiOAuthDevice,
        checkOpenAiOAuthDevice,
        completeOpenAiOAuth,
        disconnectOpenAiOAuth,
        clearOpenAiOauthDeviceDisplay,
        setOpenAiOauthButtonBusy,
        setOpenAiOauthButtonIdle
    });
}(window, document));
