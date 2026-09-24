/** Shared state and UI helpers for the configuration panel controllers. */
(function configurationRuntime(window) {
    const ACTIVITY_LOG_LEVELS = ['critical', 'error', 'warning', 'warn', 'info', 'debug', 'fatal'];
    const DEFAULT_IMPORT_JOB_STATUSES = ['queued', 'processing', 'failed'];
    const state = {
        initialized: false,
        hasLoadedOnce: false,
        isLoadingLog: false,
        activityLogRequestId: 0,
        activityLogAbortController: null,
        isLoadingSettings: false,
        isLoadingModels: false,
        modelsLoadFailed: false,
        isSavingModel: false,
        isSavingSetting: false,
        isLoadingProviders: false,
        isSavingProvider: false,
        isLoadingSecrets: false,
        isSavingSecret: false,
        isLoadingGoogle: false,
        isSavingGoogle: false,
        isLoadingMcp: false,
        isSavingMcp: false,
        isTestingMcp: false,
        isPurgingCache: false,
        isCleaningGoals: false,
        isCleaningVaultState: false,
        isRefreshingSystemAuthoring: false,
        isLoadingSystemJobs: false,
        isLoadingSystemMigrations: false,
        isRunningSystemMigrations: false,
        isLoadingImportVaults: false,
        isLoadingImportJobs: false,
        pendingImportJobsReload: false,
        hasLoadedImportJobs: false,
        isTriggeringImportQueue: false,
        isSavingImportDefaults: false,
        settings: [],
        models: [],
        providers: [],
        secrets: [],
        googleConnections: [],
        googleDraft: false,
        mcpConnections: [],
        mcpAdvancedMode: false,
        mcpAdvancedShellReady: false,
        mcpOAuthStatuses: {},
        importVaults: [],
        importJobs: [],
        importJobsNextCursor: null,
        importJobsTotalMatching: 0,
        importJobStatusCounts: {},
        activityLogEntries: [],
        activityLogNextCursor: null,
        activityLogTotalMatching: 0,
        activityLogAvailableTags: [],
        activityLogFilters: {
            query: '',
            levels: [...ACTIVITY_LOG_LEVELS],
            tags: [],
            latestFirst: true
        },
        activityLogFilterMenus: {
            level: false,
            tag: false
        },
        systemJobs: [],
        systemMigrations: null,
        settingEditKey: null,
        settingDraftValue: '',
        modelEdit: null,
        modelDraft: null,
        providerEdit: null,
        providerDraft: null,
        openAiOauthPaste: '',
        openAiOauthAuthUrl: '',
        openAiOauthDeviceVerificationUrl: '',
        openAiOauthDeviceUserCode: '',
        openAiOauthDeviceExpiresAt: '',
        openAiOauthDevicePollIntervalSeconds: null,
        isOpenAiOauthBusy: false,
        secretEdit: null,
        secretDraft: null,
        settingsFilter: ''
    };

    const BUILT_IN_PROVIDER_NAMES = new Set(['anthropic', 'google', 'grok', 'mistral', 'openai', 'openrouter']);

    const SECRET_METADATA = {
        OPENAI_API_KEY: { label: 'OpenAI API Key', description: 'Required for OpenAI model aliases' },
        ANTHROPIC_API_KEY: { label: 'Anthropic API Key', description: 'Required for Claude model aliases' },
        GOOGLE_API_KEY: { label: 'Google API Key', description: 'Required for Google Gemini model aliases' },
        GROK_API_KEY: { label: 'Grok API Key', description: 'Required for Grok model aliases' },
        MISTRAL_API_KEY: { label: 'Mistral API Key', description: 'Required for Mistral model aliases' },
        OPENROUTER_API_KEY: { label: 'OpenRouter API Key', description: 'Required for OpenRouter model aliases' },
        TAVILY_API_KEY: { label: 'Tavily API Key', description: 'Required for Tavily search/crawl tools' },
        LOGFIRE_TOKEN: { label: 'Logfire Token', description: 'Enables cloud telemetry when set' },
        LM_STUDIO_API_KEY: { label: 'LM Studio API Key', description: 'Optional key for LM Studio endpoints' },
        LM_STUDIO_BASE_URL: { label: 'LM Studio Base URL', description: 'Custom endpoint for LM Studio (http://host:port)' },
        OLLAMA_API_KEY: { label: 'Ollama API Key', description: 'Optional key for secured Ollama endpoints' },
        OLLAMA_BASE_URL: { label: 'Ollama Base URL', description: 'Custom endpoint for Ollama (http://host:port)' },
    };

    const callbacks = {
        refreshMetadata: null,
        refreshStatus: null,
        openFile: null,
        openExplorer: null
    };
    const timers = { activityLogSearch: null, importJobPoll: null, mcpOAuthStatusRequest: null };
    const resources = { googleOAuthPolls: new Map(), mcpOAuthPolls: new Map() };
    const elements = {
        activityLogViewer: null,
        refreshActivityLogBtn: null,
        activityLogSearch: null,
        activityLogLevelDropdown: null,
        activityLogLevelTrigger: null,
        activityLogLevelMenu: null,
        activityLogLevelSummary: null,
        activityLogLevelOptions: null,
        activityLogTagDropdown: null,
        activityLogTagTrigger: null,
        activityLogTagMenu: null,
        activityLogTagSummary: null,
        activityLogTagOptions: null,
        activityLogLatestFirst: null,
        activityLogCount: null,
        activityLogLoadOlderBtn: null,

        settingsFeedback: null,
        settingsFilter: null,
        settingsList: null,

        modelFeedback: null,
        modelList: null,
        modelAddBtn: null,

        providerFeedback: null,
        providerList: null,
        providerAddBtn: null,

        secretsList: null,
        secretFeedback: null,
        secretAddBtn: null,

        googleConnectionForm: null,
        googleConnectionStatus: null,
        googleConnectionFeedback: null,
        googleConnectionsList: null,
        connectionAddGoogle: null,
        connectionAddMcp: null,
        connectionsFeedback: null,

        mcpCreateForm: null,
        mcpConnectionsList: null,
        mcpFeedback: null,

        miscFeedback: null,
        refreshSystemAuthoringBtn: null,
        refreshSystemAuthoringFeedback: null,
        purgeExpiredCacheBtn: null,
        cleanupVaultStateBtn: null,
        cleanupVaultStateFeedback: null,
        systemJobsList: null,
        refreshSystemJobsBtn: null,
        systemMigrationsStatus: null,
        systemMigrationsFeedback: null,
        refreshSystemMigrationsBtn: null,
        runSystemMigrationsBtn: null,
        purgeSessionsVault: null,
        purgeSessionsAge: null,
        purgeSessionsBtn: null,
        purgeSessionsFeedback: null,
        cleanupGoalsVault: null,
        cleanupGoalsStatus: null,
        cleanupGoalsAge: null,
        cleanupGoalsBtn: null,
        cleanupGoalsFeedback: null,

        importVaultSelect: null,
        importPdfModeSelect: null,
        importPdfStrategySelect: null,
        importPdfStrategyHelp: null,
        importMarkdownOptions: null,
        importPageImageOptions: null,
        importCaptureOcrImagesCheckbox: null,
        importStatus: null,
        importDefaultsSaveBtn: null,
        importOpenExplorerBtn: null,
        importRefreshVaultsBtn: null,
        importJobsSummary: null,
        importJobsFeedback: null,
        importJobsList: null,
        importJobsRefreshBtn: null,
        importJobsRunNowBtn: null,
        importJobsStatusFilters: null,
        importJobsLoadOlderBtn: null
    };

    const toneClasses = {
        info: 'text-txt-secondary',
        success: 'state-success',
        warning: 'state-warning',
        error: 'state-error'
    };

    function iconButton(iconName, label, extraClass = '', attrs = '') {
        return `class="ui-icon-button is-compact ${extraClass}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}" ${attrs}`;
    }

    function iconSvg(iconName) {
        const icon = window.AssistantMDIcons;
        const svgByName = {
            clean: icon.CLEAN_ICON_SVG,
            database: icon.DATABASE_ICON_SVG,
            edit: icon.EDIT_ICON_SVG,
            play: icon.PLAY_ICON_SVG,
            refresh: icon.REFRESH_ICON_SVG,
            save: icon.SAVE_ICON_SVG,
            trash: icon.TRASH_ICON_SVG,
            alert: icon.ALERT_ICON_SVG,
            x: icon.X_ICON_SVG,
            circleX: icon.CIRCLE_X_ICON_SVG,
            check: icon.CHECK_ICON_SVG,
            copy: icon.COPY_ICON_SVG,
            link: icon.LINK_ICON_SVG,
        };
        return svgByName[iconName] || icon.SETTINGS_ICON_SVG;
    }

    function setIconButtonLabel(button, label) {
        window.AssistantMDIcons.setIconButtonLabel(button, label);
    }

    const restartNoticeText = 'Restart recommended: restart the container to apply pending changes.';

    function setStatus(element, message, tone = 'info') {
        if (!element) return;

        element.classList.remove(...Object.values(toneClasses));
        const className = toneClasses[tone] || toneClasses.info;
        element.classList.add(className);
        element.textContent = message;
    }

    function withRestartNotice(message, result) {
        const restart = Boolean(result && result.restart_required);
        if (restart && window.App && typeof window.App.setRestartRequired === 'function') {
            window.App.setRestartRequired(true);
        }
        return {
            text: restart ? `${message} ${restartNoticeText}` : message,
            restart,
        };
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>'"]/g, (char) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[char]));
    }

    function formatDateTime(value) {
        if (!value) return '—';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '—';
        return date.toLocaleString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
        });
    }

    async function notifyConfigChanged() {
        if (typeof callbacks.refreshMetadata === 'function') {
            try {
                await callbacks.refreshMetadata();
            } catch (error) {
                console.error('Failed to refresh metadata:', error);
            }
        }

        if (typeof callbacks.refreshStatus === 'function') {
            try {
                await callbacks.refreshStatus();
            } catch (error) {
                console.error('Failed to refresh system status:', error);
            }
        }
    }

    async function safeJson(response) {
        try {
            return await response.json();
        } catch (_) {
            return null;
        }
    }


    window.ConfigurationPanelRuntime = {
        actions: {},
        callbacks,
        constants: { ACTIVITY_LOG_LEVELS, BUILT_IN_PROVIDER_NAMES, DEFAULT_IMPORT_JOB_STATUSES, SECRET_METADATA },
        elements,
        helpers: { escapeHtml, formatDateTime, iconButton, iconSvg, notifyConfigChanged, safeJson, setIconButtonLabel, setStatus, withRestartNotice },
        resources,
        state,
        timers,
    };
}(window));
