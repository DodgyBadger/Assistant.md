(function configurationConnectionsFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, constants, elements, helpers, resources, state, timers } = runtime;
    const { ACTIVITY_LOG_LEVELS, BUILT_IN_PROVIDER_NAMES, DEFAULT_IMPORT_JOB_STATUSES, SECRET_METADATA } = constants;
    const { escapeHtml, formatDateTime, iconButton, iconSvg, notifyConfigChanged, safeJson, setIconButtonLabel, setStatus, withRestartNotice } = helpers;

    async function loadSecrets() {
        if (!elements.secretsList || state.isLoadingSecrets) return;

        state.isLoadingSecrets = true;
        try {
            const response = await fetch('api/system/secrets');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            state.secrets = Array.isArray(data) ? data : [];
            if (state.secretEdit && state.secretEdit.mode === 'existing') {
                const stillExists = state.secrets.some(secret => secret.name === state.secretEdit.key);
                if (!stillExists) {
                    state.secretEdit = null;
                    state.secretDraft = null;
                }
            }
            actions.renderSecretsTable();
            actions.updateImportOcrAvailability();
        } catch (error) {
            elements.secretsList.innerHTML = `
                <div class="rounded-lg border state-surface-error px-4 py-3 text-sm text-center shadow-sm">
                    Failed to load secrets: ${escapeHtml(error.message)}
                </div>
            `;
        } finally {
            state.isLoadingSecrets = false;
        }
    }

    async function loadGoogleConnection() {
        if (!elements.googleConnectionsList || state.isLoadingGoogle) return;
        state.isLoadingGoogle = true;
        try {
            const response = await fetch('api/system/connections/google/connections', { cache: 'no-store' });
            const payload = await safeJson(response);
            if (!response.ok) throw new Error(payload?.message || `HTTP ${response.status}`);
            state.googleConnections = Array.isArray(payload) ? payload : [];
            renderGoogleConnection();
        } catch (error) {
            setStatus(elements.connectionsFeedback, `Google connections unavailable: ${error.message}`, 'error');
        } finally {
            state.isLoadingGoogle = false;
        }
    }

    function renderGoogleConnection() {
        const list = elements.googleConnectionsList;
        const template = elements.googleConnectionForm;
        if (!(list instanceof HTMLElement) || !(template instanceof HTMLFormElement)) return;
        list.replaceChildren();
        state.googleConnections.forEach((connection) => appendGoogleConnectionCard(connection));
        if (state.googleDraft) appendGoogleConnectionCard(null);
    }

    function appendGoogleConnectionCard(connection) {
        const list = elements.googleConnectionsList;
        const template = elements.googleConnectionForm;
        if (!(list instanceof HTMLElement) || !(template instanceof HTMLFormElement)) return;
        const details = document.createElement('details');
        details.className = 'rounded-lg border border-border-primary bg-app-card shadow-sm';
        details.open = !connection;
        details.dataset.googleId = connection?.connection_id || 'draft';
        const draftAuthorizationRequired = Boolean(connection?.gmail_available && connection?.gmail?.draft_creation_enabled && !connection?.gmail_draft_available);
        const gmailReady = Boolean(connection?.gmail_available && (!connection?.gmail?.draft_creation_enabled || connection?.gmail_draft_available));
        const summary = document.createElement('summary');
        summary.className = 'collapsible-summary connection-card-summary';
        const statusIconTone = gmailReady ? 'state-success' : (draftAuthorizationRequired ? 'state-warning' : 'text-txt-secondary');
        const statusIconTitle = gmailReady ? 'Connected' : (draftAuthorizationRequired ? 'Reauthorization required for Gmail drafts' : 'Setup required');
        const statusIconName = gmailReady ? 'check' : (draftAuthorizationRequired ? 'alert' : 'x');
        const statusIcon = connection ? `<span class="connection-card-status-icon ${statusIconTone}" title="${statusIconTitle}">${iconSvg(statusIconName)}</span>` : '';
        summary.innerHTML = `<div class="summary-text"><span class="summary-title">${escapeHtml(connection?.display_name || 'New Google connection')}</span>${statusIcon}</div><svg class="chevron" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M6 8l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>`;
        const form = template.cloneNode(true);
        form.removeAttribute('id');
        form.classList.remove('hidden');
        form.classList.remove('rounded-lg', 'border', 'border-border-primary', 'shadow-sm');
        form.dataset.googleId = connection?.connection_id || 'draft';
        details.append(summary, form);
        list.append(details);
        const setValue = (name, value) => {
            const input = form.elements.namedItem(name);
            if (input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) input.value = value ?? '';
        };
        setValue('display_name', connection?.display_name || '');
        setValue('client_id', connection?.client_id || '');
        setValue('client_secret', '');
        setValue('redirect_uri', connection?.oauth_redirect_uri || googleDraftRedirectUri());
        setValue('search_default_results', connection?.gmail?.search_default_results ?? 20);
        setValue('search_max_results', connection?.gmail?.search_max_results ?? 100);
        setValue('message_max_characters', connection?.gmail?.message_max_characters ?? 50000);
        setValue('thread_max_messages', connection?.gmail?.thread_max_messages ?? 25);
        setValue('attachment_max_mb', connection?.gmail?.attachment_max_mb ?? 25);
        setValue('draft_max_characters', connection?.gmail?.draft_max_characters ?? 50000);
        const attachmentDownloadInput = form.elements.namedItem('attachment_download_enabled');
        if (attachmentDownloadInput instanceof HTMLInputElement) attachmentDownloadInput.checked = connection?.gmail?.attachment_download_enabled ?? false;
        const draftCreationInput = form.elements.namedItem('draft_creation_enabled');
        if (draftCreationInput instanceof HTMLInputElement) draftCreationInput.checked = connection?.gmail?.draft_creation_enabled ?? false;
        const defaultInput = form.elements.namedItem('is_default');
        if (defaultInput instanceof HTMLInputElement) {
            defaultInput.checked = connection?.is_default || (!connection && state.googleConnections.length === 0);
        }
        const labels = {
            not_configured: connection?.client_id ? 'Client secret required' : 'Not configured',
            authorization_required: 'Ready to authorize',
            ready: connection?.gmail_available
                ? (connection?.gmail?.draft_creation_enabled && !connection?.gmail_draft_available
                    ? 'Connected; reauthorize to enable Gmail drafts'
                    : 'Connected; Gmail tools available')
                : 'Connected; Gmail scope required',
            reconnect_required: 'Reconnect required',
        };
        const account = connection?.account_email ? ` as ${connection.account_email}` : '';
        const status = form.querySelector('#google-connection-status');
        if (status) status.removeAttribute('id');
        const statusTone = gmailReady ? 'success' : (draftAuthorizationRequired ? 'warning' : 'info');
        setStatus(status, connection ? `${labels[connection.state] || connection.state}${account}.` : 'Enter Google OAuth client settings.', statusTone);
        const feedback = form.querySelector('#google-connection-feedback');
        if (feedback) feedback.removeAttribute('id');
        const authorize = form.querySelector('[data-google-action="authorize"]');
        const disconnect = form.querySelector('[data-google-action="disconnect"]');
        const remove = form.querySelector('[data-google-action="delete"]');
        if (authorize instanceof HTMLButtonElement) {
            authorize.textContent = connection?.connected ? 'Reauthorize Google' : 'Authorize Google';
            authorize.disabled = !connection;
        }
        if (disconnect instanceof HTMLButtonElement) disconnect.disabled = !connection?.connected;
        if (remove instanceof HTMLButtonElement) remove.dataset.googleAction = connection ? 'delete' : 'cancel';
    }

    function startGoogleConnectionDraft() {
        if (state.googleDraft) {
            elements.googleConnectionsList?.lastElementChild?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            return;
        }
        state.googleDraft = true;
        renderGoogleConnection();
        elements.googleConnectionsList?.lastElementChild?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function googleDraftRedirectUri() {
        const publicUrl = document.getElementById('configured-public-url')?.textContent?.trim();
        if (!publicUrl || publicUrl === 'Loading…' || publicUrl === 'Not configured') return '';
        try {
            return new URL('/api/system/connections/google/oauth/callback', publicUrl).href;
        } catch (_error) {
            return '';
        }
    }

    async function saveGoogleConnection(event) {
        event.preventDefault();
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || state.isSavingGoogle) return;
        const values = new FormData(form);
        const id = form.dataset.googleId;
        if (id && id !== 'draft') cancelGoogleOAuthPoll(id);
        const creating = id === 'draft';
        const payload = {
            display_name: String(values.get('display_name') || '').trim(),
            client_id: String(values.get('client_id') || '').trim(),
            is_default: values.get('is_default') === 'on',
            gmail: {
                search_default_results: Number(values.get('search_default_results')),
                search_max_results: Number(values.get('search_max_results')),
                message_max_characters: Number(values.get('message_max_characters')),
                thread_max_messages: Number(values.get('thread_max_messages')),
                attachment_download_enabled: values.get('attachment_download_enabled') === 'on',
                attachment_max_mb: Number(values.get('attachment_max_mb')),
                draft_creation_enabled: values.get('draft_creation_enabled') === 'on',
                draft_max_characters: Number(values.get('draft_max_characters')),
            },
        };
        const endpoint = creating ? 'api/system/connections/google/connections' : `api/system/connections/google/connections/${encodeURIComponent(id)}`;
        const saved = await mutateGoogle(endpoint, { method: creating ? 'POST' : 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }, 'Google settings saved.', false);
        const clientSecret = String(values.get('client_secret') || '').trim();
        if (saved && clientSecret) {
            const savedId = creating ? saved.connection_id : id;
            state.googleDraft = false;
            await mutateGoogle(`api/system/connections/google/connections/${encodeURIComponent(savedId)}/client-secret`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_secret: clientSecret }) }, 'Google settings and client secret saved.');
        } else if (saved) {
            state.googleDraft = false;
            await loadGoogleConnection();
            await notifyConfigChanged();
        }
    }

    async function handleGoogleConnectionAction(event) {
        const copyButton = event.target instanceof Element ? event.target.closest('[data-google-copy]') : null;
        if (copyButton instanceof HTMLButtonElement) {
            const form = copyButton.closest('form[data-google-id]');
            const field = form?.elements.namedItem(copyButton.dataset.googleCopy || '');
            await actions.copyConnectionField(copyButton, field);
            return;
        }
        const button = event.target instanceof Element ? event.target.closest('[data-google-action]') : null;
        const form = button?.closest('form[data-google-id]');
        if (!(button instanceof HTMLButtonElement) || !(form instanceof HTMLFormElement) || state.isSavingGoogle) return;
        const id = form.dataset.googleId;
        const action = button.dataset.googleAction;
        if (action === 'authorize') {
            const savedConnection = state.googleConnections.find((item) => item.connection_id === id);
            const draftEnabled = form.elements.namedItem('draft_creation_enabled')?.checked ?? false;
            if (draftEnabled !== (savedConnection?.gmail?.draft_creation_enabled ?? false)) {
                setStatus(elements.connectionsFeedback, 'Save the Gmail capability changes before authorizing Google.', 'warning');
                return;
            }
            await startGoogleOAuth(form, id);
        } else if (action === 'complete') {
            const redirect = form.elements.namedItem('oauth_redirect')?.value || '';
            if (!redirect.trim()) {
                setStatus(form.querySelector('[id="google-connection-feedback"]') || elements.connectionsFeedback, 'Paste the full redirected URL first.', 'error');
                return;
            }
            cancelGoogleOAuthPoll(id);
            await mutateGoogle(`api/system/connections/google/connections/${encodeURIComponent(id)}/oauth/complete`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ redirect_url: redirect, code: null, state: null }) }, 'Google account connected.');
        } else if (action === 'disconnect') {
            if (window.confirm('Disconnect the authorized Google account? Client settings will be preserved.')) {
                cancelGoogleOAuthPoll(id);
                await mutateGoogle(`api/system/connections/google/connections/${encodeURIComponent(id)}/oauth`, { method: 'DELETE' }, 'Google account disconnected.');
            }
        } else if (action === 'delete') {
            if (window.confirm('Remove the Google connection, client secret, and authorized account?')) {
                cancelGoogleOAuthPoll(id);
                const replacement = state.googleConnections.find((item) => item.connection_id !== id)?.connection_id;
                const query = replacement ? `?replacement_default_id=${encodeURIComponent(replacement)}` : '';
                await mutateGoogle(`api/system/connections/google/connections/${encodeURIComponent(id)}${query}`, { method: 'DELETE' }, 'Google connection removed.');
            }
        } else if (action === 'cancel') {
            state.googleDraft = false;
            renderGoogleConnection();
        }
    }

    async function startGoogleOAuth(form, id) {
        cancelGoogleOAuthPoll(id);
        state.isSavingGoogle = true;
        setStatus(elements.connectionsFeedback, 'Starting Google authorization…');
        try {
            const response = await fetch(`api/system/connections/google/connections/${encodeURIComponent(id)}/oauth/start`, { method: 'POST' });
            const payload = await safeJson(response);
            if (!response.ok) throw new Error(payload?.message || `HTTP ${response.status}`);
            const field = form.elements.namedItem('authorization_url');
            if (field instanceof HTMLTextAreaElement) field.value = payload.authorization_url;
            const popup = window.open(payload.authorization_url, '_blank', 'noopener,noreferrer');
            setStatus(elements.connectionsFeedback, popup ? 'Finish authorization in the new tab. The URL is also available below.' : 'The browser blocked the new tab. Copy the authorization URL below.', popup ? 'success' : 'warning');
            startGoogleOAuthPoll(id);
        } catch (error) {
            setStatus(elements.connectionsFeedback, error.message, 'error');
        } finally {
            state.isSavingGoogle = false;
        }
    }

    function createOAuthPoll(owners, id, durationMs) {
        const owner = {
            controller: new AbortController(),
            timerId: null,
            deadline: Date.now() + durationMs,
            generation: Symbol(id),
            timerResolve: null,
        };
        owners.set(id, owner);
        return owner;
    }

    function ownsOAuthPoll(owners, id, owner) {
        const current = owners.get(id);
        return current?.generation === owner.generation && !owner.controller.signal.aborted;
    }

    function cancelOAuthPoll(owners, id) {
        const owner = owners.get(id);
        if (!owner) return;
        owners.delete(id);
        owner.controller.abort();
        if (owner.timerId !== null) window.clearTimeout(owner.timerId);
        owner.timerResolve?.(false);
        owner.timerResolve = null;
    }

    function finishOAuthPoll(owners, id, owner) {
        if (!ownsOAuthPoll(owners, id, owner)) return false;
        owners.delete(id);
        if (owner.timerId !== null) window.clearTimeout(owner.timerId);
        owner.timerResolve?.(false);
        owner.timerResolve = null;
        return true;
    }

    function waitForOAuthPoll(owners, id, owner, delayMs) {
        return new Promise((resolve) => {
            if (!ownsOAuthPoll(owners, id, owner)) {
                resolve(false);
                return;
            }
            owner.timerId = window.setTimeout(() => {
                owner.timerId = null;
                owner.timerResolve = null;
                resolve(ownsOAuthPoll(owners, id, owner));
            }, delayMs);
            owner.timerResolve = resolve;
        });
    }

    function cancelGoogleOAuthPoll(id) {
        cancelOAuthPoll(resources.googleOAuthPolls, id);
    }

    function cancelMcpOAuthPoll(id) {
        cancelOAuthPoll(resources.mcpOAuthPolls, id);
    }

    function cancelAllOAuthPolls() {
        [...resources.googleOAuthPolls.keys()].forEach(cancelGoogleOAuthPoll);
        [...resources.mcpOAuthPolls.keys()].forEach(cancelMcpOAuthPoll);
        if (timers.mcpOAuthStatusRequest) {
            timers.mcpOAuthStatusRequest.controller.abort();
            timers.mcpOAuthStatusRequest = null;
        }
    }

    function startGoogleOAuthPoll(id) {
        cancelGoogleOAuthPoll(id);
        const owner = createOAuthPoll(resources.googleOAuthPolls, id, 10 * 60 * 1000);
        void pollGoogleConnection(id, owner);
    }

    async function pollGoogleConnection(id, owner) {
        try {
            while (ownsOAuthPoll(resources.googleOAuthPolls, id, owner)) {
                if (Date.now() >= owner.deadline) {
                    if (finishOAuthPoll(resources.googleOAuthPolls, id, owner)) {
                        setStatus(elements.connectionsFeedback, 'Google authorization expired. Start a new attempt.', 'warning');
                    }
                    return;
                }
                if (!await waitForOAuthPoll(resources.googleOAuthPolls, id, owner, 1500)) return;
                const response = await fetch('api/system/connections/google/connections', {
                    cache: 'no-store',
                    signal: owner.controller.signal,
                });
                const payload = await safeJson(response);
                if (!ownsOAuthPoll(resources.googleOAuthPolls, id, owner)) return;
                if (!response.ok) throw new Error(payload?.message || `HTTP ${response.status}`);
                state.googleConnections = Array.isArray(payload) ? payload : [];
                renderGoogleConnection();
                if (state.googleConnections.find((connection) => connection.connection_id === id)?.connected) {
                    if (!finishOAuthPoll(resources.googleOAuthPolls, id, owner)) return;
                    setStatus(elements.connectionsFeedback, 'Google account connected.', 'success');
                    await notifyConfigChanged();
                    return;
                }
            }
        } catch (error) {
            if (error.name === 'AbortError' || !ownsOAuthPoll(resources.googleOAuthPolls, id, owner)) return;
            if (await waitForOAuthPoll(resources.googleOAuthPolls, id, owner, 1500)) void pollGoogleConnection(id, owner);
        }
    }

    async function mutateGoogle(url, options, successMessage, reload = true) {
        state.isSavingGoogle = true;
        setStatus(elements.connectionsFeedback, 'Saving…');
        try {
            const response = await fetch(url, options);
            const payload = await safeJson(response);
            if (!response.ok) throw new Error(payload?.message || `HTTP ${response.status}`);
            setStatus(elements.connectionsFeedback, successMessage, 'success');
            if (reload) await loadGoogleConnection();
            await notifyConfigChanged();
            return payload;
        } catch (error) {
            setStatus(elements.connectionsFeedback, error.message, 'error');
            return false;
        } finally {
            state.isSavingGoogle = false;
        }
    }

    Object.assign(actions, {
        loadSecrets,
        loadGoogleConnection,
        renderGoogleConnection,
        appendGoogleConnectionCard,
        startGoogleConnectionDraft,
        googleDraftRedirectUri,
        saveGoogleConnection,
        handleGoogleConnectionAction,
        startGoogleOAuth,
        createOAuthPoll,
        ownsOAuthPoll,
        cancelOAuthPoll,
        finishOAuthPoll,
        waitForOAuthPoll,
        cancelGoogleOAuthPoll,
        cancelMcpOAuthPoll,
        cancelAllOAuthPolls,
        startGoogleOAuthPoll,
        pollGoogleConnection,
        mutateGoogle
    });
}(window, document));
