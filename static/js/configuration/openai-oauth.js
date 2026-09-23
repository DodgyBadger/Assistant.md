(function configurationOpenAiOAuthFeature(window) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, elements, helpers, state } = runtime;
    const { escapeHtml, formatDateTime, notifyConfigChanged, safeJson, setStatus } = helpers;

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
            await actions.loadProviders();
            const popupText = opened
                ? 'OpenAI OAuth URL opened. The URL is also shown in the OpenAI provider panel for manual copy/paste.'
                : 'Open the OAuth URL shown in the OpenAI provider panel, then paste the final redirect URL here.';
            setStatus(elements.providerFeedback, popupText, 'info');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to start OpenAI OAuth: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            actions.renderProviders();
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
            await actions.loadProviders();
            setStatus(elements.providerFeedback, 'Open the device URL and enter the code, then check status.', 'info');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to start OpenAI OAuth device code: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            actions.renderProviders();
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
                await actions.loadProviders();
                await actions.loadModels();
                await notifyConfigChanged();
                setStatus(elements.providerFeedback, 'OpenAI OAuth connected.', 'success');
            } else {
                await actions.loadProviders();
                setStatus(elements.providerFeedback, 'OpenAI OAuth device code is still pending.', 'info');
            }
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to check OpenAI OAuth device code: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            actions.renderProviders();
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
            await actions.loadProviders();
            await actions.loadModels();
            await notifyConfigChanged();
            setStatus(elements.providerFeedback, 'OpenAI OAuth connected.', 'success');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to complete OpenAI OAuth: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            actions.renderProviders();
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
            await actions.loadProviders();
            await actions.loadModels();
            await notifyConfigChanged();
            setStatus(elements.providerFeedback, 'OpenAI OAuth disconnected.', 'success');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to disconnect OpenAI OAuth: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            actions.renderProviders();
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

    function handleOpenAiOAuthPasteInput(target) {
        if (!(target instanceof HTMLInputElement) || !target.matches('[data-openai-oauth-paste]')) {
            return false;
        }
        state.openAiOauthPaste = target.value;
        const completeButton = elements.providerList?.querySelector('[data-action="openai-oauth-complete"]');
        if (completeButton instanceof HTMLButtonElement) {
            completeButton.disabled = state.isOpenAiOauthBusy || !target.value.trim();
        }
        return true;
    }

    Object.assign(actions, {
        renderOpenAiOAuthUnsupportedWarning,
        renderOpenAiOAuthPanel,
        formatOAuthStatus,
        startOpenAiOAuth,
        startOpenAiOAuthDevice,
        checkOpenAiOAuthDevice,
        completeOpenAiOAuth,
        disconnectOpenAiOAuth,
        clearOpenAiOauthDeviceDisplay,
        setOpenAiOauthButtonBusy,
        setOpenAiOauthButtonIdle,
        handleOpenAiOAuthPasteInput
    });
}(window));
