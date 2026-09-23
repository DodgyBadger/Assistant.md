const {
    SESSION_SUMMARY_ICON_SVG,
} = window.AssistantMDIcons;

const {
    truncateText,
    formatShortDate,
} = window.AssistantMDUtils;

const browserStorage = window.AssistantMDBrowserStorage || Object.freeze({
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
});

// State management

const state = {
    sessionId: null,
    sessions: [],
    metadata: null,
    isLoading: false,
    isCancellingChat: false,
    activeChatSessionId: null,
    activeChatTaskId: null,
    activeChatAbortController: null,
    systemStatus: null,
    sessionSummaryPreviewCache: {},
    sessionSummaryPreviewInFlight: {},
    vaultActivity: {},
    selectedActivityVault: '',
    dashboardVaultSort: { column: 'name', direction: 'asc' },
    dashboardWorkflowSort: { column: 'id', direction: 'asc' },
    executionTasks: [],
    executionTaskPollTimer: null,
    vaultActivitySort: { column: 'last_run', direction: 'desc' },
    vaultActivityMutationSort: { column: 'time', direction: 'desc' },
    restartRequired: false,
    shouldAutoScroll: true,
    compactionStatusRequestId: 0,
    isChatFocusMode: false,
    chatComposerResize: null,
    workspaceExists: null,
    pendingDeferredReview: null
};
const chatComposeState = {
    pendingAttachments: [],
    popoverOpen: false,
    toolMenuOpen: false
};

// DOM elements - Chat
const chatElements = {
    vaultSelector: document.getElementById('vault-selector'),
    workspacePathInput: document.getElementById('workspace-path-input'),
    workspacePickerBtn: document.getElementById('workspace-picker-btn'),
    workspaceClearBtn: document.getElementById('workspace-clear-btn'),
    modelSelector: document.getElementById('model-selector'),
    templateSelector: document.getElementById('template-selector'),
    chatModeSelector: document.getElementById('chat-mode-selector'),
    thinkingSelector: document.getElementById('thinking-selector'),
    newSessionTrigger: document.getElementById('new-session-trigger'),
    sessionBrowserTrigger: document.getElementById('session-browser-trigger'),
    chatMessages: document.getElementById('chat-messages'),
    chatInput: document.getElementById('chat-input'),
    attachBtn: document.getElementById('attach-btn'),
    fileReferenceBtn: document.getElementById('file-reference-btn'),
    focusExplorerBtn: document.getElementById('chat-focus-explorer-btn'),
    attachInput: document.getElementById('attach-input'),
    attachCountBadge: document.getElementById('attach-count-badge'),
    attachmentPopover: document.getElementById('chat-attachment-popover'),
    sendBtn: document.getElementById('send-btn'),
    focusToggleInline: document.getElementById('chat-focus-toggle-inline'),
    focusDivider: document.getElementById('chat-focus-divider'),
    composer: document.getElementById('chat-composer'),
    compactionTrack: document.getElementById('chat-compaction-track'),
    compactionFill: document.getElementById('chat-compaction-fill'),
};

// DOM elements - Dashboard
const dashElements = {
    systemStatus: document.getElementById('system-status'),
    executionTasksStatus: document.getElementById('dashboard-execution-tasks-status'),
    executionTaskResult: document.getElementById('dashboard-execution-task-result'),
    workflowsStatus: document.getElementById('dashboard-workflows-status'),
    workflowSchedulerBadge: document.getElementById('dashboard-workflows-scheduler-badge'),
    vaultActivityStatus: document.getElementById('dashboard-vault-activity-status'),
    rescanBtn: document.getElementById('rescan-btn'),
    rescanResult: document.getElementById('rescan-result'),
    executeWorkflowResult: document.getElementById('execute-workflow-result')
};

// DOM elements - Configuration
const configElements = {
    statusBanner: document.getElementById('config-status-banner'),
    statusMessages: document.getElementById('config-status-messages'),
    publicUrl: document.getElementById('configured-public-url'),
    advancedShellExecutionMode: document.getElementById('advanced-shell-execution-mode'),
    advancedShellHost: document.getElementById('advanced-shell-host'),
    advancedShellPort: document.getElementById('advanced-shell-port'),
    advancedShellUser: document.getElementById('advanced-shell-user'),
    advancedShellCoordinates: document.querySelectorAll('[data-advanced-shell-coordinate]'),
    advancedShellRestrictedNote: document.getElementById('advanced-shell-restricted-note'),
    advancedShellReadiness: document.getElementById('advanced-shell-readiness'),
    configTab: document.getElementById('configuration-tab')
};

const configurationStatus = window.ConfigurationStatus.create({
    state,
    elements: configElements,
    browserStorage,
    icons: window.AssistantMDIcons,
    utils: window.AssistantMDUtils,
    callbacks: {
        refreshStatus: () => fetchSystemStatus(),
    },
});

function updateStatus() {
    configurationStatus.update();
}

function setRestartRequired(required = true) {
    configurationStatus.setRestartRequired(required);
}

function syncRestartFlagWithStorage() {
    configurationStatus.syncRestartFlagWithStorage();
}

const chatComposer = window.ChatComposer.create({
    state,
    composeState: chatComposeState,
    elements: chatElements,
    utils: window.AssistantMDUtils,
    browserStorage,
});
const {
    addPendingAttachments,
    clearPendingAttachments,
    createClientSessionId,
    handleChatFocusDividerKeydown,
    handleChatFocusDividerPointerDown,
    handleChatFocusDividerPointerMove,
    handleChatScroll,
    openAttachmentPicker,
    renderPendingAttachments,
    restoreChatComposerHeight,
    scrollChatToBottom,
    setAttachmentPopoverOpen,
    setChatFocusMode,
    stopChatFocusDividerResize,
    syncChatFocusToggle,
    toggleChatFocusMode,
} = chatComposer;

let sessionControls;

const sessionSummary = window.SessionSummary.create({
    state,
    elements: chatElements,
    icons: window.AssistantMDIcons,
    utils: window.AssistantMDUtils,
    callbacks: {
        renderSessionSelector: () => sessionControls.renderSelector(),
        fetchSessions,
    },
});

const vaultPathPicker = window.VaultPathPicker.create({
    elements: chatElements,
    icons: window.AssistantMDIcons,
    utils: window.AssistantMDUtils,
});

const workspacePicker = window.WorkspacePicker.create({
    state,
    elements: chatElements,
    utils: window.AssistantMDUtils,
    callbacks: {
        fetchSessions,
        addChatErrorMessage,
        closePathPicker: () => vaultPathPicker.close(),
        openVaultExplorer: (options) => fileReferences.openExplorer(options),
        syncExplorerButtons: syncVaultExplorerButtons,
    },
});

const fileReferences = window.FileReferences.create({
    state,
    elements: chatElements,
    icons: window.AssistantMDIcons,
    utils: window.AssistantMDUtils,
    callbacks: {
        isInteractionLocked: vaultMutationInteractionLocked,
        addChatErrorMessage,
        openPathPicker: (options) => vaultPathPicker.open(options),
        closePathPicker: () => vaultPathPicker.close(),
        syncPathPickerLocks: () => vaultPathPicker.syncInteractionLocks(),
        setWorkspace: (path) => workspacePicker.setPath(path),
        renderMarkdownPreview: (container, content) => chatRendering.renderMarkdownPreview(
            container,
            content,
            { softBreaks: true }
        ),
    },
});

const editProposals = window.EditProposals.create({
    state,
    elements: chatElements,
    icons: window.AssistantMDIcons,
    utils: window.AssistantMDUtils,
    callbacks: {
        openFile: (path) => fileReferences.openFile(path),
        openPathPicker: (options) => vaultPathPicker.open(options),
        enhanceFileLinks: (container) => fileReferences.enhanceFileLinks(container),
    },
});

const deferredReviews = window.DeferredReviews.create({
    state,
    elements: chatElements,
    icons: window.AssistantMDIcons,
    utils: window.AssistantMDUtils,
    callbacks: {
        streamStartedTask: (started) => chatTaskActions.streamDeferredReviewTask(started),
        reviewSubmitted: () => {
            state.pendingDeferredReview = null;
            syncChatControlLocks();
        },
        openFile: (path) => fileReferences.openFile(path, {
            onBack: () => fileReferences.openExplorer({ revealPath: path }),
        }),
        openPathPicker: (options) => vaultPathPicker.open(options),
    },
});

sessionControls = window.SessionControls.create({
    state,
    elements: chatElements,
    icons: window.AssistantMDIcons,
    utils: window.AssistantMDUtils,
    sessionSummary,
    callbacks: {
        loadSession,
        clearSession,
        clearPendingAttachments,
        renderChatEmptyState,
        resetChatModeToDefault,
        syncChatControlLocks,
        updateStatus,
    },
});

const chatRendering = window.ChatRendering.create({
    state,
    elements: chatElements,
    icons: window.AssistantMDIcons,
    utils: window.AssistantMDUtils,
    callbacks: {
        scrollChatToBottom,
        fetchSessions,
        loadSession,
        openWorkspacePicker: () => workspacePicker.openModal(),
        openChatSettings: () => sessionControls.openSessionBrowserModal(),
        retryLatestFailure: (button) => chatTaskActions.retryLatestFailure(button),
        enhanceFileLinks: (container) => fileReferences.enhanceFileLinks(container),
        renderEditProposalArtifact: (container, artifactRef, options) => editProposals.renderArtifact(container, artifactRef, options),
    },
});

const chatTaskStream = window.ChatTaskStream.create({
    state,
    chatRendering,
    callbacks: {
        handleDeferredReviewEvent,
        syncChatControlLocks,
    },
});

const chatTaskActions = window.ChatTaskActions.create({
    state,
    composeState: chatComposeState,
    elements: chatElements,
    icons: window.AssistantMDIcons,
    chatRendering,
    chatTaskStream,
    sessionControls,
    workspacePicker,
    callbacks: {
        clearPendingAttachments,
        createClientSessionId,
        fetchSessions,
        loadSession,
        reconcileCommittedToolCalls,
        syncChatControlLocks,
    },
});

const chatSessions = window.ChatSessions.create({
    state,
    elements: chatElements,
    sessionControls,
    chatRendering,
    chatTaskActions,
    chatTaskStream,
    callbacks: {
        syncControls: syncChatControlLocks,
        handleDeferredReview: handleDeferredReviewEvent,
        updateStatus,
        addErrorMessage: addChatErrorMessage,
    },
});

const chatSelection = window.ChatSelection.create({
    state,
    elements: chatElements,
    sessionControls,
    callbacks: {
        fetchSessions,
        syncControls: syncChatControlLocks,
        renderEmptyState: renderChatEmptyState,
        updateStatus,
        addErrorMessage: addChatErrorMessage,
    },
});

const vaultActivity = window.VaultActivity.create({
    state,
    elements: dashElements,
    utils: window.AssistantMDUtils,
    callbacks: {
        isInteractionLocked: vaultMutationInteractionLocked,
        formatChatSessionLabel: (session) => sessionControls.formatOptionLabel(session),
        openFileRevision: ({ vaultName, path, snapshotId }) => fileReferences.openFile(path, {
            vaultName,
            initialMode: 'history',
            initialRevisionId: snapshotId,
            onBack: () => fileReferences.openExplorer({ vaultName, revealPath: path }),
        }),
    },
});

let dashboardView;

function vaultMutationInteractionLocked() {
    return Boolean(state.isLoading || state.pendingDeferredReview);
}

const workflowActions = window.WorkflowActions.create({
    state,
    elements: dashElements,
    utils: window.AssistantMDUtils,
    callbacks: {
        fetchMetadata,
        populateSelectors,
        fetchSystemStatus,
        fetchExecutionTasks,
        displaySystemStatus,
        isTerminalTaskStatus,
        selectedVault: () => chatElements.vaultSelector?.value || '',
    },
});

const executionTaskActions = window.ExecutionTaskActions.create({
    elements: dashElements,
    utils: window.AssistantMDUtils,
    callbacks: {
        fetchExecutionTasks,
        activeExecutionTasks: () => dashboardView.activeExecutionTasks(),
    },
});

dashboardView = window.DashboardView.create({
    state,
    elements: dashElements,
    utils: window.AssistantMDUtils,
    callbacks: {
        renderVaultActivityResult: vaultActivity.renderResult,
        loadVaultActivity: vaultActivity.loadActivity,
        fetchExecutionTasks,
        isTerminalTaskStatus,
        openWorkflowFileEditor: workflowActions.openFileEditor,
        openWorkflowRunHistory: workflowActions.openRunHistory,
        toggleWorkflowEnabled: workflowActions.toggleWorkflowEnabled,
        executeWorkflow: workflowActions.executeWorkflow,
        stopExecutionTask: executionTaskActions.stopExecutionTask,
        stopAllExecutionTasks: executionTaskActions.stopAllExecutionTasks,
    },
});

function renderChatEmptyState(message) {
    chatRendering.renderEmptyState(message);
}

function refreshChatEmptyState() {
    chatRendering.refreshEmptyState();
}

function addChatErrorMessage(errorText) {
    chatRendering.addErrorMessage(errorText);
}


function syncSendButtonState() {
    const btn = chatElements.sendBtn;
    if (!btn) return;

    const reviewPending = Boolean(state.pendingDeferredReview) && !state.isLoading;
    btn.classList.toggle('chat-stop-btn', state.isLoading);
    btn.innerHTML = state.isLoading
        ? window.AssistantMDIcons.STOP_ICON_SVG
        : window.AssistantMDIcons.SEND_HORIZONTAL_ICON_SVG;
    btn.title = reviewPending
        ? 'Tool review is pending. Resolve the review card before sending another message.'
        : state.isLoading
        ? (state.isCancellingChat ? 'Stopping active response' : 'Stop the active response')
        : 'Send message';
    btn.setAttribute(
        'aria-label',
        reviewPending
            ? 'Tool review is pending. Resolve the review card before sending another message.'
            : state.isLoading
            ? (state.isCancellingChat ? 'Stopping active response' : 'Stop the active response')
            : 'Send message'
    );
    btn.disabled = reviewPending || (state.isLoading && state.isCancellingChat);
    if (chatElements.chatInput) {
        chatElements.chatInput.disabled = reviewPending;
        chatElements.chatInput.title = reviewPending
            ? 'Tool review is pending. Resolve the review card before resuming normal message flow.'
            : '';
    }
}

function syncChatControlLocks() {
    if (!chatElements.vaultSelector) return;

    const lockVaultSelector = state.isLoading;
    chatElements.vaultSelector.disabled = lockVaultSelector;
    chatElements.vaultSelector.title = lockVaultSelector
        ? 'Vault is locked while a response is running.'
        : '';

    if (chatElements.thinkingSelector) {
        chatElements.thinkingSelector.disabled = state.isLoading;
    }
    if (chatElements.chatModeSelector) {
        chatElements.chatModeSelector.disabled = state.isLoading || Boolean(state.pendingDeferredReview);
    }
    if (chatElements.sessionBrowserTrigger) {
        chatElements.sessionBrowserTrigger.disabled = state.isLoading;
    }
    if (chatElements.newSessionTrigger) {
        chatElements.newSessionTrigger.disabled = state.isLoading;
    }
    workspacePicker.syncControls();
    fileReferences.syncInteractionLocks();
    vaultActivity.syncInteractionLocks();
    syncSendButtonState();
}

function syncVaultExplorerButtons() {
    const workspacePath = (chatElements.workspacePathInput?.value || '').trim();
    const workspaceMissing = Boolean(workspacePath) && state.workspaceExists === false;
    const explorerReadOnly = state.isLoading || Boolean(state.pendingDeferredReview);
    const baseTitle = explorerReadOnly
        ? 'Open vault explorer in read-only mode'
        : 'Open vault explorer';
    const title = workspaceMissing
        ? `${baseTitle}\nWorkspace folder not found: ${workspacePath}`
        : workspacePath
            ? `${baseTitle}\nWorkspace: ${workspacePath}`
            : baseTitle;
    const ariaLabel = workspaceMissing
        ? `${baseTitle} for missing workspace ${workspacePath}`
        : workspacePath
            ? `${baseTitle} for workspace ${workspacePath}`
            : baseTitle;

    [chatElements.fileReferenceBtn, chatElements.focusExplorerBtn].forEach((button) => {
        if (!button) return;
        button.disabled = false;
        button.classList.toggle('has-workspace', Boolean(workspacePath));
        button.classList.toggle('has-missing-workspace', workspaceMissing);
        button.title = title;
        button.setAttribute('aria-label', ariaLabel);
    });
}


function fetchMetadata() {
    return chatSelection.fetchMetadata();
}

function populateSelectors() {
    chatSelection.populateSelectors();
}

function resetChatModeToDefault() {
    chatSelection.resetChatModeToDefault();
}

function isChatSelectableModel(model) {
    return chatSelection.isChatSelectableModel(model);
}

function persistSelectedChatMode() {
    return chatSelection.persistSelectedChatMode();
}

function handleVaultChange() {
    chatSelection.handleVaultChange();
}

function fetchTemplates(vault, preferredTemplate = '') {
    return chatSelection.fetchTemplates(vault, preferredTemplate);
}

function fetchSessions(vault, preferredSessionId = '') {
    return chatSessions.fetchSessions(vault, preferredSessionId);
}

function loadSession(sessionId, options = {}) {
    return chatSessions.loadSession(sessionId, options);
}

function reconcileCommittedToolCalls(context, vault, sessionId) {
    return chatSessions.reconcileCommittedToolCalls(context, vault, sessionId);
}



// Tab management
const tabs = {
    chat: {
        button: document.getElementById('chat-tab'),
        content: document.getElementById('chat-content')
    },
    dashboard: {
        button: document.getElementById('dashboard-tab'),
        content: document.getElementById('dashboard-content')
    },
    configuration: {
        button: document.getElementById('configuration-tab'),
        content: document.getElementById('configuration-content')
    }
};

// Theme management
const themeManager = {
    themes: [
        { name: 'light', label: 'Light' },
        { name: 'dark', label: 'Dark' },
        { name: 'ocean', label: 'Ocean' },
        { name: 'sunset', label: 'Sunset' },
        { name: 'lavender', label: 'Lavender' },
        { name: 'forest', label: 'Forest' }
    ],

    current: null,

    safeGet(key) {
        return browserStorage.getItem(key);
    },

    safeSet(key, value) {
        browserStorage.setItem(key, value);
    },

    init() {
        const saved = this.safeGet('theme');
        let initialTheme = saved;

        if (!saved) {
            // No saved preference - check system preference
            const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            initialTheme = prefersDark ? 'dark' : 'light';
        }

        this.apply(initialTheme);

        // Set up click handler
        const button = document.getElementById('theme-toggle');
        if (button) {
            button.addEventListener('click', () => this.cycle());
        }
    },

    apply(themeName) {
        const theme = this.themes.find(t => t.name === themeName) || this.themes[0];
        this.current = theme;

        // Update DOM
        document.documentElement.setAttribute('data-theme', theme.name);

        // Update button title
        const button = document.getElementById('theme-toggle');
        if (button) {
            button.title = `Theme: ${theme.label} (click to change)`;
        }

        // Save preference (best-effort)
        this.safeSet('theme', theme.name);
    },

    cycle() {
        const currentIndex = this.themes.findIndex(t => t.name === this.current?.name);
        const nextIndex = (currentIndex + 1) % this.themes.length;
        this.apply(this.themes[nextIndex].name);
    }
};

// Initialize app
async function init() {
    window.AssistantMDIcons.hydrateIconButtons(document);
    themeManager.init();
    setupTabs();
    setupEventListeners();
    restoreChatComposerHeight();
    syncChatFocusToggle();
    if (window.ConfigurationPanel) {
        window.ConfigurationPanel.init({
            refreshMetadata: () => fetchMetadata(),
            refreshStatus: () => fetchSystemStatus(),
            openFile: (path, vaultName) => fileReferences.openFile(path, { vaultName })
        });
    }
    await fetchMetadata();
    await fetchSystemStatus();
    renderChatEmptyState();
    renderPendingAttachments();
    updateCollapsibleArrows();
}

// Setup tab switching
function setupTabs() {
    Object.entries(tabs).forEach(([name, tabControls]) => {
        if (tabControls.button) {
            tabControls.button.addEventListener('click', () => switchTab(name));
        } else {
            console.error(`Tab button not found for ${name}`, tabControls);
        }
    });
}

function switchTab(tabName) {
    if (tabName !== 'configuration') {
        window.ConfigurationPanel?.onTabDeactivated?.();
    }
    Object.entries(tabs).forEach(([name, tabControls]) => {
        if (!tabControls.button || !tabControls.content) return;

        const isActive = name === tabName;
        tabControls.button.classList.toggle('border-accent', isActive);
        tabControls.button.classList.toggle('text-accent', isActive);
        tabControls.button.classList.toggle('border-transparent', !isActive);
        tabControls.button.classList.toggle('text-txt-secondary', !isActive);
        tabControls.content.classList.toggle('hidden', !isActive);
    });

    if (tabName === 'dashboard') {
        fetchSystemStatus();
        if (window.ConfigurationPanel) {
            window.ConfigurationPanel.onDashboardActivated();
        }
    } else if (tabName === 'configuration') {
        fetchSystemStatus();
        if (window.ConfigurationPanel) {
            window.ConfigurationPanel.onTabActivated();
        }
    }
}

// Update collapsible section arrows (placeholder)
function updateCollapsibleArrows() {}

// Fetch metadata from API

// Fetch system status
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
        const envDefaultModel = state.systemStatus && state.systemStatus.configuration_status
            ? state.systemStatus.configuration_status.default_model
            : null;
        if (envDefaultModel && state.metadata && chatElements.modelSelector) {
            const availableModels = state.metadata.models
                .filter(isChatSelectableModel)
                .filter(m => m.available !== false);
            const firstAvailableModel = availableModels.length ? availableModels[0].name : null;
            const currentValue = chatElements.modelSelector.value;
            const hasEnvDefault = availableModels.some(m => m.name === envDefaultModel);
            if (hasEnvDefault && (!currentValue || currentValue === firstAvailableModel)) {
                chatElements.modelSelector.value = envDefaultModel;
            }
        }
        syncRestartFlagWithStorage();
        await fetchExecutionTasks({ render: false });
        displaySystemStatus();
        updateStatus();
        refreshChatEmptyState();
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
        if (render) {
            displaySystemStatus();
        }
    } catch (error) {
        console.error('Error fetching execution tasks:', error);
        state.executionTasks = [];
        dashboardView.syncExecutionTaskPolling();
        if (render && dashElements.executeWorkflowResult) {
            dashElements.executeWorkflowResult.innerHTML = `<p class="state-error">❌ Error: ${error.message}</p>`;
        }
    }
}

function displaySystemStatus() {
    dashboardView.displaySystemStatus();
}

document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
        if (state.isChatFocusMode) {
            setChatFocusMode(false);
            return;
        }
        workspacePicker.closeModal();
        vaultActivity.closeDetails();
    }
});

// Setup event listeners
function setupEventListeners() {
    if (chatElements.sendBtn) {
        chatElements.sendBtn.addEventListener('click', () => {
            if (state.isLoading) {
                chatTaskActions.stopChatResponse();
                return;
            }
            chatTaskActions.sendMessage();
        });
    }

    if (chatElements.chatInput) {
        chatElements.chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                chatTaskActions.sendMessage();
            }
        });
    }

    if (chatElements.focusToggleInline) {
        chatElements.focusToggleInline.addEventListener('click', toggleChatFocusMode);
    }

    if (chatElements.focusDivider) {
        chatElements.focusDivider.addEventListener('pointerdown', handleChatFocusDividerPointerDown);
        chatElements.focusDivider.addEventListener('pointermove', handleChatFocusDividerPointerMove);
        chatElements.focusDivider.addEventListener('pointerup', stopChatFocusDividerResize);
        chatElements.focusDivider.addEventListener('pointercancel', stopChatFocusDividerResize);
        chatElements.focusDivider.addEventListener('keydown', handleChatFocusDividerKeydown);
    }

    window.addEventListener('resize', () => {
        if (!state.isChatFocusMode) return;
        const current = chatElements.composer?.getBoundingClientRect().height;
        setChatComposerHeight(current, { persist: false });
    });

    sessionControls.attachEventListeners();

    if (chatElements.workspacePathInput) {
        chatElements.workspacePathInput.addEventListener('input', () => {
            state.workspaceExists = null;
            workspacePicker.syncControls();
            refreshChatEmptyState();
        });
        chatElements.workspacePathInput.addEventListener('blur', workspacePicker.savePath);
        chatElements.workspacePathInput.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                workspacePicker.savePath();
            }
        });
    }

    if (chatElements.modelSelector) {
        chatElements.modelSelector.addEventListener('change', refreshChatEmptyState);
    }

    if (chatElements.thinkingSelector) {
        chatElements.thinkingSelector.addEventListener('change', refreshChatEmptyState);
    }

    if (chatElements.workspacePickerBtn) {
        chatElements.workspacePickerBtn.addEventListener('click', workspacePicker.openModal);
    }

    if (chatElements.workspaceClearBtn) {
        chatElements.workspaceClearBtn.addEventListener('click', workspacePicker.clearPath);
    }

    [chatElements.fileReferenceBtn, chatElements.focusExplorerBtn].forEach((button) => {
        button?.addEventListener('click', () => fileReferences.openPicker());
    });

    if (chatElements.attachBtn && chatElements.attachInput) {
        chatElements.attachBtn.addEventListener('click', () => {
            if (chatComposeState.pendingAttachments.length > 0) {
                setAttachmentPopoverOpen(!chatComposeState.popoverOpen);
                return;
            }
            openAttachmentPicker();
        });
        chatElements.attachInput.addEventListener('change', (event) => {
            const input = event.target;
            addPendingAttachments(input?.files);
            if (input) {
                input.value = '';
            }
        });
        chatElements.attachBtn.addEventListener('mouseenter', () => {
            if (chatComposeState.pendingAttachments.length > 0) {
                setAttachmentPopoverOpen(true);
            }
        });
        chatElements.attachBtn.addEventListener('focus', () => {
            if (chatComposeState.pendingAttachments.length > 0) {
                setAttachmentPopoverOpen(true);
            }
        });
    }

    if (chatElements.attachmentPopover) {
        chatElements.attachmentPopover.addEventListener('click', (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;
            const idxRaw = target.getAttribute('data-attachment-remove');
            const addAction = target.getAttribute('data-attachment-add');
            if (addAction === 'true') {
                openAttachmentPicker();
                return;
            }
            if (idxRaw === null) return;
            const idx = Number.parseInt(idxRaw, 10);
            if (Number.isNaN(idx) || idx < 0 || idx >= chatComposeState.pendingAttachments.length) return;
            chatComposeState.pendingAttachments.splice(idx, 1);
            renderPendingAttachments();
            if (chatComposeState.pendingAttachments.length === 0) {
                setAttachmentPopoverOpen(false);
            }
        });
        chatElements.attachmentPopover.addEventListener('mouseenter', () => {
            if (chatComposeState.pendingAttachments.length > 0) {
                setAttachmentPopoverOpen(true);
            }
        });
        chatElements.attachmentPopover.addEventListener('mouseleave', () => {
            if (chatComposeState.popoverOpen) {
                setAttachmentPopoverOpen(false);
            }
        });
    }

    document.addEventListener('click', (event) => {
        const target = event.target;
        if (!(target instanceof Node)) return;

        if (chatComposeState.popoverOpen) {
            const clickedAttachBtn = chatElements.attachBtn && chatElements.attachBtn.contains(target);
            const clickedPopover = chatElements.attachmentPopover && chatElements.attachmentPopover.contains(target);
            if (!clickedAttachBtn && !clickedPopover) {
                setAttachmentPopoverOpen(false);
            }
        }

    });

    if (dashElements.rescanBtn) {
        dashElements.rescanBtn.addEventListener('click', workflowActions.rescanVaults);
    }

    dashboardView.attachEventListeners();
    vaultActivity.attachEventListeners();

    if (chatElements.chatMessages) {
        chatElements.chatMessages.addEventListener('scroll', handleChatScroll, { passive: true });
    }

    if (chatElements.vaultSelector) {
        chatElements.vaultSelector.addEventListener('change', handleVaultChange);
    }
    if (chatElements.chatModeSelector) {
        chatElements.chatModeSelector.addEventListener('change', persistSelectedChatMode);
    }
    syncChatControlLocks();
}


function handleDeferredReviewEvent(assistantMessage, payload) {
    if (!assistantMessage?.artifactList || !payload?.artifact_ref) return;
    state.pendingDeferredReview = payload;
    syncChatControlLocks();
    const alreadyRendered = Array.from(
        document.querySelectorAll('.message-artifact-item[data-review-artifact-ref]')
    ).some((item) => item.dataset.reviewArtifactRef === payload.artifact_ref);
    if (alreadyRendered) return;
    const container = document.createElement('div');
    container.className = 'message-artifact-item';
    container.dataset.reviewArtifactRef = payload.artifact_ref;
    assistantMessage.artifactList.appendChild(container);
    deferredReviews.renderReviewEvent(container, payload);
    scrollChatToBottom();
}


function addLoadingMessage() {
    return chatRendering.addLoadingMessage();
}

function removeLoadingMessage(messageDiv) {
    chatRendering.removeLoadingMessage(messageDiv);
}

function addMessage(role, content, options = {}) {
    chatRendering.addMessage(role, content, options);
}

function createAssistantStreamingMessage() {
    return chatRendering.createAssistantStreamingMessage();
}

function renderAssistantMarkdown(context, options = {}) {
    chatRendering.renderAssistantMarkdown(context, options);
}

function appendAssistantDelta(context, delta, options = {}) {
    chatRendering.appendAssistantDelta(context, delta, options);
}

function appendAssistantThinkingDelta(context, delta, options = {}) {
    chatRendering.appendAssistantThinkingDelta(context, delta, options);
}

function setAssistantStatus(context, label, state = 'thinking') {
    chatRendering.setAssistantStatus(context, label, state);
}

function handleToolEvent(context, payload) {
    chatRendering.handleToolEvent(context, payload);
}

function finalizeAssistantMessage(context, metadata) {
    chatRendering.finalizeAssistantMessage(context, metadata);
}


// Clear session

async function clearSession(confirmReset = true) {
    const confirmed = confirmReset
        ? window.confirm('Do you want to start a new chat session? The current session remains available in chat history unless you delete it.')
        : true;
    if (!confirmed) return;

    state.sessionId = null;
    state.pendingDeferredReview = null;
    state.workspaceExists = null;
    resetChatModeToDefault();
    if (chatElements.workspacePathInput) {
        chatElements.workspacePathInput.value = '';
    }
    sessionControls.clearCompactionProgress();
    clearPendingAttachments();
    renderChatEmptyState();
    sessionControls.renderSelector();
    sessionControls.updateTitleRow();
    syncChatControlLocks();
    updateStatus();
}

function isTerminalTaskStatus(status) {
    return ['completed', 'failed', 'cancelled', 'timed_out', 'skipped'].includes(
        String(status || '').toLowerCase()
    );
}


// Start app
window.App = window.App || {};
window.App.setRestartRequired = setRestartRequired;

init();
