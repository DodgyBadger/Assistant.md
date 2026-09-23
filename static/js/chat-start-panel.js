(function chatStartPanelModule(window) {
    function createChatStartPanelController({ elements, icons, utils, callbacks }) {
        let workspaceEditorOpen = false;

        function renderChatEmptyState() {
            callbacks.renderEmptyState();
        }

        function renderChatStartPanel(container) {
            const modelText = selectedOptionText(elements.modelSelector) || 'No model selected';
            const thinkingText = selectedOptionText(elements.thinkingSelector).replace(/^Thinking:\s*/i, '') || 'Default';
            const workspacePath = (elements.workspacePathInput?.value || '').trim();

            const panel = document.createElement('div');
            panel.className = 'chat-start-panel';
            panel.innerHTML = `
                <div class="chat-start-panel-title">Ready for a new chat</div>
                <div class="chat-start-panel-grid">
                    <div class="chat-start-panel-item">
                        <span class="chat-start-panel-label">Model</span>
                        <span class="chat-start-panel-value">${utils.escapeHtml(modelText)}</span>
                    </div>
                    <div class="chat-start-panel-item">
                        <span class="chat-start-panel-label">Thinking</span>
                        <span class="chat-start-panel-value">${utils.escapeHtml(thinkingText)}</span>
                    </div>
                    <button type="button" class="chat-start-settings-button" data-chat-start-settings="true" aria-label="Change chat settings" title="Change chat settings">
                        <span>Change settings</span>
                        ${icons.SETTINGS_ICON_SVG}
                    </button>
                </div>
                ${renderWorkspaceRow(workspacePath)}
            `;
            attachChatStartPanelEvents(panel);
            container.appendChild(panel);
        }

        function selectedOptionText(select) {
            if (!(select instanceof HTMLSelectElement)) return '';
            const option = select.selectedOptions && select.selectedOptions.length
                ? select.selectedOptions[0]
                : null;
            return (option?.textContent || select.value || '').trim();
        }

        function renderWorkspaceRow(workspacePath) {
            return `
                <div class="chat-start-workspace-block">
                    <div class="chat-start-workspace-editor">
                        <span class="chat-start-workspace-label">Workspace</span>
                        ${workspacePath
                            ? `<span class="chat-start-workspace-path">${utils.escapeHtml(workspacePath)}</span>`
                            : renderWorkspaceEntryControls()}
                    </div>
                    <p class="chat-start-workspace-help">A workspace is a folder in your vault. Setting this helps orient the chat agent. See <a href="https://github.com/DodgyBadger/Assistant.md/blob/main/docs/use/getting-the-most.md" target="_blank" rel="noopener noreferrer">Getting the Most from Assistant.md</a> for more info.</p>
                </div>
            `;
        }

        function renderWorkspaceEntryControls() {
            if (!workspaceEditorOpen) {
                return '<button type="button" class="chat-start-link-button" data-chat-start-workspace-open="true">Add workspace</button>';
            }
            return `
                <input
                    type="text"
                    class="chat-start-workspace-input"
                    placeholder="Workspace path..."
                    aria-label="Workspace path"
                    data-chat-start-workspace-input
                />
                <button type="button" class="chat-start-icon-button" data-chat-start-workspace-browse="true" aria-label="Choose workspace folder" title="Choose workspace folder">
                    ${icons.FOLDER_ICON_SVG}
                </button>
                <button type="button" class="chat-start-link-button" data-chat-start-workspace-apply="true">Apply</button>
                <button type="button" class="chat-start-link-button is-muted" data-chat-start-workspace-cancel="true">Cancel</button>
            `;
        }

        function attachChatStartPanelEvents(panel) {
            panel.addEventListener('click', (event) => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                if (target.closest('[data-chat-start-settings]')) {
                    callbacks.openChatSettings?.();
                    return;
                }
                if (target.closest('[data-chat-start-workspace-open]')) {
                    workspaceEditorOpen = true;
                    renderChatEmptyState();
                    return;
                }
                if (target.closest('[data-chat-start-workspace-cancel]')) {
                    workspaceEditorOpen = false;
                    renderChatEmptyState();
                    return;
                }
                if (target.closest('[data-chat-start-workspace-browse]')) {
                    callbacks.openWorkspacePicker?.();
                    return;
                }
                if (target.closest('[data-chat-start-workspace-apply]')) {
                    applyWorkspaceFromStartPanel(panel);
                }
            });
            panel.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter') return;
                const target = event.target;
                if (!(target instanceof HTMLInputElement) || !target.matches('[data-chat-start-workspace-input]')) {
                    return;
                }
                event.preventDefault();
                applyWorkspaceFromStartPanel(panel);
            });
        }

        function applyWorkspaceFromStartPanel(panel) {
            const input = panel.querySelector('[data-chat-start-workspace-input]');
            if (!(input instanceof HTMLInputElement)) return;
            const path = input.value.trim();
            if (!path || !elements.workspacePathInput) return;
            elements.workspacePathInput.value = path;
            elements.workspacePathInput.dispatchEvent(new Event('input', { bubbles: true }));
            workspaceEditorOpen = false;
            renderChatEmptyState();
        }

        return Object.freeze({ render: renderChatStartPanel });
    }

    window.ChatStartPanel = Object.freeze({ create: createChatStartPanelController });
})(window);
