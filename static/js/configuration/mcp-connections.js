(function configurationMcpConnectionsFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, constants, elements, helpers, resources, state, timers } = runtime;
    const { ACTIVITY_LOG_LEVELS, BUILT_IN_PROVIDER_NAMES, DEFAULT_IMPORT_JOB_STATUSES, SECRET_METADATA } = constants;
    const { escapeHtml, formatDateTime, iconButton, iconSvg, notifyConfigChanged, safeJson, setIconButtonLabel, setStatus, withRestartNotice } = helpers;

    async function loadMcpConnections() {
        if (!elements.mcpConnectionsList || state.isLoadingMcp) return;
        state.isLoadingMcp = true;
        try {
            const [response, statusResponse] = await Promise.all([
                fetch('api/system/mcp/connections', { cache: 'no-store' }),
                fetch('api/status', { cache: 'no-store' }),
            ]);
            const payload = await safeJson(response);
            if (!response.ok) throw new Error(payload?.message || `HTTP ${response.status}`);
            const statusPayload = await safeJson(statusResponse);
            state.mcpAdvancedMode = statusResponse.ok && statusPayload?.advanced_shell?.execution_mode === 'advanced';
            state.mcpAdvancedShellReady = state.mcpAdvancedMode && statusPayload?.advanced_shell?.readiness_state === 'ready';
            const stdioOption = elements.mcpCreateForm?.querySelector('option[value="advanced_shell_stdio"]');
            if (stdioOption instanceof HTMLOptionElement) {
                stdioOption.disabled = !state.mcpAdvancedMode;
                stdioOption.textContent = !state.mcpAdvancedMode
                    ? 'Advanced-shell stdio (requires advanced mode)'
                    : state.mcpAdvancedShellReady
                        ? 'Advanced-shell stdio'
                        : 'Advanced-shell stdio (shell currently unavailable)';
            }
            state.mcpConnections = Array.isArray(payload) ? payload : [];
            renderMcpConnections();
            void loadMcpOAuthStatuses();
        } catch (error) {
            elements.mcpConnectionsList.innerHTML = `<div class="rounded-lg border state-surface-error px-4 py-3 text-sm text-center shadow-sm">Failed to load MCP connections: ${escapeHtml(error.message)}</div>`;
        } finally {
            state.isLoadingMcp = false;
        }
    }

    function renderMcpConnections() {
        if (!elements.mcpConnectionsList) return;
        if (!state.mcpConnections.length) {
            elements.mcpConnectionsList.innerHTML = '';
            return;
        }
        elements.mcpConnectionsList.innerHTML = state.mcpConnections.map((connection) => {
            const allowedTools = Array.isArray(connection.allowed_tools) ? connection.allowed_tools.join(', ') : '';
            const isStdio = connection.transport === 'advanced_shell_stdio';
            const staticAuth = connection.auth_mode === 'bearer' || connection.auth_mode === 'header';
            const oauthAuth = connection.auth_mode === 'oauth';
            const browserCallbackUrl = new URL(`api/system/mcp/connections/${encodeURIComponent(connection.connection_id)}/oauth/callback`, window.location.href).href;
            const oauthCallbackUrl = connection.oauth_redirect_uri || browserCallbackUrl;
            const oauthOriginMismatch = connection.oauth_redirect_source === 'configured' && new URL(oauthCallbackUrl).origin !== window.location.origin;
            return `
                <details class="rounded-lg border border-border-primary bg-app-card shadow-sm" data-mcp-id="${escapeHtml(connection.connection_id)}">
                    <summary class="collapsible-summary connection-card-summary"><div class="summary-text"><span class="summary-title">${escapeHtml(connection.display_name)}</span><span class="connection-card-status-icon ${connection.enabled ? 'state-success' : 'text-txt-secondary'}" title="${connection.enabled ? 'Enabled' : 'Disabled'}">${iconSvg(connection.enabled ? 'check' : 'x')}</span></div><svg class="chevron" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M6 8l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg></summary>
                    <div class="p-4 pt-0 space-y-3">
                    <div class="text-xs font-medium text-txt-secondary">MCP connection</div>
                    <div class="grid gap-3 md:grid-cols-2">
                        <label class="text-xs text-txt-secondary">Display name<input data-mcp-field="display_name" value="${escapeHtml(connection.display_name)}" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card text-txt-primary" /></label>
                        ${isStdio ? `
                        <label class="text-xs text-txt-secondary">Advanced-shell executable<input data-mcp-field="stdio_executable" value="${escapeHtml(connection.stdio?.executable || '')}" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card font-mono text-xs text-txt-primary" /></label>
                        <label class="text-xs text-txt-secondary">Working directory<input data-mcp-field="stdio_working_directory" value="${escapeHtml(connection.stdio?.working_directory || '')}" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card font-mono text-xs text-txt-primary" /></label>
                        <label class="text-xs text-txt-secondary">Arguments (one per line)<textarea data-mcp-field="stdio_arguments" rows="4" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card font-mono text-xs text-txt-primary">${escapeHtml(Array.isArray(connection.stdio?.arguments) ? connection.stdio.arguments.join('\n') : '')}</textarea></label>
                        <label class="text-xs text-txt-secondary">MCP Roots (one per line)<textarea data-mcp-field="stdio_roots" rows="4" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card font-mono text-xs text-txt-primary">${escapeHtml(Array.isArray(connection.stdio?.roots) ? connection.stdio.roots.join('\n') : '')}</textarea></label>
                        <label class="text-xs text-txt-secondary md:col-span-2">Non-secret environment<textarea data-mcp-field="stdio_environment" rows="4" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card font-mono text-xs text-txt-primary">${escapeHtml(Object.entries(connection.stdio?.environment || {}).map(([name, value]) => `${name}=${value}`).join('\n'))}</textarea></label>
                        ` : `
                        <label class="text-xs text-txt-secondary">Server URL<input data-mcp-field="url" value="${escapeHtml(connection.url || '')}" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card text-txt-primary" /></label>
                        <label class="text-xs text-txt-secondary">Authentication<select data-mcp-field="auth_mode" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card text-txt-primary"><option value="none" ${connection.auth_mode === 'none' ? 'selected' : ''}>None</option><option value="bearer" ${connection.auth_mode === 'bearer' ? 'selected' : ''}>Bearer token</option><option value="header" ${connection.auth_mode === 'header' ? 'selected' : ''}>Custom header</option><option value="oauth" ${connection.auth_mode === 'oauth' ? 'selected' : ''}>OAuth</option></select></label>
                        <label class="text-xs text-txt-secondary">Header name<input data-mcp-field="header_name" value="${escapeHtml(connection.header_name || '')}" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card text-txt-primary" placeholder="X-API-Key" /></label>`}
                        <input data-mcp-field="transport" type="hidden" value="${escapeHtml(connection.transport)}" />
                        <label class="text-xs text-txt-secondary">Allowed tools<input data-mcp-field="allowed_tools" value="${escapeHtml(allowedTools)}" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card text-txt-primary" placeholder="Blank trusts all tools" /></label>
                    </div>
                    <div class="space-y-2">
                        <label class="block text-sm text-txt-primary"><input data-mcp-field="enabled" type="checkbox" ${connection.enabled ? 'checked' : ''} class="mr-2" />Enabled</label>
                        ${isStdio ? '<p class="text-xs text-txt-secondary">Runs through the deployment\'s fixed advanced shell.</p>' : `<label class="block text-sm text-txt-primary"><input data-mcp-field="allow_private_http" type="checkbox" ${connection.allow_private_http ? 'checked' : ''} class="mr-2" />Allow HTTP on a private network</label><p class="text-xs text-txt-secondary">HTTP traffic, including credentials, is not encrypted. Public HTTP addresses are always blocked.</p>`}
                    </div>
                    ${staticAuth ? `<div class="rounded-md border border-border-primary p-3 space-y-2">
                        <div class="text-xs text-txt-secondary">Credential: ${connection.credential_present ? 'stored' : 'not set'}</div>
                        <div class="flex items-center gap-2"><input data-mcp-field="credential" type="password" ${staticAuth ? '' : 'disabled'} class="flex-1 px-3 py-2 border border-border-secondary rounded-md bg-app-card text-txt-primary" placeholder="New credential" autocomplete="new-password" /><button type="button" data-mcp-action="credential" ${iconButton('save', 'Save MCP credential', 'is-primary', staticAuth ? '' : 'disabled')}>${iconSvg('save')}</button><button type="button" data-mcp-action="clear-credential" ${iconButton('x', 'Clear MCP credential', 'is-danger', connection.credential_present ? '' : 'disabled')}>${iconSvg('x')}</button></div>
                    </div>` : ''}
                    ${oauthAuth ? `<div class="rounded-md border border-border-primary p-3 space-y-3">
                        <div class="grid gap-3 md:grid-cols-2">
                            <label class="text-xs text-txt-secondary">OAuth client ID<input data-mcp-field="oauth_client_id" value="${escapeHtml(connection.oauth_client_id || '')}" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card text-txt-primary" placeholder="Blank uses dynamic registration" /></label>
                            <label class="text-xs text-txt-secondary">OAuth client secret (${connection.oauth_client_secret_present ? 'stored' : 'not set'})<input data-mcp-field="oauth_client_secret" type="password" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card text-txt-primary" placeholder="Leave blank to preserve" autocomplete="new-password" /></label>
                            <label class="text-xs text-txt-secondary md:col-span-2">OAuth scopes<input data-mcp-field="oauth_scopes" value="${escapeHtml(Array.isArray(connection.oauth_scopes) ? connection.oauth_scopes.join(', ') : '')}" class="mt-1 w-full px-3 py-2 border border-border-secondary rounded-md bg-app-card text-txt-primary" placeholder="Blank uses server metadata" /></label>
                            <label class="text-xs text-txt-secondary md:col-span-2">Authorized redirect URI (${connection.oauth_redirect_source === 'configured' ? 'configured' : 'browser fallback'})<span class="mt-1 flex items-start gap-2"><input data-mcp-field="oauth_callback_uri" readonly value="${escapeHtml(oauthCallbackUrl)}" class="min-w-0 flex-1 px-3 py-2 border border-border-secondary rounded-md bg-app-card font-mono text-xs text-txt-primary" /><button type="button" data-mcp-copy="oauth_callback_uri" ${iconButton('copy', 'Copy authorized redirect URI')}>${iconSvg('copy')}</button></span></label>
                            ${oauthOriginMismatch ? `<div class="md:col-span-2 text-xs state-warning">This browser is using ${escapeHtml(window.location.origin)}, but OAuth callbacks use the configured origin ${escapeHtml(new URL(oauthCallbackUrl).origin)}.</div>` : ''}
                        </div>
                        <div class="flex flex-wrap items-center justify-between gap-3">
                            <div data-mcp-oauth-status class="text-sm text-txt-secondary">OAuth status: loading…</div>
                            <div class="flex items-center gap-2">
                                <button type="button" data-mcp-action="oauth-connect" class="px-3 py-2 rounded-md bg-accent text-white text-xs font-medium hover:bg-accent-hover">Authorize</button>
                                <button type="button" data-mcp-action="oauth-disconnect" disabled class="px-3 py-2 rounded-md border border-border-secondary bg-app-card text-xs font-medium state-error hover:border-border-secondary disabled:opacity-50 disabled:cursor-not-allowed">Disconnect</button>
                            </div>
                        </div>
                        <p class="text-xs text-txt-secondary">Save any client ID, client secret, or scope changes before choosing Authorize. Servers that support dynamic registration can leave these fields blank.</p>
                        <p class="text-xs text-txt-secondary">Authorize opens the server's sign-in page. Assistant.md detects the callback automatically when this address is reachable from your browser.</p>
                        <label class="text-xs text-txt-secondary">Authorization URL<span class="mt-1 flex items-start gap-2"><textarea data-mcp-field="oauth_authorization_url" readonly rows="3" class="min-w-0 flex-1 px-3 py-2 border border-border-secondary rounded-md bg-app-card font-mono text-xs text-txt-primary resize-y" placeholder="Choose Authorize to generate a URL you can copy into another browser."></textarea><button type="button" data-mcp-copy="oauth_authorization_url" ${iconButton('copy', 'Copy authorization URL')}>${iconSvg('copy')}</button></span></label>
                        <div class="space-y-2">
                            <p class="text-xs text-txt-secondary">Only use this if the browser cannot reach Assistant.md's callback. Copy the full redirected URL from the browser address bar.</p>
                            <div class="flex flex-col gap-2 sm:flex-row"><input data-mcp-field="oauth_redirect" class="flex-1 px-3 py-2 border border-border-secondary rounded-md bg-app-card text-txt-primary" placeholder="Paste the full redirected URL" /><button type="button" data-mcp-action="oauth-complete" class="shrink-0 px-3 py-2 rounded-md border border-border-secondary bg-app-card text-xs font-medium text-txt-primary hover:border-accent">Finish from redirected URL</button></div>
                        </div>
                    </div>` : ''}
                    <div data-mcp-test-result class="text-sm text-txt-secondary"></div>
                    <div class="flex justify-end gap-2"><button type="button" data-mcp-action="test" ${iconButton('play', 'Test MCP connection')}>${iconSvg('play')}</button><button type="button" data-mcp-action="delete" ${iconButton('trash', 'Delete MCP connection', 'is-danger')}>${iconSvg('trash')}</button><button type="button" data-mcp-action="save" ${iconButton('save', 'Save MCP connection', 'is-primary')}>${iconSvg('save')}</button></div>
                    </div>
                </details>`;
        }).join('');
    }

    function parseMcpAllowedTools(value) {
        const tools = String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
        return tools.length ? [...new Set(tools)] : null;
    }

    async function copyConnectionField(button, field) {
        const value = field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement
            ? field.value.trim()
            : '';
        const copied = value ? await window.AssistantMDUtils.handleCopy(value) : false;
        window.AssistantMDUtils.flashCopyFeedback(button, copied);
    }

    function updateMcpCreateAuthFields() {
        if (!(elements.mcpCreateForm instanceof HTMLFormElement)) return;
        const transport = elements.mcpCreateForm.elements.namedItem('transport')?.value || 'streamable_http';
        const isStdio = transport === 'advanced_shell_stdio';
        const submitButton = elements.mcpCreateForm.querySelector('button[type="submit"]');
        if (submitButton instanceof HTMLButtonElement) {
            setIconButtonLabel(submitButton, isStdio ? 'Test and add advanced-shell stdio connection' : 'Add MCP connection');
        }
        elements.mcpCreateForm.querySelectorAll('[data-mcp-create-http]').forEach((element) => element.classList.toggle('hidden', isStdio));
        elements.mcpCreateForm.querySelectorAll('[data-mcp-create-stdio]').forEach((element) => element.classList.toggle('hidden', !isStdio));
        const urlInput = elements.mcpCreateForm.elements.namedItem('url');
        if (urlInput instanceof HTMLInputElement) {
            urlInput.required = !isStdio;
            urlInput.disabled = isStdio;
        }
        const authMode = elements.mcpCreateForm.elements.namedItem('auth_mode')?.value || 'none';
        const headerInput = elements.mcpCreateForm.elements.namedItem('header_name');
        const credentialInput = elements.mcpCreateForm.elements.namedItem('credential');
        const oauthInputs = ['oauth_client_id', 'oauth_client_secret', 'oauth_scopes'].map((name) => elements.mcpCreateForm.elements.namedItem(name));
        if (headerInput instanceof HTMLInputElement) {
            headerInput.disabled = isStdio || authMode !== 'header';
            if (headerInput.disabled) headerInput.value = '';
        }
        if (credentialInput instanceof HTMLInputElement) {
            credentialInput.disabled = isStdio || (authMode !== 'bearer' && authMode !== 'header');
            if (credentialInput.disabled) credentialInput.value = '';
        }
        oauthInputs.forEach((input) => {
            if (input instanceof HTMLInputElement) {
                input.disabled = isStdio || authMode !== 'oauth';
                if (input.disabled) input.value = '';
            }
        });
    }

    function parseMcpLines(value) {
        return String(value || '').split('\n').map((item) => item.trim()).filter(Boolean);
    }

    function parseMcpEnvironment(value) {
        const environment = {};
        for (const line of parseMcpLines(value)) {
            const separator = line.indexOf('=');
            if (separator < 1) throw new Error(`Invalid environment entry: ${line}`);
            environment[line.slice(0, separator).trim()] = line.slice(separator + 1);
        }
        return environment;
    }

    async function parseMcpImport() {
        if (!(elements.mcpCreateForm instanceof HTMLFormElement)) return;
        const configuration = elements.mcpCreateForm.elements.namedItem('import_configuration')?.value || '';
        if (!configuration.trim()) {
            setStatus(elements.mcpFeedback, 'Paste advanced-shell stdio YAML or JSON first.', 'error');
            return;
        }
        try {
            const response = await fetch('api/system/mcp/connections/import/parse', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ configuration }),
            });
            const payload = await safeJson(response);
            if (!response.ok) throw new Error(payload?.message || `HTTP ${response.status}`);
            const setValue = (name, value) => {
                const field = elements.mcpCreateForm.elements.namedItem(name);
                if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement) field.value = value;
            };
            setValue('display_name', payload.display_name || '');
            setValue('transport', payload.transport || 'advanced_shell_stdio');
            setValue('allowed_tools', Array.isArray(payload.allowed_tools) ? payload.allowed_tools.join(', ') : '');
            setValue('stdio_executable', payload.stdio?.executable || '');
            setValue('stdio_working_directory', payload.stdio?.working_directory || '/workspace');
            setValue('stdio_arguments', Array.isArray(payload.stdio?.arguments) ? payload.stdio.arguments.join('\n') : '');
            setValue('stdio_roots', Array.isArray(payload.stdio?.roots) ? payload.stdio.roots.join('\n') : '');
            setValue('stdio_environment', Object.entries(payload.stdio?.environment || {}).map(([name, value]) => `${name}=${value}`).join('\n'));
            const enabled = elements.mcpCreateForm.elements.namedItem('enabled');
            if (enabled instanceof HTMLInputElement) enabled.checked = payload.enabled !== false;
            updateMcpCreateAuthFields();
            setStatus(elements.mcpFeedback, 'Configuration imported. Review the fields, then add the connection.', 'success');
        } catch (error) {
            setStatus(elements.mcpFeedback, error.message, 'error');
        }
    }

    async function loadMcpOAuthStatuses() {
        if (timers.mcpOAuthStatusRequest) timers.mcpOAuthStatusRequest.controller.abort();
        const request = { controller: new AbortController(), generation: Symbol('mcp-status') };
        timers.mcpOAuthStatusRequest = request;
        const oauthConnections = state.mcpConnections.filter((connection) => connection.auth_mode === 'oauth');
        await Promise.all(oauthConnections.map(async (connection) => {
            try {
                const response = await fetch(`api/system/mcp/connections/${encodeURIComponent(connection.connection_id)}/oauth/status`, { cache: 'no-store', signal: request.controller.signal });
                const payload = await safeJson(response);
                if (timers.mcpOAuthStatusRequest !== request || request.controller.signal.aborted) return;
                if (!response.ok) throw new Error(payload?.message || `HTTP ${response.status}`);
                state.mcpOAuthStatuses[connection.connection_id] = payload;
                const card = elements.mcpConnectionsList?.querySelector(`[data-mcp-id="${CSS.escape(connection.connection_id)}"]`);
                const statusElement = card?.querySelector('[data-mcp-oauth-status]');
                if (statusElement) statusElement.textContent = `OAuth status: ${payload.status}`;
                const authorizeButton = card?.querySelector('[data-mcp-action="oauth-connect"]');
                const disconnectButton = card?.querySelector('[data-mcp-action="oauth-disconnect"]');
                if (authorizeButton instanceof HTMLButtonElement) authorizeButton.textContent = payload.connected ? 'Reauthorize' : (payload.status === 'pending' ? 'Restart authorization' : 'Authorize');
                if (disconnectButton instanceof HTMLButtonElement) disconnectButton.disabled = !payload.connected && payload.status !== 'pending';
            } catch (error) {
                if (error.name === 'AbortError' || timers.mcpOAuthStatusRequest !== request) return;
                const card = elements.mcpConnectionsList?.querySelector(`[data-mcp-id="${CSS.escape(connection.connection_id)}"]`);
                const statusElement = card?.querySelector('[data-mcp-oauth-status]');
                if (statusElement) statusElement.textContent = `OAuth status unavailable: ${error.message}`;
            }
        }));
        if (timers.mcpOAuthStatusRequest === request) timers.mcpOAuthStatusRequest = null;
    }

    async function handleMcpCreate(event) {
        event.preventDefault();
        if (state.isSavingMcp || !(elements.mcpCreateForm instanceof HTMLFormElement)) return;
        const form = new FormData(elements.mcpCreateForm);
        const transport = String(form.get('transport') || 'streamable_http');
        const isStdio = transport === 'advanced_shell_stdio';
        let stdio = null;
        try {
            if (isStdio) {
                stdio = {
                    executable: String(form.get('stdio_executable') || ''),
                    working_directory: String(form.get('stdio_working_directory') || ''),
                    arguments: parseMcpLines(form.get('stdio_arguments')),
                    environment: parseMcpEnvironment(form.get('stdio_environment')),
                    roots: parseMcpLines(form.get('stdio_roots')),
                };
            }
        } catch (error) {
            setStatus(elements.mcpFeedback, error.message, 'error');
            return;
        }
        const payload = {
            display_name: String(form.get('display_name') || ''),
            url: isStdio ? null : String(form.get('url') || ''),
            transport,
            auth_mode: isStdio ? 'none' : String(form.get('auth_mode') || 'none'),
            header_name: String(form.get('auth_mode') || 'none') === 'header' ? (String(form.get('header_name') || '').trim() || null) : null,
            enabled: form.get('enabled') === 'on',
            allow_private_http: !isStdio && form.get('allow_private_http') === 'on',
            allowed_tools: parseMcpAllowedTools(form.get('allowed_tools')),
            credential: ['bearer', 'header'].includes(String(form.get('auth_mode') || 'none')) ? (String(form.get('credential') || '').trim() || null) : null,
            oauth_client_id: String(form.get('auth_mode') || 'none') === 'oauth' ? (String(form.get('oauth_client_id') || '').trim() || null) : null,
            oauth_client_secret: String(form.get('auth_mode') || 'none') === 'oauth' ? (String(form.get('oauth_client_secret') || '').trim() || null) : null,
            oauth_scopes: String(form.get('auth_mode') || 'none') === 'oauth' ? parseMcpAllowedTools(form.get('oauth_scopes')) : null,
            stdio,
        };
        const enableAfterTest = isStdio && payload.enabled;
        if (isStdio) payload.enabled = false;
        const saved = await mutateMcp('api/system/mcp/connections', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }, 'MCP connection added.');
        if (saved && isStdio && saved.connection_id) {
            try {
                setStatus(elements.mcpFeedback, 'Testing advanced-shell stdio connection…');
                const testResponse = await fetch(`api/system/mcp/connections/${encodeURIComponent(saved.connection_id)}/test`, { method: 'POST' });
                const testResult = await safeJson(testResponse);
                if (!testResponse.ok || !testResult?.ready) throw new Error(testResult?.message || `HTTP ${testResponse.status}`);
                if (enableAfterTest) {
                    const { credential: _credential, oauth_client_secret: _oauthClientSecret, ...updatePayload } = payload;
                    await mutateMcp(`api/system/mcp/connections/${encodeURIComponent(saved.connection_id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...updatePayload, enabled: true }) }, `Advanced-shell stdio connection tested and enabled with ${testResult.tool_count} tool(s).`);
                } else {
                    setStatus(elements.mcpFeedback, `Advanced-shell stdio connection tested successfully with ${testResult.tool_count} tool(s) and remains disabled.`, 'success');
                }
            } catch (error) {
                setStatus(elements.mcpFeedback, `Connection was saved disabled because its test failed: ${error.message}`, 'error');
            }
        }
        if (saved) {
            elements.mcpCreateForm.reset();
            elements.mcpCreateForm.classList.add('hidden');
            updateMcpCreateAuthFields();
        }
    }

    async function handleMcpConnectionAction(event) {
        const copyButton = event.target instanceof Element ? event.target.closest('[data-mcp-copy]') : null;
        if (copyButton instanceof HTMLButtonElement) {
            const card = copyButton.closest('[data-mcp-id]');
            const field = card?.querySelector(`[data-mcp-field="${CSS.escape(copyButton.dataset.mcpCopy || '')}"]`);
            await copyConnectionField(copyButton, field);
            return;
        }
        const button = event.target instanceof Element ? event.target.closest('[data-mcp-action]') : null;
        const card = button?.closest('[data-mcp-id]');
        if (!(button instanceof HTMLButtonElement) || !(card instanceof HTMLElement) || state.isSavingMcp) return;
        const id = card.dataset.mcpId;
        if (!id) return;
        const action = button.dataset.mcpAction;
        const endpoint = `api/system/mcp/connections/${encodeURIComponent(id)}`;
        if (action === 'oauth-connect') {
            await startMcpOAuth(card, endpoint);
            return;
        }
        if (action === 'oauth-complete') {
            const redirectUrl = card.querySelector('[data-mcp-field="oauth_redirect"]')?.value || '';
            if (!redirectUrl.trim()) {
                setStatus(elements.mcpFeedback, 'Paste the full redirected URL before using the headless fallback.', 'error');
                return;
            }
            actions.cancelMcpOAuthPoll(id);
            await mutateMcp(`${endpoint}/oauth/complete`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ redirect_url: redirectUrl, code: null, state: null }) }, 'MCP OAuth connected.');
            return;
        }
        if (action === 'oauth-disconnect') {
            actions.cancelMcpOAuthPoll(id);
            await mutateMcp(`${endpoint}/oauth`, { method: 'DELETE' }, 'MCP OAuth disconnected.');
            return;
        }
        if (action === 'test') {
            await testMcpConnection(card, endpoint, button);
            return;
        }
        if (action === 'delete') {
            if (!window.confirm('Delete this MCP connection and its stored credential?')) return;
            actions.cancelMcpOAuthPoll(id);
            await mutateMcp(endpoint, { method: 'DELETE' }, 'MCP connection deleted.');
            return;
        }
        if (action === 'clear-credential') {
            actions.cancelMcpOAuthPoll(id);
            await mutateMcp(`${endpoint}/credential`, { method: 'DELETE' }, 'MCP credential cleared.');
            return;
        }
        if (action === 'credential') {
            actions.cancelMcpOAuthPoll(id);
            const credential = card.querySelector('[data-mcp-field="credential"]')?.value || '';
            await mutateMcp(`${endpoint}/credential`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ credential }) }, 'MCP credential saved.');
            return;
        }
        if (action === 'save') {
            actions.cancelMcpOAuthPoll(id);
            const value = (name) => card.querySelector(`[data-mcp-field="${name}"]`)?.value || '';
            const enabled = card.querySelector('[data-mcp-field="enabled"]')?.checked === true;
            const allowPrivateHttp = card.querySelector('[data-mcp-field="allow_private_http"]')?.checked === true;
            const authMode = value('auth_mode');
            const transport = value('transport');
            const isStdio = transport === 'advanced_shell_stdio';
            let stdio = null;
            try {
                if (isStdio) {
                    stdio = {
                        executable: value('stdio_executable'),
                        working_directory: value('stdio_working_directory'),
                        arguments: parseMcpLines(value('stdio_arguments')),
                        environment: parseMcpEnvironment(value('stdio_environment')),
                        roots: parseMcpLines(value('stdio_roots')),
                    };
                }
            } catch (error) {
                setStatus(elements.mcpFeedback, error.message, 'error');
                return;
            }
            const payload = { display_name: value('display_name'), url: isStdio ? null : value('url'), transport, auth_mode: isStdio ? 'none' : authMode, header_name: !isStdio && authMode === 'header' ? (value('header_name').trim() || null) : null, enabled, allow_private_http: !isStdio && allowPrivateHttp, allowed_tools: parseMcpAllowedTools(value('allowed_tools')), oauth_client_id: !isStdio && authMode === 'oauth' ? (value('oauth_client_id').trim() || null) : null, oauth_scopes: !isStdio && authMode === 'oauth' ? parseMcpAllowedTools(value('oauth_scopes')) : null, stdio };
            const saved = await mutateMcp(endpoint, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }, 'MCP connection saved.');
            const clientSecret = value('oauth_client_secret').trim();
            if (saved && authMode === 'oauth' && clientSecret) {
                await mutateMcp(`${endpoint}/oauth/client-secret`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_secret: clientSecret }) }, 'MCP OAuth client settings saved.');
            }
        }
    }

    async function mutateMcp(url, options, successMessage) {
        state.isSavingMcp = true;
        setStatus(elements.mcpFeedback, 'Saving…');
        try {
            const response = await fetch(url, options);
            const payload = await safeJson(response);
            if (!response.ok) throw new Error(payload?.message || `HTTP ${response.status}`);
            setStatus(elements.mcpFeedback, successMessage, 'success');
            await loadMcpConnections();
            await notifyConfigChanged();
            return payload;
        } catch (error) {
            setStatus(elements.mcpFeedback, error.message, 'error');
            return false;
        } finally {
            state.isSavingMcp = false;
        }
    }

    async function startMcpOAuth(card, endpoint) {
        const id = card.dataset.mcpId;
        if (!id) return;
        actions.cancelMcpOAuthPoll(id);
        state.isSavingMcp = true;
        setStatus(elements.mcpFeedback, 'Starting OAuth…');
        try {
            const callbackField = card.querySelector('[data-mcp-field="oauth_callback_uri"]');
            const redirectUri = callbackField instanceof HTMLInputElement
                ? callbackField.value
                : new URL(`${endpoint}/oauth/callback`, window.location.href).href;
            const response = await fetch(`${endpoint}/oauth/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ redirect_uri: redirectUri }),
            });
            const payload = await safeJson(response);
            if (!response.ok) throw new Error(payload?.message || `HTTP ${response.status}`);
            if (callbackField instanceof HTMLInputElement) callbackField.value = payload.redirect_uri;
            const authorizationUrl = card.querySelector('[data-mcp-field="oauth_authorization_url"]');
            if (authorizationUrl instanceof HTMLTextAreaElement) authorizationUrl.value = payload.auth_url;
            const popup = window.open(payload.auth_url, '_blank', 'noopener,noreferrer');
            const statusElement = card.querySelector('[data-mcp-oauth-status]');
            if (statusElement) statusElement.textContent = 'OAuth status: pending';
            setStatus(elements.mcpFeedback, popup ? 'Finish authorization in the new tab, or copy the authorization URL into an external browser.' : 'The browser blocked the authorization tab. Copy the authorization URL into an external browser.', popup ? 'success' : 'error');
            const owner = actions.createOAuthPoll(resources.mcpOAuthPolls, id, 10 * 60 * 1000);
            void pollMcpOAuthStatus(id, endpoint, owner);
        } catch (error) {
            setStatus(elements.mcpFeedback, error.message, 'error');
        } finally {
            state.isSavingMcp = false;
        }
    }

    async function pollMcpOAuthStatus(id, endpoint, owner) {
        while (actions.ownsOAuthPoll(resources.mcpOAuthPolls, id, owner)) {
            if (Date.now() >= owner.deadline) {
                if (actions.finishOAuthPoll(resources.mcpOAuthPolls, id, owner)) {
                    setStatus(elements.mcpFeedback, 'MCP OAuth authorization expired. Start a new attempt.', 'warning');
                }
                return;
            }
            try {
                const response = await fetch(`${endpoint}/oauth/status`, { cache: 'no-store', signal: owner.controller.signal });
                const payload = await safeJson(response);
                if (!actions.ownsOAuthPoll(resources.mcpOAuthPolls, id, owner)) return;
                if (response.ok && (payload?.connected || payload?.status === 'failed' || payload?.status === 'expired')) {
                    if (!actions.finishOAuthPoll(resources.mcpOAuthPolls, id, owner)) return;
                    const connected = payload.connected === true;
                    setStatus(elements.mcpFeedback, connected ? 'MCP OAuth connected.' : `MCP OAuth authorization ${payload.status}. Start a new attempt.`, connected ? 'success' : 'error');
                    await loadMcpConnections();
                    return;
                }
            } catch (error) {
                if (error.name === 'AbortError' || !actions.ownsOAuthPoll(resources.mcpOAuthPolls, id, owner)) return;
                // A transient status failure should not cancel the browser flow.
            }
            if (!await actions.waitForOAuthPoll(resources.mcpOAuthPolls, id, owner, 2000)) return;
        }
    }

    async function testMcpConnection(card, endpoint, button) {
        if (state.isTestingMcp) return;
        const resultElement = card.querySelector('[data-mcp-test-result]');
        state.isTestingMcp = true;
        button.disabled = true;
        if (resultElement) {
            resultElement.className = 'text-sm text-txt-secondary';
            resultElement.textContent = 'Testing connection and discovering tools…';
        }
        try {
            const response = await fetch(`${endpoint}/test`, { method: 'POST' });
            const payload = await safeJson(response);
            if (!response.ok) throw new Error(payload?.message || `HTTP ${response.status}`);
            const names = Array.isArray(payload?.tool_names) ? payload.tool_names : [];
            const namesMarkup = names.length
                ? `<div class="mt-1 font-mono text-xs break-words">${names.map((name) => escapeHtml(name)).join(', ')}</div>`
                : '';
            if (resultElement) {
                resultElement.className = `text-sm ${payload.ready ? 'state-success' : 'state-warning'}`;
                resultElement.innerHTML = `${escapeHtml(payload.message || 'Connection test finished.')}${namesMarkup}`;
            }
        } catch (error) {
            if (resultElement) {
                resultElement.className = 'text-sm state-error';
                resultElement.textContent = error.message;
            }
        } finally {
            state.isTestingMcp = false;
            button.disabled = false;
        }
    }

    Object.assign(actions, {
        loadMcpConnections,
        renderMcpConnections,
        parseMcpAllowedTools,
        copyConnectionField,
        updateMcpCreateAuthFields,
        parseMcpLines,
        parseMcpEnvironment,
        parseMcpImport,
        loadMcpOAuthStatuses,
        handleMcpCreate,
        handleMcpConnectionAction,
        mutateMcp,
        startMcpOAuth,
        pollMcpOAuthStatus,
        testMcpConnection
    });
}(window, document));
