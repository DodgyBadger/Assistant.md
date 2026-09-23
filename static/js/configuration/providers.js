(function configurationProvidersFeature(window, document) {
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
            actions.renderModels();
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
            await actions.loadModels(); // provider availability can update model availability
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
            await actions.loadModels();
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
                await actions.loadModels();
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
            await actions.loadModels();
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
            await actions.loadModels();
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
