(function systemStatusModule(window) {
    function createSystemStatus({ state, chatElements, dashElements, configElements, dashboardView, callbacks }) {
        async function fetchSystemStatus() {
            try {
                const response = await fetch('api/status', { cache: 'no-store' });
                if (!response.ok) throw new Error('Failed to fetch status');

                state.systemStatus = await response.json();
                const publicUrl = state.systemStatus?.system?.public_url || '';
                if (configElements.publicUrl) {
                    configElements.publicUrl.textContent = publicUrl || 'Not configured';
                }
                const advancedShell = state.systemStatus?.advanced_shell;
                const advancedMode = advancedShell?.execution_mode === 'advanced';
                if (configElements.advancedShellExecutionMode) {
                    configElements.advancedShellExecutionMode.textContent = advancedShell?.execution_mode || 'Unavailable';
                }
                if (configElements.advancedShellHost) {
                    configElements.advancedShellHost.textContent = advancedShell?.host || 'Unavailable';
                }
                if (configElements.advancedShellPort) {
                    configElements.advancedShellPort.textContent = advancedShell?.port ?? 'Unavailable';
                }
                if (configElements.advancedShellUser) {
                    configElements.advancedShellUser.textContent = advancedShell?.user || 'Unavailable';
                }
                configElements.advancedShellCoordinates.forEach((element) => {
                    element.classList.toggle('opacity-50', !advancedMode);
                    element.setAttribute('aria-disabled', advancedMode ? 'false' : 'true');
                });
                configElements.advancedShellRestrictedNote?.classList.toggle('hidden', advancedMode);
                if (configElements.advancedShellReadiness) {
                    configElements.advancedShellReadiness.textContent = advancedShell?.readiness_message || 'Unavailable';
                }

                const envDefaultModel = state.systemStatus?.configuration_status?.default_model || null;
                if (envDefaultModel && state.metadata && chatElements.modelSelector) {
                    const availableModels = state.metadata.models
                        .filter(callbacks.isChatSelectableModel)
                        .filter(model => model.available !== false);
                    const firstAvailableModel = availableModels.length ? availableModels[0].name : null;
                    const currentValue = chatElements.modelSelector.value;
                    const hasEnvDefault = availableModels.some(model => model.name === envDefaultModel);
                    if (
                        !state.modelSelectionTouched
                        && hasEnvDefault
                        && (!currentValue || currentValue === firstAvailableModel)
                    ) {
                        chatElements.modelSelector.value = envDefaultModel;
                    }
                }
                callbacks.syncRestartFlag();
                await fetchExecutionTasks({ render: false });
                display();
                callbacks.updateStatus();
                callbacks.refreshChatEmptyState();
            } catch (error) {
                console.error('Error fetching status:', error);
                dashElements.systemStatus.innerHTML = '<p class="state-error text-sm">Failed to fetch system status</p>';
                if (dashElements.workflowsStatus) {
                    dashElements.workflowsStatus.innerHTML = '<p class="state-error text-sm">Failed to fetch workflow status</p>';
                }
                if (dashElements.vaultActivityStatus) {
                    dashElements.vaultActivityStatus.innerHTML = '<p class="state-error text-sm">Failed to fetch Assistant.md activity</p>';
                }
            }
        }

        async function fetchExecutionTasks({ render = true } = {}) {
            try {
                const response = await fetch('api/tasks?include_terminal=false', { cache: 'no-store' });
                if (!response.ok) throw new Error('Failed to fetch execution tasks');
                const data = await response.json();
                state.executionTasks = data.tasks || [];
                dashboardView.syncExecutionTaskPolling();
                if (render) display();
            } catch (error) {
                console.error('Error fetching execution tasks:', error);
                state.executionTasks = [];
                dashboardView.syncExecutionTaskPolling();
                if (render && dashElements.executeWorkflowResult) {
                    dashElements.executeWorkflowResult.innerHTML = `<p class="state-error">❌ Error: ${error.message}</p>`;
                }
            }
        }

        function display() {
            dashboardView.displaySystemStatus();
        }

        return Object.freeze({
            fetchSystemStatus,
            fetchExecutionTasks,
            display,
        });
    }

    window.SystemStatus = Object.freeze({ create: createSystemStatus });
})(window);
