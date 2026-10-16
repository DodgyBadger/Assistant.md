(function chatSelectionModule(window, document) {
    function createChatSelection({ state, elements, sessionControls, callbacks }) {
        let savingChatMode = false;
        function populateThinkingSelector() {
            if (!elements.thinkingSelector) return;
            const defaultThinking = state.metadata?.settings?.default_model_thinking || 'default';
            const allowed = new Set(Array.from(elements.thinkingSelector.options).map(option => option.value));
            elements.thinkingSelector.value = allowed.has(defaultThinking) ? defaultThinking : 'default';
        }

        async function fetchMetadata() {
            try {
                const response = await fetch('api/metadata');
                if (!response.ok) throw new Error('Failed to fetch metadata');

                state.metadata = await response.json();
                window.App = window.App || {};
                window.App.metadata = state.metadata;
                window.ConfigurationPanel?.onMetadataUpdated?.();
                populateSelectors();
                callbacks.updateStatus();
            } catch (error) {
                console.error('Error fetching metadata:', error);
                callbacks.updateStatus();
            }
        }

        function populateSelectors() {
            const previousVault = elements.vaultSelector?.value || '';
            const previousModel = elements.modelSelector?.value || '';
            const previousTemplate = elements.templateSelector?.value || '';

            elements.vaultSelector.innerHTML = '<option value="">Select vault...</option>';
            elements.modelSelector.innerHTML = '<option value="">Select model...</option>';
            if (elements.templateSelector) {
                elements.templateSelector.innerHTML = '<option value="">No context script</option>';
                elements.templateSelector.disabled = true;
            }
            populateThinkingSelector();
            if (!state.sessionId) resetChatModeToDefault();

            state.metadata.vaults.forEach((vault) => {
                const option = document.createElement('option');
                option.value = vault;
                option.textContent = vault;
                elements.vaultSelector.appendChild(option);
            });

            if (previousVault && state.metadata.vaults.includes(previousVault)) {
                elements.vaultSelector.value = previousVault;
            }

            let firstAvailableModel = null;
            const envDefaultModel = state.systemStatus?.configuration_status?.default_model || null;
            const chatModels = state.metadata.models.filter(isChatSelectableModel);

            chatModels.forEach((model) => {
                const option = document.createElement('option');
                option.value = model.name;
                const displayModelName = model.model_string || model.model || model.provider;
                option.textContent = `${model.name} (${displayModelName})${model.available ? '' : ' (unavailable)'}`;
                option.disabled = model.available === false;
                elements.modelSelector.appendChild(option);
                if (model.available && !firstAvailableModel) firstAvailableModel = model.name;
            });

            if (previousModel && chatModels.some(model => model.name === previousModel && model.available !== false)) {
                elements.modelSelector.value = previousModel;
            } else if (envDefaultModel && chatModels.some(model => model.name === envDefaultModel && model.available)) {
                elements.modelSelector.value = envDefaultModel;
            } else if (firstAvailableModel) {
                elements.modelSelector.value = firstAvailableModel;
            }

            if (elements.vaultSelector?.value) {
                fetchTemplates(elements.vaultSelector.value, previousTemplate);
                callbacks.fetchSessions(elements.vaultSelector.value, state.sessionId || '');
            }

            sessionControls.renderSelector();
            callbacks.syncControls();
        }

        function configuredDefaultChatMode() {
            return state.metadata?.settings?.default_chat_mode === 'inline_edit'
                ? 'inline_edit'
                : 'normal';
        }

        function resetChatModeToDefault() {
            if (elements.chatModeSelector) {
                elements.chatModeSelector.value = configuredDefaultChatMode();
            }
            syncFileApprovalToggle();
        }

        function syncFileApprovalToggle() {
            const enabled = elements.chatModeSelector?.value === 'inline_edit';
            const locked = state.isLoading || Boolean(state.pendingDeferredReview) || savingChatMode;
            if (elements.chatModeSelector) elements.chatModeSelector.disabled = locked;
            const toggle = elements.fileApprovalToggle;
            if (!toggle) return;
            toggle.textContent = `File approvals: ${enabled ? 'On' : 'Off'}`;
            toggle.setAttribute('aria-pressed', enabled ? 'true' : 'false');
            toggle.disabled = locked || !elements.vaultSelector?.value;
            toggle.title = savingChatMode ? 'Saving file approval mode…'
                : locked ? 'Available after the active response or review finishes.'
                : 'Toggle inline review of file changes for this chat session.';
        }

        async function toggleFileApprovals() {
            if (state.isLoading || state.pendingDeferredReview || savingChatMode
                || !elements.vaultSelector?.value || !elements.chatModeSelector) return;
            const previousMode = elements.chatModeSelector.value;
            elements.chatModeSelector.value = previousMode === 'inline_edit' ? 'normal' : 'inline_edit';
            return persistSelectedChatMode(previousMode);
        }

        function isChatSelectableModel(model) {
            return model?.chat_selectable === true;
        }

        async function persistSelectedChatMode(previousMode = null) {
            const vault = elements.vaultSelector?.value || '';
            const sessionId = state.sessionId;
            if (!elements.chatModeSelector || state.isLoading || state.pendingDeferredReview || savingChatMode) return;
            if (!vault || !sessionId) { syncFileApprovalToggle(); return; }
            const savedMode = previousMode || state.sessions.find(item => item.session_id === sessionId)?.chat_mode || configuredDefaultChatMode();
            const chatMode = elements.chatModeSelector.value === 'inline_edit' ? 'inline_edit' : 'normal';
            savingChatMode = true;
            callbacks.syncControls();
            try {
                const response = await fetch(`api/chat/sessions/${encodeURIComponent(sessionId)}/mode`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ vault_name: vault, chat_mode: chatMode }),
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const session = state.sessions.find(item => item.session_id === sessionId);
                if (session && elements.vaultSelector?.value === vault) session.chat_mode = chatMode;
            } catch (error) {
                if (elements.vaultSelector?.value === vault && state.sessionId === sessionId) {
                    elements.chatModeSelector.value = savedMode;
                }
                console.error('Failed to persist chat mode:', error);
                callbacks.addErrorMessage('Could not save the selected chat mode.');
            } finally {
                savingChatMode = false;
                callbacks.syncControls();
            }
        }

        function handleVaultChange() {
            const vault = elements.vaultSelector?.value || '';
            state.sessionId = null;
            state.pendingDeferredReview = null;
            state.workspaceExists = null;
            state.sessions = [];
            resetChatModeToDefault();
            if (elements.workspacePathInput) elements.workspacePathInput.value = '';
            sessionControls.clearCompactionProgress();
            sessionControls.renderSelector();
            sessionControls.updateTitleRow();
            callbacks.renderEmptyState();
            callbacks.updateStatus();
            populateTemplates([]);
            if (vault) {
                fetchTemplates(vault);
                callbacks.fetchSessions(vault);
            }
        }

        function populateTemplates(templates, preferredTemplate = '') {
            if (!elements.templateSelector) return;
            const templateList = Array.isArray(templates) ? templates : [];
            elements.templateSelector.innerHTML = '<option value="">No context script</option>';
            templateList.forEach((template) => {
                const option = document.createElement('option');
                option.value = template.name;
                option.textContent = `${template.name} (${template.source})`;
                elements.templateSelector.appendChild(option);
            });
            const candidates = [
                preferredTemplate,
                state.metadata?.default_context_script || '',
                'default.md'
            ].filter((value, index, values) => value && values.indexOf(value) === index);
            elements.templateSelector.value = candidates.find(candidate => (
                Array.from(elements.templateSelector.options).some(option => option.value === candidate)
            )) || '';
            elements.templateSelector.disabled = false;
        }

        async function fetchTemplates(vault, preferredTemplate = '') {
            if (!vault) {
                populateTemplates([], preferredTemplate);
                return;
            }
            try {
                const response = await fetch(`api/context/templates?vault_name=${encodeURIComponent(vault)}`);
                if (!response.ok) throw new Error('Failed to fetch templates');
                populateTemplates(await response.json(), preferredTemplate);
            } catch (error) {
                console.error('Error fetching templates:', error);
                populateTemplates([], preferredTemplate);
            }
        }

        return Object.freeze({
            fetchMetadata,
            populateSelectors,
            resetChatModeToDefault,
            isChatSelectableModel,
            persistSelectedChatMode,
            syncFileApprovalToggle,
            toggleFileApprovals,
            handleVaultChange,
            fetchTemplates,
        });
    }

    window.ChatSelection = Object.freeze({ create: createChatSelection });
})(window, document);
