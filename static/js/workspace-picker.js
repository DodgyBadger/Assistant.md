(function workspacePickerModule(window) {
    function createWorkspacePickerController({ state, elements, callbacks }) {
        let workspaceChangeGeneration = 0;
        elements.workspacePathInput?.addEventListener('input', () => {
            // A newer manual draft owns the input even before its next save.
            workspaceChangeGeneration += 1;
        });

        function beginWorkspaceChange() {
            const generation = ++workspaceChangeGeneration;
            const vault = elements.vaultSelector?.value || '';
            const sessionId = state.sessionId || '';
            return {
                vault,
                sessionId,
                isCurrent: () => generation === workspaceChangeGeneration
                    && state.sessionId === (sessionId || null)
                    && elements.vaultSelector?.value === vault,
            };
        }
        function syncControls() {
            const input = elements.workspacePathInput;
            if (!input) return;

            const hasSession = Boolean(state.sessionId);
            const hasWorkspace = Boolean(input.value.trim());
            const locked = state.isLoading || (hasSession && hasWorkspace);

            input.disabled = locked;
            input.title = locked
                ? 'Use the folder button to change this session workspace.'
                : '';

            if (elements.workspacePickerBtn) {
                const workspacePath = input.value.trim();
                const workspaceMissing = hasWorkspace && state.workspaceExists === false;
                elements.workspacePickerBtn.disabled = state.isLoading;
                elements.workspacePickerBtn.classList.toggle('has-workspace', hasWorkspace);
                elements.workspacePickerBtn.classList.toggle(
                    'has-missing-workspace',
                    workspaceMissing
                );
                elements.workspacePickerBtn.title = workspaceMissing
                    ? `Choose replacement workspace\nWorkspace folder not found: ${workspacePath}`
                    : hasWorkspace
                        ? `Change workspace folder\nWorkspace: ${workspacePath}`
                        : 'Choose workspace folder';
                elements.workspacePickerBtn.setAttribute(
                    'aria-label',
                    workspaceMissing
                        ? `Choose replacement for missing workspace ${workspacePath}`
                        : hasWorkspace
                            ? `Change workspace from ${workspacePath}`
                            : 'Choose workspace folder'
                );
            }
            if (elements.workspaceClearBtn) {
                elements.workspaceClearBtn.classList.toggle('hidden', !hasWorkspace);
                elements.workspaceClearBtn.disabled = state.isLoading;
            }
            callbacks.syncExplorerButtons?.();
        }

        function currentPath() {
            return (elements.workspacePathInput?.value || '').trim();
        }

        function savePath() {
            return persistPath(beginWorkspaceChange());
        }

        async function persistPath({ vault, sessionId, isCurrent }) {
            const input = elements.workspacePathInput;
            if (!input || !vault || !sessionId || state.isLoading) return false;

            const path = currentPath();
            try {
                const response = await fetch(`api/chat/sessions/${encodeURIComponent(sessionId)}/workspace`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ vault_name: vault, path }),
                });
                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.message || `HTTP ${response.status}`);
                }
                const payload = await response.json().catch(() => null);
                if (!isCurrent()) return false;
                input.value = payload?.path || '';
                state.workspaceExists = payload?.path
                    ? payload.exists === true
                    : null;
                await callbacks.fetchSessions(vault, sessionId);
                return isCurrent();
            } catch (error) {
                if (isCurrent()) {
                    console.error('Failed to save workspace path:', error);
                    callbacks.addChatErrorMessage(`Workspace not saved: ${error.message}`);
                }
                return false;
            } finally {
                if (isCurrent()) syncControls();
            }
        }

        async function setPath(path) {
            if (!elements.workspacePathInput) return;
            const previousPath = currentPath();
            const previousExists = state.workspaceExists;
            elements.workspacePathInput.value = path || '';
            elements.workspacePathInput.dispatchEvent(new Event('input', { bubbles: true }));
            const change = beginWorkspaceChange();
            state.workspaceExists = path ? true : null;
            syncControls();
            if (!change.sessionId) return change.isCurrent();
            const saved = await persistPath(change);
            if (!saved && change.isCurrent()) {
                elements.workspacePathInput.value = previousPath;
                state.workspaceExists = previousExists;
                syncControls();
            }
            return saved && change.isCurrent();
        }

        async function clearPath(event) {
            event?.preventDefault();
            event?.stopPropagation();
            if (!elements.workspacePathInput || state.isLoading) return;
            const confirmed = window.confirm(
                'Clear the workspace for this session? Future turns will no longer use workspace-specific context.'
            );
            if (!confirmed) return;
            await setPath('');
        }

        function openModal() {
            if (!elements.workspacePathInput) return;
            const vault = elements.vaultSelector?.value || '';
            if (!vault) {
                alert('Select a vault before choosing a workspace.');
                return;
            }
            const path = currentPath();
            const workspaceMissing = Boolean(path) && state.workspaceExists === false;
            if (
                Boolean(state.sessionId)
                && path
                && !workspaceMissing
                && !window.confirm(
                    'Change the workspace for this session? Future turns will use the new workspace path.'
                )
            ) {
                return;
            }
            callbacks.openVaultExplorer?.({
                revealPath: workspaceMissing ? '' : path,
                subtitle: vault,
                workspaceSelection: true,
            });
        }

        function closeModal() {
            callbacks.closePathPicker?.();
        }

        return Object.freeze({
            syncControls,
            currentPath,
            savePath,
            setPath,
            clearPath,
            openModal,
            closeModal,
        });
    }

    window.WorkspacePicker = Object.freeze({
        create: createWorkspacePickerController,
    });
})(window);
