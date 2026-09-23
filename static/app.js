const {
    SESSION_SUMMARY_ICON_SVG,
} = window.AssistantMDIcons;

const {
    escapeHtml,
    truncateText,
    formatShortDate,
} = window.AssistantMDUtils;

const browserStorage = window.AssistantMDBrowserStorage || Object.freeze({
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
});

// State management
const RESTART_NOTICE_TEXT = 'Restart the container to apply changes.';
const RESTART_STORAGE_KEY = 'assistantmd_restart_required';
const CHAT_COMPOSE_HEIGHT_STORAGE_KEY = 'assistantmd_chat_compose_height';
const CHAT_COMPOSE_DEFAULT_HEIGHT = 288;
const CHAT_COMPOSE_MIN_HEIGHT = 128;

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

function isChatNearBottom(element, threshold = 64) {
    if (!element) return true;
    const distance = element.scrollHeight - element.clientHeight - element.scrollTop;
    return distance <= threshold;
}

function scrollChatToBottom(force = false) {
    const container = chatElements.chatMessages;
    if (!container) return;

    if (force) {
        state.shouldAutoScroll = true;
    }

    if (force || state.shouldAutoScroll) {
        container.scrollTop = container.scrollHeight;
    }
}

function getViewportHeight() {
    return window.visualViewport?.height || window.innerHeight || document.documentElement.clientHeight || 800;
}

function getChatComposerMaxHeight() {
    return Math.max(CHAT_COMPOSE_MIN_HEIGHT, Math.floor(getViewportHeight() * 0.72));
}

function clampChatComposerHeight(value) {
    const parsed = Number(value);
    const fallback = Math.min(CHAT_COMPOSE_DEFAULT_HEIGHT, getChatComposerMaxHeight());
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(Math.max(parsed, CHAT_COMPOSE_MIN_HEIGHT), getChatComposerMaxHeight());
}

function persistChatComposerHeight(height) {
    browserStorage.setItem(CHAT_COMPOSE_HEIGHT_STORAGE_KEY, String(Math.round(height)));
}

function readStoredChatComposerHeight() {
    return browserStorage.getItem(CHAT_COMPOSE_HEIGHT_STORAGE_KEY);
}

function setChatComposerHeight(height, { persist = true } = {}) {
    const clamped = clampChatComposerHeight(height);
    document.documentElement.style.setProperty('--chat-compose-height', `${clamped}px`);

    if (chatElements.focusDivider) {
        chatElements.focusDivider.setAttribute('aria-valuemin', String(CHAT_COMPOSE_MIN_HEIGHT));
        chatElements.focusDivider.setAttribute('aria-valuemax', String(getChatComposerMaxHeight()));
        chatElements.focusDivider.setAttribute('aria-valuenow', String(Math.round(clamped)));
    }

    if (persist) {
        persistChatComposerHeight(clamped);
    }

    return clamped;
}

function restoreChatComposerHeight() {
    setChatComposerHeight(readStoredChatComposerHeight(), { persist: false });
}

function syncChatFocusToggle() {
    const toggle = chatElements.focusToggleInline;
    if (!toggle) return;

    toggle.setAttribute('aria-pressed', state.isChatFocusMode ? 'true' : 'false');
    toggle.title = state.isChatFocusMode ? 'Return to normal chat layout' : 'Focus the chat workspace';
    toggle.setAttribute(
        'aria-label',
        state.isChatFocusMode ? 'Exit chat focus mode' : 'Focus chat workspace'
    );
}

function setChatFocusMode(enabled) {
    const nextValue = Boolean(enabled);
    if (state.isChatFocusMode === nextValue) return;

    state.isChatFocusMode = nextValue;
    document.body.classList.toggle('chat-focus-mode', state.isChatFocusMode);
    syncChatFocusToggle();

    if (state.isChatFocusMode) {
        restoreChatComposerHeight();
        window.requestAnimationFrame(() => {
            scrollChatToBottom();
            chatElements.chatInput?.focus();
        });
    } else {
        state.chatComposerResize = null;
    }
}

function toggleChatFocusMode() {
    setChatFocusMode(!state.isChatFocusMode);
}

function resizeFocusedChatComposerFromPointer(clientY) {
    if (!state.isChatFocusMode) return;
    const height = getViewportHeight() - Number(clientY || 0);
    setChatComposerHeight(height);
}

function handleChatFocusDividerPointerDown(event) {
    if (!state.isChatFocusMode || !chatElements.focusDivider) return;
    event.preventDefault();
    state.chatComposerResize = { pointerId: event.pointerId };
    chatElements.focusDivider.setPointerCapture?.(event.pointerId);
    resizeFocusedChatComposerFromPointer(event.clientY);
}

function handleChatFocusDividerPointerMove(event) {
    if (!state.chatComposerResize || state.chatComposerResize.pointerId !== event.pointerId) return;
    event.preventDefault();
    resizeFocusedChatComposerFromPointer(event.clientY);
}

function stopChatFocusDividerResize(event) {
    if (!state.chatComposerResize) return;
    if (event?.pointerId !== undefined && state.chatComposerResize.pointerId !== event.pointerId) return;

    if (event?.pointerId !== undefined) {
        chatElements.focusDivider?.releasePointerCapture?.(event.pointerId);
    }
    state.chatComposerResize = null;
}

function handleChatFocusDividerKeydown(event) {
    if (!state.isChatFocusMode) return;

    const current = clampChatComposerHeight(chatElements.composer?.getBoundingClientRect().height);
    const step = event.shiftKey ? 64 : 24;
    let nextHeight = null;

    if (event.key === 'ArrowUp') nextHeight = current + step;
    if (event.key === 'ArrowDown') nextHeight = current - step;
    if (event.key === 'Home') nextHeight = CHAT_COMPOSE_MIN_HEIGHT;
    if (event.key === 'End') nextHeight = getChatComposerMaxHeight();

    if (nextHeight === null) return;
    event.preventDefault();
    setChatComposerHeight(nextHeight);
}

function handleChatScroll() {
    const container = chatElements.chatMessages;
    if (!container) return;
    state.shouldAutoScroll = isChatNearBottom(container);
}

function renderChatEmptyState(message) {
    chatRendering.renderEmptyState(message);
}

function refreshChatEmptyState() {
    chatRendering.refreshEmptyState();
}

function addChatErrorMessage(errorText) {
    chatRendering.addErrorMessage(errorText);
}


function formatAttachmentSize(sizeBytes) {
    if (!Number.isFinite(sizeBytes) || sizeBytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let value = sizeBytes;
    let idx = 0;
    while (value >= 1024 && idx < units.length - 1) {
        value /= 1024;
        idx += 1;
    }
    const precision = idx === 0 ? 0 : 1;
    return `${value.toFixed(precision)} ${units[idx]}`;
}

function renderPendingAttachments() {
    const items = chatComposeState.pendingAttachments;
    const badge = chatElements.attachCountBadge;
    const popover = chatElements.attachmentPopover;
    const attachBtn = chatElements.attachBtn;

    if (badge) {
        if (!items.length) {
            badge.textContent = '';
            badge.style.display = 'none';
        } else {
            badge.textContent = String(items.length);
            badge.style.display = 'inline-flex';
        }
    }

    if (attachBtn) {
        attachBtn.classList.toggle('has-attachments', items.length > 0);
    }

    if (!popover) return;
    if (!items.length) {
        popover.classList.add('hidden');
        popover.innerHTML = '';
        popover.setAttribute('aria-hidden', 'true');
        chatComposeState.popoverOpen = false;
        if (attachBtn) {
            attachBtn.setAttribute('aria-expanded', 'false');
        }
        return;
    }

    const listHtml = items
        .map((item, index) => `
            <div class="chat-attachment-item">
                <span class="chat-attachment-name" title="${escapeHtml(item.file.name)}">${escapeHtml(item.file.name)}</span>
                <span class="text-txt-secondary">${formatAttachmentSize(item.file.size)}</span>
                <button type="button" class="chat-attachment-remove" data-attachment-remove="${index}" aria-label="Remove attachment">✕</button>
            </div>
        `)
        .join('');
    popover.innerHTML = `
        <div class="chat-attachment-item">
            <button type="button" class="chat-attachment-remove" data-attachment-add="true" aria-label="Add images" style="padding:0.2rem 0.35rem;border-radius:0.35rem;border:1px solid rgb(var(--border-primary));">
                + Add images...
            </button>
        </div>
        ${listHtml}
    `;
    popover.setAttribute('aria-hidden', chatComposeState.popoverOpen ? 'false' : 'true');
}

function setAttachmentPopoverOpen(isOpen) {
    const popover = chatElements.attachmentPopover;
    const attachBtn = chatElements.attachBtn;
    if (!popover || !attachBtn) return;
    const shouldOpen = Boolean(isOpen) && chatComposeState.pendingAttachments.length > 0;
    chatComposeState.popoverOpen = shouldOpen;
    popover.classList.toggle('hidden', !shouldOpen);
    popover.setAttribute('aria-hidden', shouldOpen ? 'false' : 'true');
    attachBtn.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
}

function clearPendingAttachments() {
    chatComposeState.pendingAttachments = [];
    setAttachmentPopoverOpen(false);
    if (chatElements.attachInput) {
        chatElements.attachInput.value = '';
    }
    renderPendingAttachments();
}

function addPendingAttachments(fileList) {
    if (!fileList || !fileList.length) return;

    const existingKeys = new Set(
        chatComposeState.pendingAttachments.map(
            (item) => `${item.file.name}:${item.file.size}:${item.file.lastModified}`
        )
    );

    for (const file of Array.from(fileList)) {
        if (!file || !file.type || !file.type.startsWith('image/')) {
            continue;
        }
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        if (existingKeys.has(key)) {
            continue;
        }
        existingKeys.add(key);
        chatComposeState.pendingAttachments.push({ file, key });
    }
    renderPendingAttachments();
    if (chatComposeState.pendingAttachments.length > 0) {
        setAttachmentPopoverOpen(true);
    }
}

function openAttachmentPicker() {
    if (chatElements.attachInput) {
        chatElements.attachInput.click();
    }
}

function createClientSessionId(vault) {
    const safeVault = String(vault || 'chat').trim().replace(/[\s/\\]+/g, '_') || 'chat';
    const now = new Date();
    const pad = (value) => String(value).padStart(2, '0');
    const stamp = [
        now.getFullYear(),
        pad(now.getMonth() + 1),
        pad(now.getDate())
    ].join('') + '_' + [
        pad(now.getHours()),
        pad(now.getMinutes()),
        pad(now.getSeconds())
    ].join('');
    return `${safeVault}_${stamp}_${now.getMilliseconds()}_${Math.random().toString(36).slice(2, 6)}`;
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

function populateThinkingSelector() {
    if (!chatElements.thinkingSelector) return;
    const defaultThinking = state.metadata?.settings?.default_model_thinking || 'default';
    const allowed = new Set(Array.from(chatElements.thinkingSelector.options).map((option) => option.value));
    const selected = allowed.has(defaultThinking) ? defaultThinking : 'default';
    chatElements.thinkingSelector.value = selected;
}

async function fetchSessions(vault, preferredSessionId = '') {
    state.sessions = [];
    sessionControls.renderSelector();
    if (!vault) {
        sessionControls.clearCompactionProgress();
        return;
    }
    try {
        const response = await fetch(`api/chat/sessions?vault_name=${encodeURIComponent(vault)}`);
        if (!response.ok) {
            throw new Error('Failed to fetch chat sessions');
        }
        state.sessions = await response.json();
        sessionControls.renderSelector();
        if (preferredSessionId && state.sessions.some((session) => session.session_id === preferredSessionId)) {
            state.sessionId = preferredSessionId;
            sessionControls.renderSelector();
        }
        await sessionControls.refreshCompactionProgress();
    } catch (error) {
        console.error('Error fetching chat sessions:', error);
    }
}

async function loadSession(sessionId, options = {}) {
    const vault = chatElements.vaultSelector?.value || '';
    if (!vault || !sessionId) {
        return;
    }
    chatRendering.closeToolCallDetails();
    let loadedSessionId = '';
    let loadedHistoryRevision = null;
    try {
        state.pendingDeferredReview = null;
        state.sessionId = sessionId;
        sessionControls.renderSelector();
        void sessionControls.refreshCompactionProgress();
        state.isLoading = true;
        syncChatControlLocks();
        const response = await fetch(
            `api/chat/sessions/${encodeURIComponent(sessionId)}?vault_name=${encodeURIComponent(vault)}`
        );
        if (!response.ok) {
            throw new Error('Failed to load chat session');
        }
        const payload = await response.json();
        if (state.sessionId !== sessionId || chatElements.vaultSelector?.value !== vault) {
            return;
        }
        state.sessionId = payload.session_id || sessionId;
        loadedSessionId = state.sessionId;
        loadedHistoryRevision = Number.isInteger(payload.history_revision)
            ? payload.history_revision
            : null;
        if (chatElements.chatModeSelector) {
            chatElements.chatModeSelector.value = payload.chat_mode === 'inline_edit'
                ? 'inline_edit'
                : 'normal';
        }
        state.pendingDeferredReview = payload.pending_review || null;
        state.workspaceExists = payload.workspace
            ? payload.workspace.exists === true
            : null;
        if (chatElements.workspacePathInput) {
            chatElements.workspacePathInput.value = payload.workspace?.path || '';
        }
        renderPersistedSession(payload, options);
        if (state.pendingDeferredReview) {
            const reviewMessage = createAssistantStreamingMessage();
            handleDeferredReviewEvent(reviewMessage, state.pendingDeferredReview);
            setAssistantStatus(reviewMessage, 'Waiting for review', 'tools');
        }
        sessionControls.renderSelector();
        sessionControls.updateTitleRow();
        updateStatus();
    } catch (error) {
        console.error('Error loading chat session:', error);
        addChatErrorMessage(error.message);
    } finally {
        state.isLoading = false;
        syncChatControlLocks();
    }
    if (
        loadedSessionId
        && !options.skipActiveTaskCheck
        && !state.activeChatTaskId
        && state.sessionId === loadedSessionId
    ) {
        await reattachActiveChatTask(loadedSessionId, vault, {
            historyRevision: loadedHistoryRevision,
        });
    }
}

async function reconcileCommittedToolCalls(context, vault, sessionId) {
    try {
        const response = await fetch(
            `api/chat/sessions/${encodeURIComponent(sessionId)}?vault_name=${encodeURIComponent(vault)}`,
            { cache: 'no-store' }
        );
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const payload = await response.json();
        if (state.sessionId !== sessionId || chatElements.vaultSelector?.value !== vault) {
            return;
        }
        chatRendering.reconcileToolCallPersistence(context, payload.tool_calls);
    } catch (error) {
        console.warn('Unable to reconcile committed tool details:', error);
        if (state.sessionId === sessionId && chatElements.vaultSelector?.value === vault) {
            chatRendering.reconcileToolCallPersistence(context, []);
        }
    }
}

async function reattachActiveChatTask(
    sessionId,
    vault,
    { historyRevision = null } = {}
) {
    let response;
    try {
        response = await fetch(
            `api/chat/sessions/${encodeURIComponent(sessionId)}/active-task`,
            { cache: 'no-store' }
        );
    } catch (error) {
        console.warn('Could not check for an active chat task:', error);
        return;
    }
    if (response.status === 404) {
        const errorData = await response.json().catch(() => ({}));
        const currentRevision = errorData?.details?.history_revision;
        if (
            Number.isInteger(historyRevision)
            && Number.isInteger(currentRevision)
            && currentRevision !== historyRevision
            && state.sessionId === sessionId
            && chatElements.vaultSelector?.value === vault
        ) {
            await loadSession(sessionId, { skipActiveTaskCheck: true });
        }
        return;
    }
    if (!response.ok) {
        console.warn(`Could not check for an active chat task: HTTP ${response.status}`);
        return;
    }

    const task = await response.json();
    if (!task?.task_id || !['queued', 'running'].includes(task.status)) return;
    if (state.activeChatTaskId || state.sessionId !== sessionId) return;

    const abortController = new AbortController();
    state.isLoading = true;
    state.isCancellingChat = false;
    state.activeChatSessionId = sessionId;
    state.activeChatTaskId = task.task_id;
    state.activeChatAbortController = abortController;
    syncChatControlLocks();
    try {
        await chatTaskActions.streamStartedTask(
            { session_id: sessionId, task },
            vault,
            abortController,
            { hydrateReplay: true }
        );
    } catch (error) {
        console.error('Error reattaching to active chat task:', error);
        if (!state.isCancellingChat && error.name !== 'AbortError') {
            addChatErrorMessage(error.message);
        }
    } finally {
        if (chatTaskStream.releaseActiveStream(abortController)) {
            syncChatControlLocks();
        }
    }
}

function renderPersistedSession(payload, options = {}) {
    chatRendering.renderPersistedSession(payload, options);
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

function getConfigurationWarnings() {
    const status = state.systemStatus;
    if (!status || !status.configuration_status) return [];
    const issues = status.configuration_status.issues || [];
    const warnings = issues.filter((issue) => {
        const severity = (issue.severity || '').toLowerCase();
        if (severity !== 'warning' && severity !== 'error') {
            return false;
        }
        const name = issue.name || '';
        if (name.startsWith('model:') || name.startsWith('tool:')) {
            return false;
        }
        return true;
    });
    if (status.authentication_warning) {
        warnings.unshift({
            name: 'authentication:disabled',
            severity: 'warning',
            message: status.authentication_warning
        });
    }
    return warnings;
}

// Fetch metadata from API
async function fetchMetadata() {
    try {
        const response = await fetch('api/metadata');
        if (!response.ok) throw new Error('Failed to fetch metadata');

        state.metadata = await response.json();
        // Expose for other modules (e.g., configuration import panel) to avoid duplicate fetches.
        window.App = window.App || {};
        window.App.metadata = state.metadata;
        window.ConfigurationPanel?.onMetadataUpdated?.();
        populateSelectors();
        updateStatus();
    } catch (error) {
        console.error('Error fetching metadata:', error);
        // Connection failure - could add a warning here if needed
        updateStatus();
    }
}

// Populate selectors with metadata
function populateSelectors() {
    const previousVault = chatElements.vaultSelector?.value || '';
    const previousModel = chatElements.modelSelector?.value || '';
    const previousTemplate = chatElements.templateSelector?.value || '';

    chatElements.vaultSelector.innerHTML = '<option value="">Select vault...</option>';
    chatElements.modelSelector.innerHTML = '<option value="">Select model...</option>';
    if (chatElements.templateSelector) {
        chatElements.templateSelector.innerHTML = '<option value="">No context script</option>';
        chatElements.templateSelector.disabled = true;
    }
    populateThinkingSelector();
    if (!state.sessionId) {
        resetChatModeToDefault();
    }

    state.metadata.vaults.forEach(vault => {
        const option = document.createElement('option');
        option.value = vault;
        option.textContent = vault;
        chatElements.vaultSelector.appendChild(option);
    });

    if (previousVault && state.metadata.vaults.includes(previousVault)) {
        chatElements.vaultSelector.value = previousVault;
    }

    let firstAvailableModel = null;
    const envDefaultModel = state.systemStatus && state.systemStatus.configuration_status
        ? state.systemStatus.configuration_status.default_model
        : null;

    const chatModels = state.metadata.models.filter(isChatSelectableModel);

    chatModels.forEach(model => {
        const option = document.createElement('option');
        option.value = model.name;
        const displayModelName = model.model_string || model.model || model.provider;
        option.textContent = `${model.name} (${displayModelName})${model.available ? '' : ' (unavailable)'}`;
        option.disabled = model.available === false;
        chatElements.modelSelector.appendChild(option);

        if (model.available && !firstAvailableModel) {
            firstAvailableModel = model.name;
        }
    });

    if (
        previousModel &&
        chatModels.some(m => m.name === previousModel && m.available !== false)
    ) {
        chatElements.modelSelector.value = previousModel;
    } else if (envDefaultModel && chatModels.some(m => m.name === envDefaultModel && m.available)) {
        chatElements.modelSelector.value = envDefaultModel;
    } else if (firstAvailableModel) {
        chatElements.modelSelector.value = firstAvailableModel;
    }

    // Trigger template fetch if a vault is already selected (e.g., persisted UI state in future)
    if (chatElements.vaultSelector && chatElements.vaultSelector.value) {
        fetchTemplates(chatElements.vaultSelector.value, previousTemplate);
        fetchSessions(chatElements.vaultSelector.value, state.sessionId || '');
    }

    sessionControls.renderSelector();
    syncChatControlLocks();
}

function configuredDefaultChatMode() {
    return state.metadata?.settings?.default_chat_mode === 'inline_edit'
        ? 'inline_edit'
        : 'normal';
}

function resetChatModeToDefault() {
    if (chatElements.chatModeSelector) {
        chatElements.chatModeSelector.value = configuredDefaultChatMode();
    }
}

function isChatSelectableModel(model) {
    const capabilities = Array.isArray(model?.capabilities)
        ? model.capabilities.map(capability => String(capability || '').trim().toLowerCase())
        : [];
    return !capabilities.includes('embedding');
}

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

async function persistSelectedChatMode() {
    const vault = chatElements.vaultSelector?.value || '';
    const sessionId = state.sessionId;
    if (!vault || !sessionId || !chatElements.chatModeSelector) return;
    const chatMode = chatElements.chatModeSelector.value === 'inline_edit'
        ? 'inline_edit'
        : 'normal';
    try {
        const response = await fetch(`api/chat/sessions/${encodeURIComponent(sessionId)}/mode`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ vault_name: vault, chat_mode: chatMode }),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const session = state.sessions.find((item) => item.session_id === sessionId);
        if (session) session.chat_mode = chatMode;
    } catch (error) {
        console.error('Failed to persist chat mode:', error);
        addChatErrorMessage('Could not save the selected chat mode.');
    }
}

function handleVaultChange() {
    const vault = chatElements.vaultSelector ? chatElements.vaultSelector.value : '';
    state.sessionId = null;
    state.pendingDeferredReview = null;
    state.workspaceExists = null;
    state.sessions = [];
    resetChatModeToDefault();
    if (chatElements.workspacePathInput) {
        chatElements.workspacePathInput.value = '';
    }
    sessionControls.clearCompactionProgress();
    sessionControls.renderSelector();
    sessionControls.updateTitleRow();
    renderChatEmptyState();
    updateStatus();
    populateTemplates([]); // reset while loading
    if (vault) {
        fetchTemplates(vault);
        fetchSessions(vault);
    }
}

function populateTemplates(templates, preferredTemplate = '') {
    if (!chatElements.templateSelector) return;
    const templateList = Array.isArray(templates) ? templates : [];
    chatElements.templateSelector.innerHTML = '<option value="">No context script</option>';
    templateList.forEach((tmpl) => {
        const option = document.createElement('option');
        option.value = tmpl.name;
        option.textContent = `${tmpl.name} (${tmpl.source})`;
        chatElements.templateSelector.appendChild(option);
    });
    const configuredDefaultTemplate = state.metadata?.default_context_script || '';
    const fallbackCandidates = [
        preferredTemplate,
        configuredDefaultTemplate,
        'default.md'
    ].filter((value, index, values) => value && values.indexOf(value) === index);

    const selectedTemplate = fallbackCandidates.find((candidate) =>
        Array.from(chatElements.templateSelector.options).some((option) => option.value === candidate)
    );
    if (selectedTemplate) {
        chatElements.templateSelector.value = selectedTemplate;
    } else {
        chatElements.templateSelector.value = '';
    }
    chatElements.templateSelector.disabled = false;
}

async function fetchTemplates(vault, preferredTemplate = '') {
    if (!vault) {
        populateTemplates([], preferredTemplate);
        return;
    }
    try {
        const response = await fetch(`api/context/templates?vault_name=${encodeURIComponent(vault)}`);
        if (!response.ok) {
            throw new Error('Failed to fetch templates');
        }
        const templates = await response.json();
        populateTemplates(templates, preferredTemplate);
    } catch (error) {
        console.error('Error fetching templates:', error);
        populateTemplates([], preferredTemplate);
    }
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

function updateStatus(message) {
    if (!configElements.statusBanner || !configElements.statusMessages || !configElements.configTab) return;

    const warnings = getConfigurationWarnings();
    const noticeLines = [];
    let repairNeeded = false;

    if (state.restartRequired) {
        noticeLines.push(RESTART_NOTICE_TEXT);
    }

    warnings.forEach((issue) => {
        noticeLines.push(issue.message);
        if (issue.name && /^(settings|models|providers|tools):(missing|extra|missing_metadata)$/.test(issue.name)) {
            repairNeeded = true;
        }
    });

    // Check for no vaults
    if (state.metadata && state.metadata.vaults && state.metadata.vaults.length === 0) {
        noticeLines.push('No vaults found. Review installation instructions.');
    }

    // Update System tab and banner
    if (noticeLines.length === 0) {
        // No warnings - hide banner and remove tab highlight
        configElements.statusBanner.classList.add('hidden');
        configElements.statusMessages.innerHTML = '';
        configElements.configTab.classList.remove('font-semibold', 'bg-app-elevated', 'px-3', 'rounded-t-md', 'text-accent');
        configElements.configTab.classList.add('text-txt-secondary');
        configElements.configTab.style.borderColor = '';
        configElements.configTab.textContent = 'System';
    } else {
        // Show warnings in banner and highlight tab with background
        configElements.statusBanner.classList.remove('hidden');
        const noticeHtml = noticeLines.map(line => `<div>• ${escapeHtml(line)}</div>`).join('');
        let messageHtml = noticeHtml;
        if (repairNeeded) {
            messageHtml = `
                <div class="flex flex-wrap items-start gap-2">
                    <button id="repair-settings-btn" type="button" class="ui-icon-button is-primary shrink-0" data-icon="wrench" data-icon-label="Repair settings from template"></button>
                    <div class="flex-1 min-w-0 space-y-1">${noticeHtml}</div>
                </div>
            `;
        }
        configElements.statusMessages.innerHTML = messageHtml;
        window.AssistantMDIcons.hydrateIconButtons(configElements.statusMessages);
        configElements.configTab.classList.remove('text-txt-secondary', 'text-txt-primary');
        configElements.configTab.classList.add('text-accent', 'font-semibold', 'bg-app-elevated', 'px-3', 'rounded-t-md');
        configElements.configTab.style.borderColor = 'rgb(var(--border-primary))';
        configElements.configTab.textContent = 'System ⚠️';
        const repairBtn = document.getElementById('repair-settings-btn');
        if (repairBtn) {
            repairBtn.addEventListener('click', async () => {
                const confirmed = window.confirm(
                    'Repair settings from template?\n\nThis will add missing keys and metadata from settings.template.yaml, prune unknown settings, and remove unknown non-user-editable tools/models/providers. Existing values for matching keys will be preserved.\nA backup will be written to system/settings.bak. Reload the page after repair to see changes.'
                );
                if (!confirmed) return;

                repairBtn.disabled = true;
                window.AssistantMDIcons.setIconButtonLabel(repairBtn, 'Repairing settings...');
                let alertEl = document.getElementById('config-repair-alert');
                if (!alertEl && configElements.statusMessages) {
                    alertEl = document.createElement('div');
                    alertEl.id = 'config-repair-alert';
                    alertEl.className = 'mt-2 text-sm';
                    configElements.statusMessages.appendChild(alertEl);
                }
                const showAlert = (text, tone = 'info') => {
                    if (!alertEl) return;
                    alertEl.textContent = text;
                    alertEl.className = `mt-2 text-sm ${tone === 'error' ? 'state-error' : 'text-txt-secondary'}`;
                };
                try {
                    const resp = await fetch('api/system/settings/repair', { method: 'POST' });
                    if (!resp.ok) throw new Error(await resp.text() || 'Repair failed');
                    await fetchSystemStatus();
                    showAlert('Settings repaired. Backup saved to system/settings.bak. Reload the page to see new defaults.', 'info');
                } catch (err) {
                    console.error('Settings repair failed', err);
                    showAlert('Settings repair failed: ' + err.message, 'error');
                } finally {
                    repairBtn.disabled = false;
                    window.AssistantMDIcons.setIconButtonLabel(repairBtn, 'Repair settings from template');
                }
            });
        }
    }
}

function setRestartRequired(required = true) {
    const currentStartup = state.systemStatus?.system?.startup_time || null;

    if (!required) {
        state.restartRequired = false;
        browserStorage.removeItem(RESTART_STORAGE_KEY);
        if (window.ConfigurationPanel && typeof window.ConfigurationPanel.setRestartRequired === 'function') {
            window.ConfigurationPanel.setRestartRequired(false);
        }
        updateStatus();
        return;
    }

    const payload = { required: true, startupTime: currentStartup };
    browserStorage.setItem(RESTART_STORAGE_KEY, JSON.stringify(payload));

    state.restartRequired = true;
    if (window.ConfigurationPanel && typeof window.ConfigurationPanel.setRestartRequired === 'function') {
        window.ConfigurationPanel.setRestartRequired(true);
    }
    updateStatus();
}

function syncRestartFlagWithStorage() {
    let stored = null;
    try {
        const raw = browserStorage.getItem(RESTART_STORAGE_KEY);
        stored = raw ? JSON.parse(raw) : null;
    } catch (error) {
        console.warn('Failed to read restart-required flag:', error);
        browserStorage.removeItem(RESTART_STORAGE_KEY);
    }

    const currentStartup = state.systemStatus?.system?.startup_time || null;

    const isValid = stored && stored.required && (!stored.startupTime || stored.startupTime === currentStartup);

    if (isValid) {
        if (currentStartup && stored.startupTime !== currentStartup) {
            browserStorage.setItem(RESTART_STORAGE_KEY, JSON.stringify({ required: true, startupTime: currentStartup }));
        }
        if (!state.restartRequired) {
            state.restartRequired = true;
            if (window.ConfigurationPanel && typeof window.ConfigurationPanel.setRestartRequired === 'function') {
                window.ConfigurationPanel.setRestartRequired(true);
            }
            updateStatus();
        }
        return;
    }

    if (state.restartRequired) {
        state.restartRequired = false;
        if (window.ConfigurationPanel && typeof window.ConfigurationPanel.setRestartRequired === 'function') {
            window.ConfigurationPanel.setRestartRequired(false);
        }
        updateStatus();
    }

    browserStorage.removeItem(RESTART_STORAGE_KEY);
}

// Start app
window.App = window.App || {};
window.App.setRestartRequired = setRestartRequired;

init();
