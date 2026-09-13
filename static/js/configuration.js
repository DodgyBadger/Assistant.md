/**
 * System and dashboard maintenance panel logic.
 *
 * Provides a structured interface for activity logs, model mappings,
 * provider settings, and secrets management without polluting app.js.
 */

(function configurationModule(window, document) {
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
        isScanningImport: false,
        isLoadingImportVaults: false,
        isLoadingImportJobs: false,
        pendingImportJobsReload: false,
        hasLoadedImportJobs: false,
        isTriggeringImportQueue: false,
        isImportingUrl: false,
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
        importResults: null,
        importUrlResult: null,
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
        openFile: null
    };
    let activityLogSearchTimer = null;
    let importJobPollTimer = null;
    const googleOAuthPolls = new Map();
    const mcpOAuthPolls = new Map();
    let mcpOAuthStatusRequest = null;

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
        importOcrOptions: null,
        importPdfOcrEnrichments: null,
        importPageImageOptions: null,
        importQueueCheckbox: null,
        importCaptureOcrImagesCheckbox: null,
        importIncludeOcrBlocksCheckbox: null,
        importExtractOcrHeaderCheckbox: null,
        importExtractOcrFooterCheckbox: null,
        importOcrTableFormatSelect: null,
        importOcrConfidenceSelect: null,
        importStatus: null,
        importScanBtn: null,
        importRefreshVaultsBtn: null,
        importResults: null,
        importUrlForm: null,
        importUrlInput: null,
        importUrlSubmit: null,
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

    function cacheElements() {
        elements.activityLogViewer = document.getElementById('activity-log-viewer');
        elements.refreshActivityLogBtn = document.getElementById('refresh-activity-log');
        elements.activityLogSearch = document.getElementById('activity-log-search');
        elements.activityLogLevelDropdown = document.getElementById('activity-log-level-dropdown');
        elements.activityLogLevelTrigger = document.getElementById('activity-log-level-trigger');
        elements.activityLogLevelMenu = document.getElementById('activity-log-level-menu');
        elements.activityLogLevelSummary = document.getElementById('activity-log-level-summary');
        elements.activityLogLevelOptions = document.getElementById('activity-log-level-options');
        elements.activityLogTagDropdown = document.getElementById('activity-log-tag-dropdown');
        elements.activityLogTagTrigger = document.getElementById('activity-log-tag-trigger');
        elements.activityLogTagMenu = document.getElementById('activity-log-tag-menu');
        elements.activityLogTagSummary = document.getElementById('activity-log-tag-summary');
        elements.activityLogTagOptions = document.getElementById('activity-log-tag-options');
        elements.activityLogLatestFirst = document.getElementById('activity-log-latest-first');
        elements.activityLogCount = document.getElementById('activity-log-count');
        elements.activityLogLoadOlderBtn = document.getElementById('load-older-activity-log');

        elements.settingsFeedback = document.getElementById('settings-feedback');
        elements.settingsFilter = document.getElementById('settings-filter');
        elements.settingsList = document.getElementById('settings-list');

        elements.modelFeedback = document.getElementById('model-feedback');
        elements.modelList = document.getElementById('model-list');
        elements.modelAddBtn = document.getElementById('model-add-row');

        elements.providerFeedback = document.getElementById('provider-feedback');
        elements.providerList = document.getElementById('provider-list');
        elements.providerAddBtn = document.getElementById('provider-add-row');

        elements.secretsList = document.getElementById('secrets-list');
        elements.secretFeedback = document.getElementById('secret-feedback');
        elements.secretAddBtn = document.getElementById('secret-add-row');

        elements.googleConnectionForm = document.getElementById('google-connection-form');
        elements.googleConnectionStatus = document.getElementById('google-connection-status');
        elements.googleConnectionFeedback = document.getElementById('google-connection-feedback');
        elements.googleConnectionsList = document.getElementById('google-connections-list');
        elements.connectionAddGoogle = document.getElementById('connection-add-google');
        elements.connectionAddMcp = document.getElementById('connection-add-mcp');
        elements.connectionsFeedback = document.getElementById('connections-feedback');

        elements.mcpCreateForm = document.getElementById('mcp-create-form');
        elements.mcpConnectionsList = document.getElementById('mcp-connections-list');
        elements.mcpFeedback = document.getElementById('mcp-feedback');

        elements.miscFeedback = document.getElementById('misc-feedback');
        elements.refreshSystemAuthoringBtn = document.getElementById('refresh-system-authoring');
        elements.refreshSystemAuthoringFeedback = document.getElementById('refresh-system-authoring-feedback');
        elements.purgeExpiredCacheBtn = document.getElementById('purge-expired-cache');
        elements.cleanupVaultStateBtn = document.getElementById('cleanup-vault-state');
        elements.cleanupVaultStateFeedback = document.getElementById('cleanup-vault-state-feedback');
        elements.systemJobsList = document.getElementById('system-jobs-list');
        elements.refreshSystemJobsBtn = document.getElementById('refresh-system-jobs');
        elements.systemMigrationsStatus = document.getElementById('system-migrations-status');
        elements.systemMigrationsFeedback = document.getElementById('system-migrations-feedback');
        elements.refreshSystemMigrationsBtn = document.getElementById('refresh-system-migrations');
        elements.runSystemMigrationsBtn = document.getElementById('run-system-migrations');
        elements.purgeSessionsVault = document.getElementById('purge-sessions-vault');
        elements.purgeSessionsAge = document.getElementById('purge-sessions-age');
        elements.purgeSessionsBtn = document.getElementById('purge-sessions-btn');
        elements.purgeSessionsFeedback = document.getElementById('purge-sessions-feedback');
        elements.cleanupGoalsVault = document.getElementById('cleanup-goals-vault');
        elements.cleanupGoalsStatus = document.getElementById('cleanup-goals-status');
        elements.cleanupGoalsAge = document.getElementById('cleanup-goals-age');
        elements.cleanupGoalsBtn = document.getElementById('cleanup-goals-btn');
        elements.cleanupGoalsFeedback = document.getElementById('cleanup-goals-feedback');

        elements.importVaultSelect = document.getElementById('import-vault-select');
        elements.importPdfModeSelect = document.getElementById('import-pdf-mode');
        elements.importPdfStrategySelect = document.getElementById('import-pdf-strategy');
        elements.importPdfStrategyHelp = document.getElementById('import-pdf-strategy-help');
        elements.importMarkdownOptions = document.getElementById('import-markdown-options');
        elements.importOcrOptions = document.getElementById('import-ocr-options');
        elements.importPdfOcrEnrichments = document.getElementById('import-pdf-ocr-enrichments');
        elements.importPageImageOptions = document.getElementById('import-page-image-options');
        elements.importQueueCheckbox = document.getElementById('import-queue');
        elements.importCaptureOcrImagesCheckbox = document.getElementById('import-capture-ocr-images');
        elements.importIncludeOcrBlocksCheckbox = document.getElementById('import-include-ocr-blocks');
        elements.importExtractOcrHeaderCheckbox = document.getElementById('import-extract-ocr-header');
        elements.importExtractOcrFooterCheckbox = document.getElementById('import-extract-ocr-footer');
        elements.importOcrTableFormatSelect = document.getElementById('import-ocr-table-format');
        elements.importOcrConfidenceSelect = document.getElementById('import-ocr-confidence');
        elements.importStatus = document.getElementById('import-status');
        elements.importScanBtn = document.getElementById('import-scan');
        elements.importRefreshVaultsBtn = document.getElementById('import-refresh-vaults');
        elements.importResults = document.getElementById('import-results');
        elements.importUrlForm = document.getElementById('import-url-form');
        elements.importUrlInput = document.getElementById('import-url-input');
        elements.importUrlSubmit = document.getElementById('import-url-submit');
        elements.importJobsSummary = document.getElementById('import-jobs-summary');
        elements.importJobsFeedback = document.getElementById('import-jobs-feedback');
        elements.importJobsList = document.getElementById('import-jobs-list');
        elements.importJobsRefreshBtn = document.getElementById('import-jobs-refresh');
        elements.importJobsRunNowBtn = document.getElementById('import-jobs-run-now');
        elements.importJobsStatusFilters = document.getElementById('import-jobs-status-filters');
        elements.importJobsLoadOlderBtn = document.getElementById('import-jobs-load-older');
    }

    function bindEvents() {
        elements.refreshActivityLogBtn?.addEventListener('click', () => refreshActivityLog());
        elements.activityLogSearch?.addEventListener('input', handleActivityLogSearchInput);
        elements.activityLogLevelTrigger?.addEventListener('click', () => setActivityLogFilterMenuOpen('level', !state.activityLogFilterMenus.level));
        elements.activityLogLevelOptions?.addEventListener('change', handleActivityLogFilterChange);
        elements.activityLogTagTrigger?.addEventListener('click', () => setActivityLogFilterMenuOpen('tag', !state.activityLogFilterMenus.tag));
        elements.activityLogTagOptions?.addEventListener('change', handleActivityLogFilterChange);
        elements.activityLogLatestFirst?.addEventListener('change', handleActivityLogFilterChange);
        elements.activityLogLoadOlderBtn?.addEventListener('click', () => refreshActivityLog({ append: true }));
        document.addEventListener('click', handleActivityLogDocumentClick);

        elements.settingsList?.addEventListener('click', handleSettingsTableClick);
        elements.settingsList?.addEventListener('input', handleSettingsInputChange);
        elements.settingsFilter?.addEventListener('input', handleSettingsFilterInput);

        elements.modelAddBtn?.addEventListener('click', startNewModel);
        elements.modelList?.addEventListener('click', handleModelTableClick);
        elements.modelList?.addEventListener('input', handleModelInputChange);
        elements.modelList?.addEventListener('change', handleModelInputChange);

        elements.providerAddBtn?.addEventListener('click', startNewProvider);
        elements.providerList?.addEventListener('click', handleProviderTableClick);
        elements.providerList?.addEventListener('input', handleProviderInputChange);
        elements.providerList?.addEventListener('change', handleProviderInputChange);

        elements.secretAddBtn?.addEventListener('click', startNewSecret);
        elements.secretsList?.addEventListener('click', handleSecretsTableClick);
        elements.secretsList?.addEventListener('input', handleSecretInputChange);
        elements.connectionAddGoogle?.addEventListener('click', startGoogleConnectionDraft);
        elements.connectionAddMcp?.addEventListener('click', () => {
            elements.mcpCreateForm?.classList.remove('hidden');
            elements.mcpCreateForm?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
        elements.googleConnectionsList?.addEventListener('submit', saveGoogleConnection);
        elements.googleConnectionsList?.addEventListener('click', handleGoogleConnectionAction);
        elements.mcpCreateForm?.addEventListener('submit', handleMcpCreate);
        elements.mcpCreateForm?.addEventListener('click', (event) => {
            const parseImport = event.target instanceof Element ? event.target.closest('[data-mcp-create-action="parse-import"]') : null;
            if (parseImport) {
                void parseMcpImport();
                return;
            }
            const cancel = event.target instanceof Element ? event.target.closest('[data-mcp-create-action="cancel"]') : null;
            if (!cancel) return;
            elements.mcpCreateForm.reset();
            elements.mcpCreateForm.classList.add('hidden');
            updateMcpCreateAuthFields();
        });
        elements.mcpConnectionsList?.addEventListener('click', handleMcpConnectionAction);
        elements.mcpCreateForm?.addEventListener('change', updateMcpCreateAuthFields);
        elements.refreshSystemAuthoringBtn?.addEventListener('click', handleRefreshSystemAuthoring);
        elements.purgeExpiredCacheBtn?.addEventListener('click', handlePurgeExpiredCache);
        elements.cleanupVaultStateBtn?.addEventListener('click', handleCleanupVaultState);
        elements.refreshSystemJobsBtn?.addEventListener('click', loadSystemJobs);
        elements.refreshSystemMigrationsBtn?.addEventListener('click', loadSystemMigrations);
        elements.runSystemMigrationsBtn?.addEventListener('click', handleRunSystemMigrations);
        elements.purgeSessionsBtn?.addEventListener('click', handlePurgeSessions);
        elements.cleanupGoalsBtn?.addEventListener('click', handleCleanupGoals);

        elements.importScanBtn?.addEventListener('click', handleImportScan);
        elements.importRefreshVaultsBtn?.addEventListener('click', handleImportVaultRescan);
        elements.importVaultSelect?.addEventListener('change', handleImportVaultChange);
        elements.importUrlForm?.addEventListener('submit', (event) => {
            event.preventDefault();
            handleImportUrl();
        });
        elements.importJobsRefreshBtn?.addEventListener('click', () => loadImportJobs());
        elements.importJobsRunNowBtn?.addEventListener('click', handleRunImportQueueNow);
        elements.importJobsStatusFilters?.addEventListener('change', handleImportJobFilterChange);
        elements.importJobsLoadOlderBtn?.addEventListener('click', () => loadImportJobs({ append: true }));
        elements.importJobsList?.addEventListener('click', handleImportJobAction);
        elements.importResults?.addEventListener('click', handleImportJobAction);
        elements.importPdfModeSelect?.addEventListener('change', updateImportOcrAvailability);
        elements.importPdfStrategySelect?.addEventListener('change', updateImportOcrAvailability);
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

    async function refreshActivityLog({ append = false } = {}) {
        if (!elements.activityLogViewer) return;
        if (append && state.isLoadingLog) return;
        if (append && !state.activityLogNextCursor) return;
        if (
            !state.activityLogFilters.levels.length
            || (state.activityLogAvailableTags.length && !state.activityLogFilters.tags.length)
        ) {
            state.activityLogEntries = [];
            state.activityLogNextCursor = null;
            state.activityLogTotalMatching = 0;
            renderActivityLog();
            return;
        }

        if (!append && state.activityLogAbortController) {
            state.activityLogAbortController.abort();
        }
        const requestId = state.activityLogRequestId + 1;
        const abortController = new AbortController();
        state.activityLogRequestId = requestId;
        state.activityLogAbortController = abortController;

        state.isLoadingLog = true;
        const refreshBtn = elements.refreshActivityLogBtn;
        const prevLabel = refreshBtn ? (refreshBtn.dataset.iconLabel || refreshBtn.title || 'Refresh Activity Log') : '';

        if (refreshBtn) {
            refreshBtn.disabled = true;
            setIconButtonLabel(refreshBtn, 'Refreshing activity log...');
        }

        if (!append) {
            elements.activityLogViewer.textContent = 'Loading system log…';
        }

        try {
            const params = new URLSearchParams({ limit: '200' });
            if (append) params.set('cursor', state.activityLogNextCursor);
            state.activityLogFilters.levels.forEach((level) => params.append('level', level));
            state.activityLogFilters.tags.forEach((tag) => params.append('tag', tag));
            if (state.activityLogFilters.query) params.set('search', state.activityLogFilters.query);

            const response = await fetch(`api/system/activity-log?${params.toString()}`, {
                signal: abortController.signal
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            if (requestId !== state.activityLogRequestId) return;
            state.activityLog = data;
            const received = Array.isArray(data.entries)
                ? data.entries.map(normalizeActivityLogEntry)
                : [];
            if (append) {
                const knownIds = new Set(state.activityLogEntries.map((entry) => entry.id).filter(Boolean));
                state.activityLogEntries.push(...received.filter((entry) => !entry.id || !knownIds.has(entry.id)));
            } else {
                state.activityLogEntries = received;
                state.activityLogTotalMatching = Number(data.total_matching || received.length);
            }
            state.activityLogNextCursor = data.next_cursor || null;
            state.activityLogAvailableTags = Array.isArray(data.available_tags) ? data.available_tags : [];
            populateActivityLogFilters();
            renderActivityLog();
        } catch (error) {
            if (error.name !== 'AbortError' && requestId === state.activityLogRequestId) {
                elements.activityLogViewer.textContent = `Failed to load activity log: ${error.message}`;
            }
        } finally {
            if (requestId !== state.activityLogRequestId) return;
            if (refreshBtn) {
                refreshBtn.disabled = false;
                setIconButtonLabel(refreshBtn, prevLabel);
            }
            state.isLoadingLog = false;
            state.activityLogAbortController = null;
        }
    }

    async function loadGeneralSettings() {
        if (!elements.settingsList || state.isLoadingSettings) return;

        state.isLoadingSettings = true;
        setStatus(elements.settingsFeedback, 'Loading settings…', 'info');

        try {
            const response = await fetch('api/system/settings/general');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            state.settings = Array.isArray(data) ? data : [];
            state.settingEditKey = null;
            state.settingDraftValue = '';
            renderSettings();
            updateImportOcrAvailability();
            setStatus(elements.settingsFeedback, '', 'info');
        } catch (error) {
            renderSettings(true);
            setStatus(elements.settingsFeedback, `Failed to load settings: ${error.message}`, 'error');
        } finally {
            state.isLoadingSettings = false;
        }
    }

    function renderSettings(emptyOnError = false) {
        if (!elements.settingsList) return;

        const query = normalizeSearchText(state.settingsFilter);
        const filteredSettings = state.settings.filter((setting) => settingMatchesFilter(setting, query));

        if (!state.settings.length || !filteredSettings.length) {
            const message = emptyOnError
                ? 'Unable to load settings.'
                : state.settings.length
                    ? 'No settings match the current filter.'
                    : 'No configurable settings found.';
            elements.settingsList.innerHTML = `
                <div class="rounded-lg border border-border-primary bg-app-card px-4 py-3 text-sm text-txt-secondary text-center shadow-sm">
                    ${escapeHtml(message)}
                </div>
            `;
            return;
        }

        const sortedSettings = [...filteredSettings].sort((a, b) =>
            String(a?.key ?? '').localeCompare(String(b?.key ?? ''), undefined, { sensitivity: 'base' })
        );

        const grouped = groupSettingsByCategory(sortedSettings);
        const cards = grouped.map(([category, settings]) => `
            <section class="space-y-2">
                <div class="text-xs font-semibold uppercase text-txt-secondary">${escapeHtml(category)}</div>
                <div class="flex flex-col gap-3">
                    ${settings.map((setting) => {
                        if (state.settingEditKey === setting.key) {
                            return renderSettingEditCard(setting);
                        }
                        return renderSettingViewCard(setting);
                    }).join('')}
                </div>
            </section>
        `).join('');

        elements.settingsList.innerHTML = cards;
    }

    function normalizeSearchText(value) {
        return String(value || '').trim().toLowerCase();
    }

    function settingMatchesFilter(setting, query) {
        if (!query) return true;
        const haystack = [
            setting.key,
            setting.value,
            setting.description,
            setting.category
        ].map((value) => String(value || '').toLowerCase()).join(' ');
        return haystack.includes(query);
    }

    function groupSettingsByCategory(settings) {
        const groups = new Map();
        settings.forEach((setting) => {
            const category = setting.category || 'Other';
            if (!groups.has(category)) {
                groups.set(category, []);
            }
            groups.get(category).push(setting);
        });
        return Array.from(groups.entries()).sort((a, b) => {
            if (a[0] === 'Other') return 1;
            if (b[0] === 'Other') return -1;
            return a[0].localeCompare(b[0], undefined, { sensitivity: 'base' });
        });
    }

    function renderSettingViewCard(setting) {
        const description = setting.description
            ? `<div class="text-xs text-txt-secondary mt-1">${escapeHtml(setting.description)}</div>`
            : '';

        return `
            <div class="setting-card rounded-lg border border-border-primary bg-app-card px-5 py-4 shadow-sm hover:shadow transition-shadow" data-setting="${escapeHtml(setting.key)}" data-mode="view" style="max-width: 1400px;">
                <div class="flex flex-col md:flex-row gap-4 md:items-center md:justify-between">
                    <div class="flex-1">
                        <div class="font-semibold text-txt-primary text-sm">${escapeHtml(setting.key)}</div>
                        ${description}
                    </div>
                    <div class="flex items-center gap-4">
                        <div class="text-sm text-txt-primary font-mono bg-app-elevated px-3 py-2 rounded border border-border-primary break-words inline-block max-w-xs">${escapeHtml(setting.value ?? '')}</div>
                        <button data-action="edit-setting" ${iconButton('edit', 'Edit setting', 'is-primary shrink-0')}>${iconSvg('edit')}</button>
                    </div>
                </div>
            </div>
        `;
    }

    function renderSettingEditCard(setting) {
        const description = setting.description
            ? `<div class="text-xs text-txt-secondary mt-1">${escapeHtml(setting.description)}</div>`
            : '';

        const draftValue = state.settingDraftValue ?? setting.value ?? '';

        return `
            <div class="setting-card rounded-lg border border-border-primary bg-app-card editing-highlight px-5 py-4 shadow-sm" data-setting="${escapeHtml(setting.key)}" data-mode="edit" style="max-width: 1400px;">
                <div class="space-y-4">
                    <div>
                        <div class="font-semibold text-txt-primary text-sm">${escapeHtml(setting.key)}</div>
                        ${description}
                    </div>
                    <div class="space-y-2">
                        <input data-field="setting-value" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent font-mono text-sm bg-app-card text-txt-primary transition-colors" value="${escapeHtml(draftValue)}" />
                        <p class="text-xs text-txt-secondary">Values are stored as plain text; lists/objects use JSON.</p>
                    </div>
                    <div class="flex justify-end gap-2">
                        <button data-action="cancel-setting" ${iconButton('circleX', 'Cancel setting edit')}>${iconSvg('circleX')}</button>
                        <button data-action="save-setting" ${iconButton('save', 'Save setting', 'is-primary')}>${iconSvg('save')}</button>
                    </div>
                </div>
            </div>
        `;
    }

    function handleSettingsTableClick(event) {
        const actionButton = event.target.closest('[data-action]');
        if (!actionButton) return;

        const card = actionButton.closest('[data-setting]');
        const settingKey = card?.dataset.setting;
        if (!settingKey) return;

        const action = actionButton.dataset.action;

        if (action === 'edit-setting') {
            startSettingEdit(settingKey);
        } else if (action === 'cancel-setting') {
            cancelSettingEdit();
        } else if (action === 'save-setting') {
            saveSettingValue(settingKey);
        }
    }

    function handleSettingsInputChange(event) {
        if (!state.settingEditKey) return;
        if (event.target.dataset.field === 'setting-value') {
            state.settingDraftValue = event.target.value;
        }
    }

    function handleSettingsFilterInput(event) {
        state.settingsFilter = event.target.value || '';
        renderSettings();
    }

    function startSettingEdit(settingKey) {
        if (state.isSavingSetting) return;

        const setting = state.settings.find((s) => s.key === settingKey);
        if (!setting) return;

        state.settingEditKey = setting.key;
        state.settingDraftValue = setting.value ?? '';
        renderSettings();
        setStatus(elements.settingsFeedback, `Editing '${setting.key}'.`, 'info');
        focusSettingInput();
    }

    function focusSettingInput() {
        requestAnimationFrame(() => {
            const input = elements.settingsList?.querySelector('[data-setting][data-mode="edit"] [data-field="setting-value"]');
            if (input) input.focus();
        });
    }

    function cancelSettingEdit(message = true) {
        state.settingEditKey = null;
        state.settingDraftValue = '';
        renderSettings();
        if (message) {
            setStatus(elements.settingsFeedback, 'Editing cancelled.', 'info');
        }
    }

    async function saveSettingValue(settingKey) {
        if (state.isSavingSetting || !state.settingEditKey) return;

        const value = state.settingDraftValue ?? '';
        state.isSavingSetting = true;
        setStatus(elements.settingsFeedback, 'Saving setting…', 'info');

        try {
            const response = await fetch(`api/system/settings/general/${encodeURIComponent(settingKey)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ value })
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            state.settings = state.settings.map((setting) => (setting.key === result.key ? result : setting));
            updateImportOcrAvailability();
            cancelSettingEdit(false);
            renderSettings();
            await notifyConfigChanged();
            const resultMessage = withRestartNotice(`Saved setting '${result.key}'.`, result);
            setStatus(elements.settingsFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.settingsFeedback, `Failed to save setting: ${error.message}`, 'error');
        } finally {
            state.isSavingSetting = false;
        }
    }

    async function loadProviders() {
        if (!elements.providerList || state.isLoadingProviders) return;

        state.isLoadingProviders = true;
        setStatus(elements.providerFeedback, 'Loading providers…', 'info');

        try {
            const response = await fetch('api/system/providers');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            state.providers = Array.isArray(data) ? data : [];
            if (state.providerEdit && state.providerEdit.mode === 'existing') {
                const stillExists = state.providers.some(provider => provider.name === state.providerEdit.key);
                if (!stillExists) {
                    state.providerEdit = null;
                    state.providerDraft = null;
                }
            }

            renderProviders();
            populateProviderOptions();
            renderModels();
            setStatus(elements.providerFeedback, '', 'info');
        } catch (error) {
            renderProviders(true);
            setStatus(elements.providerFeedback, `Failed to load providers: ${error.message}`, 'error');
        } finally {
            state.isLoadingProviders = false;
        }
    }

    function renderProviders(emptyOnError = false) {
        if (!elements.providerList) return;

        const cards = [];
        const editing = state.providerEdit;
        const draft = state.providerDraft || {};

        if (editing && editing.mode === 'new') {
            cards.push(renderProviderEditCard(draft, { isNew: true }));
        }

        if (!state.providers.length) {
            const message = emptyOnError
                ? 'Unable to load providers.'
                : 'No custom providers configured.';
            cards.push(`
                <div class="rounded-lg border border-border-primary bg-app-card px-4 py-3 text-sm text-txt-secondary text-center shadow-sm">
                    ${escapeHtml(message)}
                </div>
            `);
            elements.providerList.innerHTML = cards.join('');
            focusProviderInput();
            return;
        }

        state.providers.forEach((provider) => {
            if (editing && editing.mode === 'existing' && editing.key === provider.name) {
                cards.push(renderProviderEditCard(draft, { isNew: false }));
                return;
            }
            const editable = provider.user_editable === true;
            const isBuiltIn = isBuiltInProviderName(provider.name);
            const isOpenAi = provider.name === 'openai';

            const apiKeyDisplay = provider.api_key
                ? provider.api_key_has_value
                    ? `<div class="flex items-center gap-2">
                        <div class="w-fit"><span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-success border">${'Set'}</span></div>
                        <div class="text-xs text-txt-secondary font-mono">${escapeHtml(provider.api_key)}</div>
                    </div>`
                    : `<div class="flex items-center gap-2">
                        <div class="w-fit"><span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-error border">Not set</span></div>
                        <div class="text-xs state-error">Configure ${escapeHtml(provider.api_key)}</div>
                    </div>`
                : `<div class="flex items-center gap-2"><span class="text-sm text-txt-secondary">No key required</span></div>`;

            const baseUrlDisplay = provider.base_url
                ? provider.base_url_has_value
                    ? `<div class="flex items-center gap-2">
                        <div class="w-fit"><span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-success border">Set</span></div>
                        <div class="text-xs text-txt-secondary font-mono">${escapeHtml(provider.base_url)}</div>
                    </div>`
                    : `<div class="flex items-center gap-2">
                        <div class="w-fit"><span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-error border">Not set</span></div>
                        <div class="text-xs state-error">Configure ${escapeHtml(provider.base_url)}</div>
                    </div>`
                : `<div class="flex items-center gap-2"><span class="text-sm text-txt-secondary">No base URL configured</span></div>`;

            const actions = [
                editable
                    ? `<button data-action="edit" data-provider="${escapeHtml(provider.name)}" ${iconButton('edit', 'Edit provider', 'is-primary')}>${iconSvg('edit')}</button>`
                    : '',
                editable && !isBuiltIn
                    ? `<button data-action="delete" data-provider="${escapeHtml(provider.name)}" ${iconButton('trash', 'Delete provider', 'is-danger')}>${iconSvg('trash')}</button>`
                    : '',
            ].join('');

            const providerMeta = isBuiltIn
                ? '<div class="text-xs text-txt-secondary mt-0.5">Built-in provider</div>'
                : '';
            const openAiOAuthPanel = isOpenAi ? renderOpenAiOAuthPanel(provider) : '';

            cards.push(`
                <div class="provider-card rounded-lg border border-border-primary bg-app-card px-5 py-4 shadow-sm hover:shadow transition-shadow" data-provider-row="${escapeHtml(provider.name)}" style="max-width: 1400px;">
                    <div class="space-y-4">
                        <div class="flex items-center justify-between gap-4">
                            <div>
                                <div class="font-semibold text-txt-primary text-sm">${escapeHtml(provider.name)}</div>
                                ${providerMeta}
                            </div>
                            <div class="flex gap-2 shrink-0">
                                ${actions}
                            </div>
                        </div>
                        <div class="grid gap-6 md:grid-cols-2">
                            <div>
                                <div class="text-xs font-medium text-txt-secondary mb-2">API Key</div>
                                <div>${apiKeyDisplay}</div>
                            </div>
                            <div>
                                <div class="text-xs font-medium text-txt-secondary mb-2">Base URL</div>
                                <div>${baseUrlDisplay}</div>
                            </div>
                        </div>
                        ${openAiOAuthPanel}
                    </div>
                </div>
            `);
        });

        elements.providerList.innerHTML = cards.join('');
        focusProviderInput();
    }

    function populateProviderOptions() {
        // provider options are built per-row during render; nothing to do here.
    }

    function isBuiltInProviderName(name) {
        return BUILT_IN_PROVIDER_NAMES.has(String(name || '').toLowerCase());
    }

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

    function formatDateTime(value) {
        if (!value) return '';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleString();
    }

    function renderProviderEditCard(draft, { isNew }) {
        const rowKey = isNew ? '__new' : (state.providerEdit?.key || draft.name || '');
        const nameReadonly = isNew ? '' : 'readonly';
        const nameHelp = isNew
            ? 'Provider names identify custom endpoints used by models.'
            : 'Provider names are identities and cannot be renamed here.';
        const isOpenAiDraft = !isNew && draft.name === 'openai';
        const openAiAuthControls = isOpenAiDraft
            ? `
                <div class="rounded-lg border border-border-secondary bg-app-elevated px-4 py-3">
                    <div class="grid gap-4 md:grid-cols-2">
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Auth Mode</label>
                            <select data-provider-field="auth_mode" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors">
                                <option value="api_key" ${(draft.auth_mode || 'api_key') === 'api_key' ? 'selected' : ''}>API key</option>
                                <option value="oauth" ${draft.auth_mode === 'oauth' ? 'selected' : ''}>OAuth</option>
                            </select>
                            <p class="text-xs text-txt-secondary mt-1">OAuth requires openai_oauth_enabled and a connected account.</p>
                            ${draft.auth_mode === 'oauth' ? renderOpenAiOAuthUnsupportedWarning('mt-2') : ''}
                        </div>
                        <label class="flex items-start gap-3 rounded-md border border-border-secondary px-3 py-2">
                            <input data-provider-field="oauth_api_key_fallback_enabled" type="checkbox" class="mt-1 h-4 w-4 rounded border-border-secondary text-accent focus:ring-accent" ${draft.oauth_api_key_fallback_enabled ? 'checked' : ''} />
                            <span>
                                <span class="block text-xs font-medium text-txt-primary">Allow API key fallback</span>
                                <span class="block text-xs text-txt-secondary mt-1">Only use the API key when OAuth cannot be used.</span>
                            </span>
                        </label>
                    </div>
                </div>
            `
            : '';
        return `
            <div class="provider-card rounded-lg border border-border-primary bg-app-card editing-highlight px-5 py-4 shadow-sm" data-provider-row="${escapeHtml(rowKey)}" data-mode="edit" style="max-width: 1400px;">
                <div class="space-y-4">
                    <div class="grid gap-4 md:grid-cols-3">
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Provider Name</label>
                            <input data-provider-field="name" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="e.g. local-ollama" value="${escapeHtml(draft.name || '')}" ${nameReadonly} />
                            <p class="text-xs text-txt-secondary mt-1">${nameHelp}</p>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">API Key Secret</label>
                            <input data-provider-field="api_key" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="SECRET_NAME (optional)" value="${escapeHtml(draft.api_key || '')}" />
                            <p class="text-xs text-txt-secondary mt-1">Enter the secret name; set the value in Secrets.</p>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Base URL Secret</label>
                            <input data-provider-field="base_url" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="SECRET_NAME (optional)" value="${escapeHtml(draft.base_url || '')}" />
                            <p class="text-xs text-txt-secondary mt-1">Store base URLs as secrets; enter the secret name here.</p>
                        </div>
                    </div>
                    ${openAiAuthControls}
                    <div class="flex justify-end gap-2">
                        <button data-action="cancel-provider" ${iconButton('circleX', 'Cancel provider edit')}>${iconSvg('circleX')}</button>
                        <button data-action="save-provider" ${iconButton('save', 'Save provider', 'is-primary')}>${iconSvg('save')}</button>
                    </div>
                </div>
            </div>
        `;
    }

    function focusProviderInput(field = 'name') {
        if (!state.providerEdit) return;
        requestAnimationFrame(() => {
            const editableName = state.providerEdit?.mode === 'new';
            const targetField = editableName ? field : (field === 'name' ? 'api_key' : field);
            const el = elements.providerList?.querySelector(`[data-provider-row][data-mode="edit"] [data-provider-field="${targetField}"]`);
            if (el instanceof HTMLInputElement) {
                el.focus();
                el.select();
            }
        });
    }

    async function loadModels() {
        if (!elements.modelList || state.isLoadingModels) return;

        state.isLoadingModels = true;
        setStatus(elements.modelFeedback, 'Loading models…', 'info');

        try {
            const response = await fetch('api/system/models');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            state.models = Array.isArray(data) ? data : [];

            if (state.modelEdit && state.modelEdit.mode === 'existing') {
                const stillExists = state.models.some(m => m.name === state.modelEdit.key);
                if (!stillExists) {
                    state.modelEdit = null;
                    state.modelDraft = null;
                }
            }

            renderModels();
            setStatus(elements.modelFeedback, '', 'info');
        } catch (error) {
            renderModels(true);
            setStatus(elements.modelFeedback, `Failed to load models: ${error.message}`, 'error');
        } finally {
            state.isLoadingModels = false;
        }
    }

    function renderModels(emptyOnError = false) {
        if (!elements.modelList) return;

        const cards = [];
        const editing = state.modelEdit;
        const draft = state.modelDraft || {};

        if (editing && editing.mode === 'new') {
            cards.push(renderModelEditCard(draft, { isNew: true }));
        }

        if (!state.models.length) {
            const message = emptyOnError ? 'Unable to load model mappings.' : 'No models configured.';
            cards.push(`
                <div class="rounded-lg border border-border-primary bg-app-card px-4 py-3 text-sm text-txt-secondary text-center shadow-sm">
                    ${escapeHtml(message)}
                </div>
            `);
        } else {
            state.models.forEach((model) => {
                if (editing && editing.mode === 'existing' && editing.key === model.name) {
                    cards.push(renderModelEditCard(draft, { isNew: false }));
                } else {
                    cards.push(renderModelViewCard(model));
                }
            });
        }

        elements.modelList.innerHTML = cards.join('');
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

    function normalizeActivityLogEntry(record) {
        const data = record && typeof record.data === 'object' && record.data !== null ? record.data : {};
        return {
            id: record.id || '',
            timestamp: record.timestamp || '',
            level: String(record.level || '').toLowerCase(),
            tag: record.tag || '',
            message: record.message || '',
            data,
            bootId: record.boot_id ?? null
        };
    }

    function handleActivityLogFilterChange(event) {
        state.activityLogFilters = {
            query: (elements.activityLogSearch?.value || '').trim().toLowerCase(),
            levels: getCheckedActivityLogFilterValues(elements.activityLogLevelOptions),
            tags: getCheckedActivityLogFilterValues(elements.activityLogTagOptions),
            latestFirst: Boolean(elements.activityLogLatestFirst?.checked)
        };
        updateActivityLogFilterSummaries();
        if (event?.target === elements.activityLogLatestFirst) {
            renderActivityLog();
            return;
        }
        if (!state.activityLogFilters.levels.length || (state.activityLogAvailableTags.length && !state.activityLogFilters.tags.length)) {
            state.activityLogEntries = [];
            state.activityLogNextCursor = null;
            state.activityLogTotalMatching = 0;
            renderActivityLog();
            return;
        }
        refreshActivityLog();
    }

    function handleActivityLogSearchInput() {
        state.activityLogFilters.query = (elements.activityLogSearch?.value || '').trim().toLowerCase();
        if (activityLogSearchTimer) window.clearTimeout(activityLogSearchTimer);
        activityLogSearchTimer = window.setTimeout(() => refreshActivityLog(), 300);
    }

    function populateActivityLogFilters() {
        renderActivityLogLevelOptions();
        renderActivityLogTagOptions();
        updateActivityLogFilterSummaries();
    }

    function renderActivityLogLevelOptions() {
        if (!elements.activityLogLevelOptions) return;

        const levels = ACTIVITY_LOG_LEVELS;
        const selected = new Set(Array.isArray(state.activityLogFilters.levels) ? state.activityLogFilters.levels : levels);
        elements.activityLogLevelOptions.innerHTML = levels
            .map((level) => renderActivityLogCheckbox('level', level, formatActivityLogLevelLabel(level), selected.has(level)))
            .join('');
        state.activityLogFilters.levels = getCheckedActivityLogFilterValues(elements.activityLogLevelOptions);
    }

    function renderActivityLogTagOptions() {
        if (!elements.activityLogTagOptions) return;

        const previousOptions = Array.from(elements.activityLogTagOptions.querySelectorAll('input[type="checkbox"]')).map((input) => input.value);
        const previousSelected = getCheckedActivityLogFilterValues(elements.activityLogTagOptions);
        const previousWasAll = previousOptions.length === 0 || previousSelected.length === previousOptions.length;
        const tags = [...new Set(state.activityLogAvailableTags)].sort();
        const selected = previousWasAll ? new Set(tags) : new Set(previousSelected.filter((tag) => tags.includes(tag)));

        elements.activityLogTagOptions.innerHTML = tags.length
            ? tags.map((tag) => renderActivityLogCheckbox('tag', tag, tag, selected.has(tag))).join('')
            : '<div class="tool-dropdown-menu-header">No tags loaded.</div>';
        state.activityLogFilters.tags = getCheckedActivityLogFilterValues(elements.activityLogTagOptions);
    }

    function renderActivityLogCheckbox(group, value, label, checked) {
        const id = `activity-log-${group}-${value.replace(/[^a-z0-9_-]/gi, '-')}`;
        return `
            <div class="tool-checkbox-wrapper">
                <label for="${escapeHtml(id)}">
                    <input id="${escapeHtml(id)}" type="checkbox" value="${escapeHtml(value)}" ${checked ? 'checked' : ''}>
                    <span class="tool-checkbox-name">${escapeHtml(label)}</span>
                </label>
            </div>
        `;
    }

    function getCheckedActivityLogFilterValues(container) {
        return Array.from(container?.querySelectorAll('input[type="checkbox"]:checked') || [])
            .map((input) => input.value);
    }

    function updateActivityLogFilterSummaries() {
        updateActivityLogFilterSummary(
            elements.activityLogLevelSummary,
            state.activityLogFilters.levels,
            ACTIVITY_LOG_LEVELS,
            'All levels',
            'No levels'
        );
        const allTags = [...new Set(state.activityLogAvailableTags)].sort();
        updateActivityLogFilterSummary(
            elements.activityLogTagSummary,
            state.activityLogFilters.tags,
            allTags,
            'All tags',
            'No tags'
        );
    }

    function updateActivityLogFilterSummary(element, selectedValues, allValues, allLabel, noneLabel) {
        if (!element) return;
        if (!allValues.length || !selectedValues.length) {
            element.textContent = noneLabel;
            return;
        }
        if (selectedValues.length === allValues.length) {
            element.textContent = allLabel;
            return;
        }
        element.textContent = `${selectedValues.length} selected`;
    }

    function formatActivityLogLevelLabel(level) {
        return {
            error: 'Error',
            critical: 'Critical',
            fatal: 'Fatal',
            warning: 'Warning',
            warn: 'Warn',
            info: 'Info',
            debug: 'Debug'
        }[level] || level;
    }

    function setActivityLogFilterMenuOpen(menu, open) {
        state.activityLogFilterMenus[menu] = Boolean(open);
        if (open) {
            const otherMenu = menu === 'level' ? 'tag' : 'level';
            state.activityLogFilterMenus[otherMenu] = false;
            const otherDropdown = otherMenu === 'level' ? elements.activityLogLevelDropdown : elements.activityLogTagDropdown;
            const otherTrigger = otherMenu === 'level' ? elements.activityLogLevelTrigger : elements.activityLogTagTrigger;
            const otherPanel = otherMenu === 'level' ? elements.activityLogLevelMenu : elements.activityLogTagMenu;
            otherDropdown?.classList.remove('open');
            otherPanel?.classList.add('hidden');
            otherTrigger?.setAttribute('aria-expanded', 'false');
        }
        const dropdown = menu === 'level' ? elements.activityLogLevelDropdown : elements.activityLogTagDropdown;
        const trigger = menu === 'level' ? elements.activityLogLevelTrigger : elements.activityLogTagTrigger;
        const panel = menu === 'level' ? elements.activityLogLevelMenu : elements.activityLogTagMenu;

        dropdown?.classList.toggle('open', state.activityLogFilterMenus[menu]);
        panel?.classList.toggle('hidden', !state.activityLogFilterMenus[menu]);
        trigger?.setAttribute('aria-expanded', state.activityLogFilterMenus[menu] ? 'true' : 'false');
    }

    function handleActivityLogDocumentClick(event) {
        const target = event.target;
        if (!(target instanceof Node)) return;

        if (state.activityLogFilterMenus.level && !elements.activityLogLevelDropdown?.contains(target)) {
            setActivityLogFilterMenuOpen('level', false);
        }
        if (state.activityLogFilterMenus.tag && !elements.activityLogTagDropdown?.contains(target)) {
            setActivityLogFilterMenuOpen('tag', false);
        }
    }

    function renderActivityLog() {
        if (!elements.activityLogViewer) return;

        const entries = getFilteredActivityLogEntries();
        if (elements.activityLogCount) {
            const total = state.activityLogTotalMatching;
            const shown = entries.length;
            const earliest = state.activityLog?.earliest_retained_timestamp
                ? ` Retained since ${formatActivityLogTimestamp(state.activityLog.earliest_retained_timestamp)}.`
                : '';
            elements.activityLogCount.textContent = `${shown} of ${total} matching entries loaded.${earliest}`;
        }
        elements.activityLogLoadOlderBtn?.classList.toggle('hidden', !state.activityLogNextCursor);

        if (!state.activityLogEntries.length) {
            elements.activityLogViewer.innerHTML = '<div class="p-3 text-txt-secondary">No activity log entries loaded.</div>';
            return;
        }

        if (!entries.length) {
            elements.activityLogViewer.innerHTML = '<div class="p-3 text-txt-secondary">No entries match the current filters.</div>';
            return;
        }

        elements.activityLogViewer.innerHTML = entries.map(renderActivityLogEntry).join('');
        elements.activityLogViewer.scrollTop = state.activityLogFilters.latestFirst ? 0 : elements.activityLogViewer.scrollHeight;
    }

    function getFilteredActivityLogEntries() {
        const filters = state.activityLogFilters;
        let entries = state.activityLogEntries;

        if (!filters.latestFirst) {
            entries = [...entries].reverse();
        }
        return entries;
    }

    function renderActivityLogEntry(entry) {
        const levelClass = activityLogLevelClass(entry.level);
        const time = formatActivityLogTimestamp(entry.timestamp);
        const dataSummary = summarizeActivityLogData(entry.data, entry.bootId);

        return `
            <div class="activity-log-row ${levelClass}">
                <div class="text-txt-secondary">${escapeHtml(time || 'unknown time')}</div>
                <div class="font-semibold uppercase">${escapeHtml(entry.level || 'raw')}</div>
                <div class="text-txt-primary">${escapeHtml(entry.tag || 'unstructured')}</div>
                <div>
                    <div class="text-txt-primary">${escapeHtml(entry.message)}</div>
                    ${dataSummary ? `<div class="activity-log-meta">${dataSummary}</div>` : ''}
                </div>
            </div>
        `;
    }

    function activityLogLevelClass(level) {
        if (level === 'error' || level === 'critical' || level === 'fatal') return 'log-level-error';
        if (level === 'warning' || level === 'warn') return 'log-level-warn';
        if (level === 'info') return 'log-level-info';
        if (level === 'debug') return 'log-level-debug';
        return '';
    }

    function formatActivityLogTimestamp(timestamp) {
        if (!timestamp) return '';
        const date = new Date(timestamp);
        if (Number.isNaN(date.getTime())) return timestamp;
        return date.toLocaleString(undefined, {
            month: 'short',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    }

    function summarizeActivityLogData(data, bootId) {
        const fields = [];
        if (data && typeof data === 'object') {
            Object.entries(data).forEach(([key, value]) => {
                if (value === undefined || value === null || value === '') return;
                fields.push([key, value]);
            });
        }
        if (bootId !== null && bootId !== undefined) {
            fields.push(['boot_id', bootId]);
        }

        return fields
            .map(([key, value]) => {
                const rendered = typeof value === 'string' ? value : JSON.stringify(value);
                return `<span>${escapeHtml(key)}=${escapeHtml(truncateActivityLogValue(rendered))}</span>`;
            })
            .join('');
    }

    function truncateActivityLogValue(value, limit = 140) {
        const text = String(value ?? '');
        return text.length > limit ? `${text.slice(0, limit)}...` : text;
    }

    function buildProviderOptions(selected, { embeddingOnly = false } = {}) {
        const providers = embeddingOnly
            ? state.providers.filter((provider) => provider.name === 'openai')
            : state.providers;
        if (!providers.length) {
            return '<option value="">No providers available</option>';
        }

        return providers.map((provider) => {
            const isSelected = provider.name === selected ? 'selected' : '';
            const label = provider.user_editable === false
                ? `${provider.name} (built-in)`
                : provider.name;
            return `<option value="${escapeHtml(provider.name)}" ${isSelected}>${escapeHtml(label)}</option>`;
        }).join('');
    }

    function capabilitiesIncludeEmbedding(capabilities) {
        const values = Array.isArray(capabilities)
            ? capabilities
            : String(capabilities || '').split(',');
        return values.some((item) => String(item).trim().toLowerCase() === 'embedding');
    }

    function renderEmbeddingProviderNotice() {
        return `
            <p class="text-xs state-warning mt-1">
                Embedding models currently support only the OpenAI provider.
            </p>
        `;
    }

    function renderModelViewCard(model) {
        const editable = model.user_editable !== false;
        const availabilityBadge = model.available
            ? '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-success border">Available</span>'
            : '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-error border">Unavailable</span>';

        const reasonLine = model.status_message && !model.available
            ? `<div class="text-xs state-error mt-1">${escapeHtml(model.status_message)}</div>`
            : '';
        const capabilities = Array.isArray(model.capabilities) && model.capabilities.length
            ? model.capabilities.join(', ')
            : 'text';
        const embeddingNotice = capabilitiesIncludeEmbedding(model.capabilities)
            ? renderEmbeddingProviderNotice()
            : '';

        const actions = editable
            ? `
                <button data-action="edit" data-model="${escapeHtml(model.name)}" ${iconButton('edit', 'Edit model', 'is-primary')}>${iconSvg('edit')}</button>
                <button data-action="delete" data-model="${escapeHtml(model.name)}" ${iconButton('trash', 'Delete model', 'is-danger')}>${iconSvg('trash')}</button>
            `
            : `<span class="inline-block px-2 py-0.5 text-xs text-txt-secondary bg-app-elevated rounded border border-border-primary">Read-only</span>`;

        return `
            <div class="model-card rounded-lg border border-border-primary bg-app-card px-5 py-4 shadow-sm hover:shadow transition-shadow" data-row="${escapeHtml(model.name)}" data-mode="view" style="max-width: 1400px;">
                <div class="space-y-4">
                    <div class="flex items-start justify-between gap-4">
                        <div class="min-w-0">
                            <div class="font-semibold text-txt-primary text-sm">${escapeHtml(model.name)}</div>
                            <div class="flex items-center gap-2 mt-1">
                                <div class="w-fit">${availabilityBadge}</div>
                            </div>
                            ${reasonLine}
                        </div>
                        <div class="flex gap-2 shrink-0">
                            ${actions}
                        </div>
                    </div>
                    <div class="flex gap-6 items-start flex-wrap">
                        <div>
                            <div class="text-xs font-medium text-txt-secondary mb-1">Provider</div>
                            <div class="text-sm text-txt-primary">${escapeHtml(model.provider)}</div>
                            ${embeddingNotice}
                        </div>
                        <div>
                            <div class="text-xs font-medium text-txt-secondary mb-1">Model Identifier</div>
                            <div class="text-xs font-mono text-txt-primary bg-app-elevated px-2 py-1 rounded border border-border-primary inline-block max-w-xs break-words">${escapeHtml(model.model_string)}</div>
                        </div>
                        <div>
                            <div class="text-xs font-medium text-txt-secondary mb-1">Capabilities</div>
                            <div class="text-xs text-txt-primary bg-app-elevated px-2 py-1 rounded border border-border-primary inline-block max-w-xs break-words">${escapeHtml(capabilities)}</div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    function renderModelEditCard(draft, { isNew }) {
        const rowKey = isNew ? '__new' : (state.modelEdit?.key || draft.name || '');
        const isEmbeddingModel = capabilitiesIncludeEmbedding(draft.capabilities || 'text');
        const selectedProvider = isEmbeddingModel
            ? 'openai'
            : draft.provider !== undefined ? draft.provider : (state.providers[0]?.name || '');
        const providerOptions = buildProviderOptions(selectedProvider, { embeddingOnly: isEmbeddingModel });

        const providerDisabled = state.providers.length === 0 ? 'disabled' : '';
        const providerHelp = isEmbeddingModel
            ? 'Only the OpenAI provider is supported for embedding model aliases.'
            : state.providers.length ? 'Select the provider powering this model.' : 'Add a provider before creating models.';
        const embeddingNotice = isEmbeddingModel ? renderEmbeddingProviderNotice() : '';

        const renameHint = isNew
            ? '<p class="text-xs text-txt-secondary mt-1">Lowercase alias used in assistant files.</p>'
            : '<p class="text-xs text-txt-secondary mt-1">Renaming updates the model alias used in assistants.</p>';

        return `
            <div class="model-card rounded-lg border border-border-primary bg-app-card editing-highlight px-5 py-4 shadow-sm" data-row="${escapeHtml(rowKey)}" data-mode="edit" style="max-width: 1400px;">
                <div class="space-y-4">
                    <div class="grid gap-4 md:grid-cols-2">
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Model Name</label>
                            <input data-field="name" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="e.g. planning" value="${escapeHtml(draft.name || '')}" />
                            ${renameHint}
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Provider</label>
                            <select data-field="provider" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" ${providerDisabled}>
                                ${providerOptions}
                            </select>
                            <p class="text-xs text-txt-secondary mt-1">${providerHelp}</p>
                            ${embeddingNotice}
                        </div>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-txt-primary mb-1.5">Model Identifier</label>
                        <input data-field="model_string" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary font-mono text-sm transition-colors" placeholder="e.g. claude-sonnet-4-5" value="${escapeHtml(draft.model_string || '')}" />
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-txt-primary mb-1.5">Capabilities</label>
                        <input data-field="capabilities" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="e.g. text, vision" value="${escapeHtml(draft.capabilities || 'text')}" />
                        <p class="text-xs text-txt-secondary mt-1">Comma-separated values. Example: <code>text, vision</code>.</p>
                    </div>
                    <div class="flex justify-end gap-2">
                        <button data-action="cancel-model" ${iconButton('circleX', 'Cancel model edit')}>${iconSvg('circleX')}</button>
                        <button data-action="save-model" ${iconButton('save', 'Save model', 'is-primary')}>${iconSvg('save')}</button>
                    </div>
                </div>
            </div>
        `;
    }

    function focusModelInput(field) {
        requestAnimationFrame(() => {
            const el = elements.modelList?.querySelector(`[data-row][data-mode="edit"] [data-field="${field}"]`);
            if (el) {
                el.focus();
            }
        });
    }

    function handleModelTableClick(event) {
        const actionButton = event.target.closest('[data-action]');
        if (!actionButton) return;

        const rowEl = actionButton.closest('[data-row]');
        const rowKey = rowEl?.dataset.row;
        const action = actionButton.dataset.action;

        if (action === 'edit' && rowKey) {
            startModelEdit(rowKey);
        } else if (action === 'delete' && rowKey) {
            deleteModel(rowKey);
        } else if (action === 'cancel-model') {
            cancelModelEdit();
        } else if (action === 'save-model' && rowKey) {
            saveModelRow(rowKey);
        }
    }

function handleModelInputChange(event) {
    if (!state.modelEdit || !event.target.dataset.field) {
        return;
    }
    const field = event.target.dataset.field;
    if (!state.modelDraft) {
        state.modelDraft = {};
    }
    const wasEmbeddingModel = capabilitiesIncludeEmbedding(state.modelDraft.capabilities || 'text');
    if (field === 'name') {
        const normalized = event.target.value.trim().toLowerCase();
        state.modelDraft[field] = normalized;
        if (event.target.value !== normalized) {
            event.target.value = normalized;
        }
    } else {
        state.modelDraft[field] = event.target.value;
    }
    const isEmbeddingModel = capabilitiesIncludeEmbedding(state.modelDraft.capabilities);
    if (field === 'capabilities' && !wasEmbeddingModel && isEmbeddingModel) {
        state.modelDraft.provider = 'openai';
    }
    if (field === 'capabilities' && wasEmbeddingModel !== isEmbeddingModel) {
        renderModels();
        focusModelInput('capabilities');
    }
}

function startModelEdit(modelName) {
    if (state.isSavingModel) return;
    if (state.modelEdit && state.modelEdit.mode === 'new') {
        setStatus(elements.modelFeedback, 'Finish creating the new model before editing another.', 'warning');
        return;
    }

    const model = state.models.find(m => m.name === modelName);
    if (!model || model.user_editable === false) {
        setStatus(elements.modelFeedback, 'This model is read-only.', 'warning');
        return;
    }

    state.modelEdit = { mode: 'existing', key: model.name };
    state.modelDraft = {
        name: model.name,
        provider: model.provider,
        model_string: model.model_string,
        capabilities: Array.isArray(model.capabilities) && model.capabilities.length
            ? model.capabilities.join(', ')
            : 'text'
    };

    renderModels();
    focusModelInput('name');
    setStatus(elements.modelFeedback, `Editing '${model.name}'.`, 'info');
}

function startNewModel() {
    if (state.isSavingModel) return;
    if (state.modelEdit) {
        setStatus(elements.modelFeedback, 'Finish editing the current row before adding a new model.', 'warning');
        return;
    }

    const defaultProvider = state.providers[0]?.name || '';
    state.modelEdit = { mode: 'new', key: '__new' };
    state.modelDraft = {
        name: '',
        provider: defaultProvider,
        model_string: '',
        capabilities: 'text'
    };

    renderModels();
    focusModelInput('name');
    setStatus(elements.modelFeedback, 'Enter details for the new model row and click Save.', 'info');
}

function cancelModelEdit(message = true) {
    state.modelEdit = null;
    state.modelDraft = null;
    renderModels();
    if (message) {
        setStatus(elements.modelFeedback, 'Editing cancelled.', 'info');
    }
}

async function saveModelRow(rowKey) {
    if (state.isSavingModel || !state.modelEdit || !state.modelDraft) return;

    const draft = state.modelDraft;
    let alias = (draft.name || '').trim().toLowerCase();
    const provider = (draft.provider || '').trim();
    const modelString = (draft.model_string || '').trim();
    const capabilitiesInput = (draft.capabilities || '').trim();
    const capabilities = capabilitiesInput
        .split(',')
        .map((item) => item.trim().toLowerCase())
        .filter((item) => item.length > 0);

    const isNew = state.modelEdit.mode === 'new' || rowKey === '__new';
    const originalName = state.modelEdit.mode === 'existing' ? state.modelEdit.key : null;

    if (!alias) {
        setStatus(elements.modelFeedback, 'Model name is required.', 'error');
        return;
    }

    if (!provider || !modelString) {
        setStatus(elements.modelFeedback, 'Provider and model identifier are required.', 'error');
        return;
    }
    if (!capabilities.length) {
        setStatus(elements.modelFeedback, 'At least one capability is required (e.g. text).', 'error');
        return;
    }
    if (capabilities.includes('embedding') && provider !== 'openai') {
        setStatus(elements.modelFeedback, 'Embedding models currently support only the OpenAI provider.', 'error');
        return;
    }

    if ((isNew || alias !== originalName) && state.models.some(m => m.name === alias)) {
        setStatus(elements.modelFeedback, `Model '${alias}' already exists.`, 'error');
        return;
    }

    state.modelDraft.name = alias;
    state.isSavingModel = true;
    setStatus(elements.modelFeedback, 'Saving model…', 'info');

    try {
        const payload = {
            provider: provider,
            model_string: modelString,
            capabilities: capabilities
        };

        const response = await fetch(`api/system/models/${encodeURIComponent(alias)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const errorData = await safeJson(response);
            throw new Error(errorData?.message || `HTTP ${response.status}`);
        }

        const result = await response.json();
        let restartRequired = Boolean(result && result.restart_required);

        if (originalName && alias !== originalName) {
            const deleteResponse = await fetch(`api/system/models/${encodeURIComponent(originalName)}`, {
                method: 'DELETE'
            });
            if (!deleteResponse.ok) {
                const deleteError = await safeJson(deleteResponse);
                throw new Error(deleteError?.message || `Failed to remove old alias '${originalName}'.`);
            }

            const deleteResult = await safeJson(deleteResponse);
            restartRequired = restartRequired || Boolean(deleteResult && deleteResult.restart_required);
        }

        cancelModelEdit(false);
        await loadModels();
        await notifyConfigChanged();
        const resultMessage = withRestartNotice(`Saved model '${alias}'.`, { restart_required: restartRequired });
        setStatus(elements.modelFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
    } catch (error) {
        setStatus(elements.modelFeedback, `Failed to save model: ${error.message}`, 'error');
    } finally {
        state.isSavingModel = false;
    }
}

    async function deleteModel(modelName) {
        if (!window.confirm(`Delete model '${modelName}'?`)) return;

        try {
            const response = await fetch(`api/system/models/${encodeURIComponent(modelName)}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const apiResult = await response.json();

            if (state.modelEdit && state.modelEdit.key === modelName) {
                cancelModelEdit(false);
            }
            await loadModels();
            await notifyConfigChanged();
            const resultMessage = withRestartNotice(`Removed model '${modelName}'.`, apiResult);
            setStatus(elements.modelFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.modelFeedback, `Failed to delete model: ${error.message}`, 'error');
        }
    }

    async function handleProviderTableClick(event) {
        const actionButton = event.target.closest('[data-action]');
        if (!actionButton) return;

        const providerName = actionButton.getAttribute('data-provider');
        const action = actionButton.dataset.action;

        if (action === 'edit') {
            startProviderEdit(providerName);
        } else if (action === 'delete') {
            await deleteProvider(providerName);
        } else if (action === 'cancel-provider') {
            cancelProviderEdit();
        } else if (action === 'save-provider') {
            await saveProviderRow(actionButton);
        } else if (action === 'openai-oauth-start') {
            await startOpenAiOAuth(actionButton);
        } else if (action === 'openai-oauth-device-start') {
            await startOpenAiOAuthDevice(actionButton);
        } else if (action === 'openai-oauth-device-check') {
            await checkOpenAiOAuthDevice(actionButton);
        } else if (action === 'openai-oauth-complete') {
            await completeOpenAiOAuth(actionButton);
        } else if (action === 'openai-oauth-disconnect') {
            await disconnectOpenAiOAuth(actionButton);
        }
    }

    function handleProviderInputChange(event) {
        const target = event.target;
        if (target instanceof HTMLInputElement && target.matches('[data-openai-oauth-paste]')) {
            state.openAiOauthPaste = target.value;
            const completeButton = elements.providerList?.querySelector('[data-action="openai-oauth-complete"]');
            if (completeButton instanceof HTMLButtonElement) {
                completeButton.disabled = state.isOpenAiOauthBusy || !target.value.trim();
            }
            return;
        }
        if (!state.providerEdit || !target.dataset.providerField) return;
        if (!(target instanceof HTMLInputElement) && !(target instanceof HTMLSelectElement)) return;

        if (!state.providerDraft) {
            state.providerDraft = {};
        }
        if (target instanceof HTMLInputElement && target.type === 'checkbox') {
            state.providerDraft[target.dataset.providerField] = target.checked;
        } else {
            state.providerDraft[target.dataset.providerField] = target.value;
        }
    }

    function startNewProvider() {
        if (state.providerEdit) {
            setStatus(elements.providerFeedback, 'Finish editing the current provider before adding another.', 'warning');
            return;
        }
        state.providerEdit = { mode: 'new', key: '__new' };
        state.providerDraft = {
            name: '',
            api_key: '',
            base_url: ''
        };
        renderProviders();
        setStatus(elements.providerFeedback, 'Enter details for the new provider and click Save.', 'info');
        focusProviderInput('name');
    }

    function startProviderEdit(providerName) {
        if (state.providerEdit) {
            setStatus(elements.providerFeedback, 'Finish editing the current provider before editing another.', 'warning');
            return;
        }
        const provider = state.providers.find(p => p.name === providerName);
        if (!provider) return;

        if (provider.user_editable === false) {
            setStatus(elements.providerFeedback, 'Built-in providers cannot be edited.', 'warning');
            return;
        }

        state.providerEdit = { mode: 'existing', key: provider.name };
        state.providerDraft = {
            name: provider.name,
            api_key: provider.api_key || '',
            base_url: provider.base_url || '',
            auth_mode: provider.configured_auth_mode || 'api_key',
            oauth_api_key_fallback_enabled: provider.oauth_api_key_fallback_enabled === true
        };
        renderProviders();
        setStatus(elements.providerFeedback, `Editing '${provider.name}'.`, 'info');
        focusProviderInput('api_key');
    }

    function cancelProviderEdit(showStatus = true) {
        state.providerEdit = null;
        state.providerDraft = null;
        renderProviders();
        if (showStatus) {
            setStatus(elements.providerFeedback, 'Editing cancelled.', 'info');
        }
    }

    async function saveProviderRow(button) {
        if (state.isSavingProvider || !state.providerEdit || !state.providerDraft) return;

        const draft = state.providerDraft;
        const name = (draft.name || '').trim();
        const apiKeyInput = (draft.api_key || '').trim();
        const baseUrlInput = (draft.base_url || '').trim();

        if (!name) {
            setStatus(elements.providerFeedback, 'Provider name is required.', 'error');
            return;
        }

        if (baseUrlInput && baseUrlInput.includes('://')) {
            setStatus(elements.providerFeedback, 'Base URL must reference a secret name; store the actual URL via the Secrets form.', 'error');
            return;
        }

        state.isSavingProvider = true;
        if (button) {
            button.disabled = true;
            setIconButtonLabel(button, 'Saving provider...');
        }
        setStatus(elements.providerFeedback, 'Saving provider…', 'info');

        try {
            const payload = {
                api_key: apiKeyInput ? normalizeSecretName(apiKeyInput) : '',
                base_url: baseUrlInput ? normalizeSecretName(baseUrlInput) : ''
            };
            if (name === 'openai') {
                payload.auth_mode = draft.auth_mode === 'oauth' ? 'oauth' : 'api_key';
                payload.oauth_api_key_fallback_enabled = draft.oauth_api_key_fallback_enabled === true;
            }

            const response = await fetch(`api/system/providers/${encodeURIComponent(name)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const providerResult = await response.json();
            cancelProviderEdit(false);
            await loadProviders();
            await loadModels(); // provider availability can update model availability
            await loadSecrets();
            await notifyConfigChanged();
            const resultMessage = withRestartNotice(`Saved provider '${name}'.`, providerResult);
            setStatus(elements.providerFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to save provider: ${error.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                setIconButtonLabel(button, 'Save provider');
            }
            state.isSavingProvider = false;
        }
    }

    async function deleteProvider(providerName) {
        if (!window.confirm(`Delete provider '${providerName}'? Models referencing it must be removed first.`)) {
            return;
        }

        try {
            const response = await fetch(`api/system/providers/${encodeURIComponent(providerName)}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();

            await loadProviders();
            await loadModels();
            await loadSecrets();
            await notifyConfigChanged();
            if (state.providerEdit?.key === providerName) {
                cancelProviderEdit(false);
            }
            const resultMessage = withRestartNotice(`Removed provider '${providerName}'.`, result);
            setStatus(elements.providerFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to delete provider: ${error.message}`, 'error');
        }
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
            await loadProviders();
            const popupText = opened
                ? 'OpenAI OAuth URL opened. The URL is also shown in the OpenAI provider panel for manual copy/paste.'
                : 'Open the OAuth URL shown in the OpenAI provider panel, then paste the final redirect URL here.';
            setStatus(elements.providerFeedback, popupText, 'info');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to start OpenAI OAuth: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            renderProviders();
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
            await loadProviders();
            setStatus(elements.providerFeedback, 'Open the device URL and enter the code, then check status.', 'info');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to start OpenAI OAuth device code: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            renderProviders();
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
                await loadProviders();
                await loadModels();
                await notifyConfigChanged();
                setStatus(elements.providerFeedback, 'OpenAI OAuth connected.', 'success');
            } else {
                await loadProviders();
                setStatus(elements.providerFeedback, 'OpenAI OAuth device code is still pending.', 'info');
            }
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to check OpenAI OAuth device code: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            renderProviders();
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
            await loadProviders();
            await loadModels();
            await notifyConfigChanged();
            setStatus(elements.providerFeedback, 'OpenAI OAuth connected.', 'success');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to complete OpenAI OAuth: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            renderProviders();
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
            await loadProviders();
            await loadModels();
            await notifyConfigChanged();
            setStatus(elements.providerFeedback, 'OpenAI OAuth disconnected.', 'success');
        } catch (error) {
            setStatus(elements.providerFeedback, `Failed to disconnect OpenAI OAuth: ${error.message}`, 'error');
        } finally {
            state.isOpenAiOauthBusy = false;
            renderProviders();
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
            renderSecretsTable();
            updateImportOcrAvailability();
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
            await copyConnectionField(copyButton, field);
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
        cancelOAuthPoll(googleOAuthPolls, id);
    }

    function cancelMcpOAuthPoll(id) {
        cancelOAuthPoll(mcpOAuthPolls, id);
    }

    function cancelAllOAuthPolls() {
        [...googleOAuthPolls.keys()].forEach(cancelGoogleOAuthPoll);
        [...mcpOAuthPolls.keys()].forEach(cancelMcpOAuthPoll);
        if (mcpOAuthStatusRequest) {
            mcpOAuthStatusRequest.controller.abort();
            mcpOAuthStatusRequest = null;
        }
    }

    function startGoogleOAuthPoll(id) {
        cancelGoogleOAuthPoll(id);
        const owner = createOAuthPoll(googleOAuthPolls, id, 10 * 60 * 1000);
        void pollGoogleConnection(id, owner);
    }

    async function pollGoogleConnection(id, owner) {
        try {
            while (ownsOAuthPoll(googleOAuthPolls, id, owner)) {
                if (Date.now() >= owner.deadline) {
                    if (finishOAuthPoll(googleOAuthPolls, id, owner)) {
                        setStatus(elements.connectionsFeedback, 'Google authorization expired. Start a new attempt.', 'warning');
                    }
                    return;
                }
                if (!await waitForOAuthPoll(googleOAuthPolls, id, owner, 1500)) return;
                const response = await fetch('api/system/connections/google/connections', {
                    cache: 'no-store',
                    signal: owner.controller.signal,
                });
                const payload = await safeJson(response);
                if (!ownsOAuthPoll(googleOAuthPolls, id, owner)) return;
                if (!response.ok) throw new Error(payload?.message || `HTTP ${response.status}`);
                state.googleConnections = Array.isArray(payload) ? payload : [];
                renderGoogleConnection();
                if (state.googleConnections.find((connection) => connection.connection_id === id)?.connected) {
                    if (!finishOAuthPoll(googleOAuthPolls, id, owner)) return;
                    setStatus(elements.connectionsFeedback, 'Google account connected.', 'success');
                    await notifyConfigChanged();
                    return;
                }
            }
        } catch (error) {
            if (error.name === 'AbortError' || !ownsOAuthPoll(googleOAuthPolls, id, owner)) return;
            if (await waitForOAuthPoll(googleOAuthPolls, id, owner, 1500)) void pollGoogleConnection(id, owner);
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
        if (mcpOAuthStatusRequest) mcpOAuthStatusRequest.controller.abort();
        const request = { controller: new AbortController(), generation: Symbol('mcp-status') };
        mcpOAuthStatusRequest = request;
        const oauthConnections = state.mcpConnections.filter((connection) => connection.auth_mode === 'oauth');
        await Promise.all(oauthConnections.map(async (connection) => {
            try {
                const response = await fetch(`api/system/mcp/connections/${encodeURIComponent(connection.connection_id)}/oauth/status`, { cache: 'no-store', signal: request.controller.signal });
                const payload = await safeJson(response);
                if (mcpOAuthStatusRequest !== request || request.controller.signal.aborted) return;
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
                if (error.name === 'AbortError' || mcpOAuthStatusRequest !== request) return;
                const card = elements.mcpConnectionsList?.querySelector(`[data-mcp-id="${CSS.escape(connection.connection_id)}"]`);
                const statusElement = card?.querySelector('[data-mcp-oauth-status]');
                if (statusElement) statusElement.textContent = `OAuth status unavailable: ${error.message}`;
            }
        }));
        if (mcpOAuthStatusRequest === request) mcpOAuthStatusRequest = null;
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
            cancelMcpOAuthPoll(id);
            await mutateMcp(`${endpoint}/oauth/complete`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ redirect_url: redirectUrl, code: null, state: null }) }, 'MCP OAuth connected.');
            return;
        }
        if (action === 'oauth-disconnect') {
            cancelMcpOAuthPoll(id);
            await mutateMcp(`${endpoint}/oauth`, { method: 'DELETE' }, 'MCP OAuth disconnected.');
            return;
        }
        if (action === 'test') {
            await testMcpConnection(card, endpoint, button);
            return;
        }
        if (action === 'delete') {
            if (!window.confirm('Delete this MCP connection and its stored credential?')) return;
            cancelMcpOAuthPoll(id);
            await mutateMcp(endpoint, { method: 'DELETE' }, 'MCP connection deleted.');
            return;
        }
        if (action === 'clear-credential') {
            cancelMcpOAuthPoll(id);
            await mutateMcp(`${endpoint}/credential`, { method: 'DELETE' }, 'MCP credential cleared.');
            return;
        }
        if (action === 'credential') {
            cancelMcpOAuthPoll(id);
            const credential = card.querySelector('[data-mcp-field="credential"]')?.value || '';
            await mutateMcp(`${endpoint}/credential`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ credential }) }, 'MCP credential saved.');
            return;
        }
        if (action === 'save') {
            cancelMcpOAuthPoll(id);
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
        cancelMcpOAuthPoll(id);
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
            const owner = createOAuthPoll(mcpOAuthPolls, id, 10 * 60 * 1000);
            void pollMcpOAuthStatus(id, endpoint, owner);
        } catch (error) {
            setStatus(elements.mcpFeedback, error.message, 'error');
        } finally {
            state.isSavingMcp = false;
        }
    }

    async function pollMcpOAuthStatus(id, endpoint, owner) {
        while (ownsOAuthPoll(mcpOAuthPolls, id, owner)) {
            if (Date.now() >= owner.deadline) {
                if (finishOAuthPoll(mcpOAuthPolls, id, owner)) {
                    setStatus(elements.mcpFeedback, 'MCP OAuth authorization expired. Start a new attempt.', 'warning');
                }
                return;
            }
            try {
                const response = await fetch(`${endpoint}/oauth/status`, { cache: 'no-store', signal: owner.controller.signal });
                const payload = await safeJson(response);
                if (!ownsOAuthPoll(mcpOAuthPolls, id, owner)) return;
                if (response.ok && (payload?.connected || payload?.status === 'failed' || payload?.status === 'expired')) {
                    if (!finishOAuthPoll(mcpOAuthPolls, id, owner)) return;
                    const connected = payload.connected === true;
                    setStatus(elements.mcpFeedback, connected ? 'MCP OAuth connected.' : `MCP OAuth authorization ${payload.status}. Start a new attempt.`, connected ? 'success' : 'error');
                    await loadMcpConnections();
                    return;
                }
            } catch (error) {
                if (error.name === 'AbortError' || !ownsOAuthPoll(mcpOAuthPolls, id, owner)) return;
                // A transient status failure should not cancel the browser flow.
            }
            if (!await waitForOAuthPoll(mcpOAuthPolls, id, owner, 2000)) return;
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

    function renderSecretsTable() {
        if (!elements.secretsList) return;

        const cards = [];
        const editing = state.secretEdit;
        const draft = state.secretDraft || {};

        if (editing && editing.mode === 'new') {
            cards.push(renderSecretEditCard(draft, { isNew: true }));
        }

        if (!state.secrets.length) {
            cards.push(`
                <div class="rounded-lg border border-border-primary bg-app-card px-4 py-3 text-sm text-txt-secondary text-center shadow-sm">
                    No secrets registered yet.
                </div>
            `);
            elements.secretsList.innerHTML = cards.join('');
            focusSecretInput();
            return;
        }

        state.secrets.forEach((entry) => {
            if (editing && editing.mode === 'existing' && editing.key === entry.name) {
                cards.push(renderSecretEditCard(draft, { isNew: false }));
                return;
            }
            const metadata = SECRET_METADATA[entry.name] || null;
            const label = metadata?.label || entry.name;
            const hasValue = Boolean(entry.has_value);
            const stored = Boolean(entry.stored);
            const statusBadge = hasValue
                ? '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-success border">Set</span>'
                : '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium pill-error border">Not set</span>';

            const description = metadata?.description
                ? `<div class="text-xs text-txt-secondary mt-1">${escapeHtml(metadata.description)}</div>`
                : '';

            const deleteButton = stored
                ? `<button data-secret-action="delete" ${iconButton('trash', 'Delete secret', 'is-danger')}>${iconSvg('trash')}</button>`
                : '';

            cards.push(`
                <div class="secret-card rounded-lg border border-border-primary bg-app-card px-5 py-4 shadow-sm hover:shadow transition-shadow" data-secret="${escapeHtml(entry.name)}" style="max-width: 1400px;">
                    <div class="space-y-4">
                        <div class="flex items-start justify-between gap-4">
                            <div class="min-w-0">
                                <div class="flex items-center gap-2 flex-wrap">
                                    <div class="font-medium text-txt-primary text-sm">${escapeHtml(label)}</div>
                                    <div class="w-fit">${statusBadge}</div>
                                </div>
                                <div class="font-mono text-xs text-txt-secondary mt-0.5 break-all">${escapeHtml(entry.name)}</div>
                                ${description}
                            </div>
                            <div class="flex items-center gap-2 justify-end shrink-0 flex-wrap">
                                <button data-secret-action="set" ${iconButton('edit', 'Update secret', 'is-primary')}>${iconSvg('edit')}</button>
                                <button data-secret-action="clear" ${iconButton('x', 'Clear secret', 'is-danger')}>${iconSvg('x')}</button>
                                ${deleteButton}
                            </div>
                        </div>
                    </div>
                </div>
            `);
        });

        elements.secretsList.innerHTML = cards.join('');
        focusSecretInput();
        updateImportOcrAvailability();
    }

    function renderSecretEditCard(draft, { isNew }) {
        const rowKey = isNew ? '__new' : (state.secretEdit?.key || draft.name || '');
        const nameReadonly = isNew ? '' : 'readonly';
        const nameHelp = isNew
            ? 'Use uppercase letters, numbers, or underscores.'
            : 'Secret names are identities and cannot be renamed here.';
        return `
            <div class="secret-card rounded-lg border border-border-primary bg-app-card editing-highlight px-5 py-4 shadow-sm" data-secret="${escapeHtml(rowKey)}" data-mode="edit" style="max-width: 1400px;">
                <div class="space-y-4">
                    <div class="grid gap-4 md:grid-cols-2">
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Secret Name</label>
                            <input data-secret-field="name" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary font-mono text-sm transition-colors" placeholder="e.g. LOCAL_MODEL_TOKEN" value="${escapeHtml(draft.name || '')}" ${nameReadonly} />
                            <p class="text-xs text-txt-secondary mt-1">${nameHelp}</p>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-txt-primary mb-1.5">Secret Value</label>
                            <input data-secret-field="value" type="password" class="w-full px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent bg-app-card text-txt-primary text-sm transition-colors" placeholder="Enter the credential" value="${escapeHtml(draft.value || '')}" />
                            <p class="text-xs text-txt-secondary mt-1">Values are encrypted at rest and owned by the current principal.</p>
                        </div>
                    </div>
                    <div class="flex justify-end gap-2">
                        <button data-secret-action="cancel-secret" ${iconButton('circleX', 'Cancel secret edit')}>${iconSvg('circleX')}</button>
                        <button data-secret-action="save-secret" ${iconButton('save', 'Save secret', 'is-primary')}>${iconSvg('save')}</button>
                    </div>
                </div>
            </div>
        `;
    }

    function focusSecretInput(field = 'name') {
        if (!state.secretEdit) return;
        requestAnimationFrame(() => {
            const editableName = state.secretEdit?.mode === 'new';
            const targetField = editableName ? field : (field === 'name' ? 'value' : field);
            const el = elements.secretsList?.querySelector(`[data-secret][data-mode="edit"] [data-secret-field="${targetField}"]`);
            if (el instanceof HTMLInputElement) {
                el.focus();
                el.select();
            }
        });
    }

    function updateImportOcrAvailability() {
        if (!elements.importPdfModeSelect || !elements.importPdfStrategySelect) return;
        const capability = window.App?.metadata?.ingestion_capabilities?.pdf_ocr;
        const isPageImagesMode = (elements.importPdfModeSelect?.value || 'markdown') === 'page_images';
        const selectedStrategy = elements.importPdfStrategySelect.value || 'default';
        const defaultOrder = Array.isArray(capability?.default_order)
            ? capability.default_order.map(value => String(value))
            : [];
        const defaultIncludesOcr = defaultOrder.includes('pdf_ocr');
        const selectedPathUsesOcr = selectedStrategy === 'ocr'
            || (selectedStrategy === 'default' && defaultIncludesOcr);
        const missingRequirements = Array.isArray(capability?.missing)
            ? capability.missing.map(value => String(value))
            : ['OCR capability metadata'];
        const ocrAvailable = capability?.available === true;
        const disableEnrichments = !ocrAvailable || !selectedPathUsesOcr || isPageImagesMode;
        const disabledReason = !ocrAvailable
            ? `OCR unavailable: configure ${missingRequirements.join(', ')}`
            : 'Select a conversion strategy that can invoke Mistral OCR.';
        const enrichmentControls = [
            elements.importIncludeOcrBlocksCheckbox,
            elements.importExtractOcrHeaderCheckbox,
            elements.importExtractOcrFooterCheckbox,
            elements.importOcrTableFormatSelect,
            elements.importOcrConfidenceSelect
        ].filter(Boolean);

        elements.importMarkdownOptions?.classList.toggle('hidden', isPageImagesMode);
        elements.importPageImageOptions?.classList.toggle('hidden', !isPageImagesMode);
        elements.importPdfOcrEnrichments?.classList.toggle('hidden', !selectedPathUsesOcr);
        const ocrOption = elements.importPdfStrategySelect.querySelector('option[value="ocr"]');
        if (ocrOption instanceof HTMLOptionElement) ocrOption.disabled = !ocrAvailable;
        if (elements.importCaptureOcrImagesCheckbox) {
            elements.importCaptureOcrImagesCheckbox.disabled = !ocrAvailable || isPageImagesMode;
        }
        enrichmentControls.forEach(control => { control.disabled = disableEnrichments; });
        if (disableEnrichments) {
            enrichmentControls.forEach(control => { control.title = disabledReason; });
            if (!ocrAvailable && elements.importCaptureOcrImagesCheckbox) {
                elements.importCaptureOcrImagesCheckbox.title = disabledReason;
            }
        } else {
            enrichmentControls.forEach(control => { control.removeAttribute('title'); });
            elements.importCaptureOcrImagesCheckbox?.removeAttribute('title');
        }
        if (disableEnrichments) {
            if ((!ocrAvailable || isPageImagesMode) && elements.importCaptureOcrImagesCheckbox) {
                elements.importCaptureOcrImagesCheckbox.checked = false;
            }
            enrichmentControls.forEach(control => {
                if (control instanceof HTMLInputElement) control.checked = false;
                if (control instanceof HTMLSelectElement) control.value = '';
            });
        }
        if (elements.importPdfStrategyHelp) {
            const orderLabel = defaultOrder.length ? defaultOrder.join(' → ') : 'no strategies configured';
            if (selectedStrategy === 'local_text') {
                elements.importPdfStrategyHelp.textContent = 'Uses local PDF text extraction only. PDF OCR enrichments do not apply.';
            } else if (selectedStrategy === 'ocr') {
                elements.importPdfStrategyHelp.textContent = ocrAvailable
                    ? 'Uses Mistral OCR only. OCR enrichment options apply to this import.'
                    : disabledReason;
            } else {
                const ocrIndex = defaultOrder.indexOf('pdf_ocr');
                elements.importPdfStrategyHelp.textContent = ocrIndex < 0
                    ? `Configured default: ${orderLabel}. PDF OCR enrichments do not apply.`
                    : ocrIndex === 0
                        ? `Configured default: ${orderLabel}. OCR runs first, so enrichment options apply.`
                        : `Configured default: ${orderLabel}. Enrichment options apply only if earlier strategies fall through to OCR.`;
            }
            const defaultOption = elements.importPdfStrategySelect.querySelector('option[value="default"]');
            if (defaultOption instanceof HTMLOptionElement) {
                defaultOption.textContent = `Configured default (${orderLabel})`;
            }
        }
    }

    async function handleSecretsTableClick(event) {
        const actionBtn = event.target.closest('[data-secret-action]');
        if (!actionBtn) return;

        const row = actionBtn.closest('[data-secret]');
        const name = row?.dataset.secret;
        if (!name) return;

        const action = actionBtn.dataset.secretAction;

        if (action === 'set') {
            startSecretEdit(name);
        } else if (action === 'clear') {
            if (!window.confirm(`Clear the stored value for ${name}?`)) {
                return;
            }
            try {
                const result = await updateSecretValue(name, '');
                const resultMessage = withRestartNotice(`Cleared secret '${name}'.`, result);
                setStatus(elements.secretFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
            } catch (error) {
                setStatus(elements.secretFeedback, `Failed to clear secret: ${error.message}`, 'error');
            }
        } else if (action === 'delete') {
            if (!window.confirm(`Delete secret '${name}' from the system? This cannot be undone.`)) {
                return;
            }
            try {
                const result = await deleteSecret(name);
                const resultMessage = withRestartNotice(`Deleted secret '${name}'.`, result);
                setStatus(elements.secretFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
            } catch (error) {
                setStatus(elements.secretFeedback, `Failed to delete secret: ${error.message}`, 'error');
            }
        } else if (action === 'cancel-secret') {
            cancelSecretEdit();
        } else if (action === 'save-secret') {
            await saveSecretRow(actionBtn);
        }
    }

    function handleSecretInputChange(event) {
        const target = event.target;
        if (!state.secretEdit || !(target instanceof HTMLInputElement) || !target.dataset.secretField) {
            return;
        }
        if (!state.secretDraft) {
            state.secretDraft = {};
        }
        state.secretDraft[target.dataset.secretField] = target.value;
    }

    function startNewSecret() {
        if (state.secretEdit) {
            setStatus(elements.secretFeedback, 'Finish editing the current secret before adding another.', 'warning');
            return;
        }
        state.secretEdit = { mode: 'new', key: '__new' };
        state.secretDraft = {
            name: '',
            value: ''
        };
        renderSecretsTable();
        setStatus(elements.secretFeedback, 'Enter details for the new secret and click Save.', 'info');
        focusSecretInput('name');
    }

    function startSecretEdit(name) {
        if (state.secretEdit) {
            setStatus(elements.secretFeedback, 'Finish editing the current secret before editing another.', 'warning');
            return;
        }
        if (!state.secrets.some(secret => secret.name === name)) return;
        state.secretEdit = { mode: 'existing', key: name };
        state.secretDraft = {
            name,
            value: ''
        };
        renderSecretsTable();
        setStatus(elements.secretFeedback, `Updating '${name}'. Enter a new value and save.`, 'info');
        focusSecretInput('value');
    }

    function cancelSecretEdit(showStatus = true) {
        state.secretEdit = null;
        state.secretDraft = null;
        renderSecretsTable();
        if (showStatus) {
            setStatus(elements.secretFeedback, 'Editing cancelled.', 'info');
        }
    }

    async function saveSecretRow(button) {
        if (state.isSavingSecret || !state.secretEdit || !state.secretDraft) return;

        let name = (state.secretDraft.name || '').trim();
        const value = state.secretDraft.value || '';

        if (!name) {
            setStatus(elements.secretFeedback, 'Secret name is required.', 'error');
            return;
        }
        if (!value) {
            setStatus(elements.secretFeedback, 'Secret value is required.', 'error');
            return;
        }

        const normalized = normalizeSecretName(name);
        if (!normalized) {
            setStatus(elements.secretFeedback, 'Secret name must contain letters, numbers, or underscores.', 'error');
            return;
        }
        name = normalized;
        state.secretDraft.name = normalized;

        state.isSavingSecret = true;
        if (button) {
            button.disabled = true;
            setIconButtonLabel(button, 'Saving secret...');
        }
        setStatus(elements.secretFeedback, `Saving ${name}…`, 'info');

        try {
            const result = await updateSecretValue(name, value);
            cancelSecretEdit(false);
            const resultMessage = withRestartNotice(`Saved secret '${name}'.`, result);
            setStatus(elements.secretFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.secretFeedback, `Failed to save secret: ${error.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                setIconButtonLabel(button, 'Save secret');
            }
            state.isSavingSecret = false;
        }
    }

    function normalizeSecretName(name) {
        if (!name) return '';
        return name.replace(/\s+/g, '_').toUpperCase();
    }

    async function updateSecretValue(name, value) {
        const response = await fetch('api/system/secrets', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, value })
        });

        if (!response.ok) {
            const errorData = await safeJson(response);
            throw new Error(errorData?.message || `HTTP ${response.status}`);
        }

        const result = await response.json();
        await loadSecrets();
        await loadGoogleConnection();
        await loadMcpConnections();
        await loadProviders();
        await notifyConfigChanged();
        return result;
    }

    async function deleteSecret(name) {
        const response = await fetch(`api/system/secrets/${encodeURIComponent(name)}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const errorData = await safeJson(response);
            throw new Error(errorData?.message || `HTTP ${response.status}`);
        }

        const result = await response.json();
        await loadSecrets();
        await loadMcpConnections();
        await loadProviders();
        await notifyConfigChanged();
        return result;
    }

    async function loadVaultOptions(select) {
        if (!select) return;

        const cachedVaults = window.App && window.App.metadata && Array.isArray(window.App.metadata.vaults)
            ? window.App.metadata.vaults
            : null;
        const vaults = cachedVaults || await (async () => {
            try {
                const response = await fetch('api/metadata');
                if (!response.ok) return [];
                const data = await response.json();
                window.App = window.App || {};
                window.App.metadata = data;
                return Array.isArray(data?.vaults) ? data.vaults : [];
            } catch { return []; }
        })();

        select.innerHTML = '<option value="">Select vault…</option>';
        vaults.forEach((vault) => {
            const opt = document.createElement('option');
            opt.value = vault;
            opt.textContent = vault;
            select.appendChild(opt);
        });
    }

    async function loadPurgeSessionsVaults() {
        await loadVaultOptions(elements.purgeSessionsVault);
    }

    async function loadCleanupGoalsVaults() {
        await loadVaultOptions(elements.cleanupGoalsVault);
    }

    async function handlePurgeSessions() {
        const btn = elements.purgeSessionsBtn;
        if (!btn || btn.disabled) return;

        const vaultName = elements.purgeSessionsVault?.value;
        if (!vaultName) {
            setStatus(elements.purgeSessionsFeedback, 'Select a vault first.', 'warning');
            return;
        }

        const ageValue = elements.purgeSessionsAge?.value;
        const olderThanDays = ageValue ? parseInt(ageValue, 10) : null;

        const ageLabel = ageValue ? `older than ${ageValue} days` : 'all sessions';
        if (!confirm(`Delete ${ageLabel} in vault "${vaultName}"? This cannot be undone.`)) return;

        btn.disabled = true;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Purge Sessions';
        setIconButtonLabel(btn, 'Purging sessions...');
        setStatus(elements.purgeSessionsFeedback, 'Purging sessions…', 'info');

        try {
            const response = await fetch('api/chat/sessions/purge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ vault_name: vaultName, older_than_days: olderThanDays }),
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            setStatus(elements.purgeSessionsFeedback, result.message, 'success');
        } catch (error) {
            setStatus(elements.purgeSessionsFeedback, `Failed to purge sessions: ${error.message}`, 'error');
        } finally {
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
        }
    }

    async function handleCleanupGoals() {
        const btn = elements.cleanupGoalsBtn;
        if (!btn || btn.disabled || state.isCleaningGoals) return;

        const vaultName = elements.cleanupGoalsVault?.value;
        if (!vaultName) {
            setStatus(elements.cleanupGoalsFeedback, 'Select a vault first.', 'warning');
            return;
        }

        const statusValue = elements.cleanupGoalsStatus?.value || 'completed';
        const ageValue = elements.cleanupGoalsAge?.value;
        const olderThanDays = ageValue ? parseInt(ageValue, 10) : null;
        const statusLabel = elements.cleanupGoalsStatus?.selectedOptions?.[0]?.textContent || 'matching goals';
        const ageLabel = ageValue ? `older than ${ageValue} days` : 'of any age';

        if (!confirm(`Delete ${statusLabel.toLowerCase()} ${ageLabel} in vault "${vaultName}"? This cannot be undone.`)) return;

        state.isCleaningGoals = true;
        btn.disabled = true;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Clean Up Goals';
        setIconButtonLabel(btn, 'Cleaning goals...');
        setStatus(elements.cleanupGoalsFeedback, 'Cleaning up goals…', 'info');

        try {
            const response = await fetch('api/system/goals/cleanup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    vault_name: vaultName,
                    status: statusValue,
                    older_than_days: olderThanDays,
                }),
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            setStatus(elements.cleanupGoalsFeedback, result.message, 'success');
        } catch (error) {
            setStatus(elements.cleanupGoalsFeedback, `Failed to clean up goals: ${error.message}`, 'error');
        } finally {
            state.isCleaningGoals = false;
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
        }
    }

    async function handlePurgeExpiredCache() {
        if (!elements.purgeExpiredCacheBtn || state.isPurgingCache) return;

        state.isPurgingCache = true;
        const button = elements.purgeExpiredCacheBtn;
        const originalLabel = button.dataset.iconLabel || button.title || 'Purge Expired Cache';
        button.disabled = true;
        setIconButtonLabel(button, 'Purging expired cache...');
        setStatus(elements.miscFeedback, 'Purging expired cache artifacts…', 'info');

        try {
            const response = await fetch('api/system/cache/purge-expired', {
                method: 'POST'
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            setStatus(
                elements.miscFeedback,
                result?.message || `Purged ${result?.purged_count ?? 0} expired cache artifact(s).`,
                'success'
            );
        } catch (error) {
            setStatus(elements.miscFeedback, `Failed to purge cache: ${error.message}`, 'error');
        } finally {
            button.disabled = false;
            setIconButtonLabel(button, originalLabel);
            state.isPurgingCache = false;
        }
    }

    async function handleRefreshSystemAuthoring() {
        if (!elements.refreshSystemAuthoringBtn || state.isRefreshingSystemAuthoring) return;

        const confirmed = window.confirm(
            'Refresh system authoring scripts?\n\n'
            + 'This will overwrite scripts in system/Authoring. Use this if you have customized '
            + 'the system scripts and want to return to baseline or update to the latest version '
            + 'of system scripts. Vault scripts in AssistantMD/Authoring are not touched.'
        );
        if (!confirmed) return;

        state.isRefreshingSystemAuthoring = true;
        const button = elements.refreshSystemAuthoringBtn;
        const originalLabel = button.dataset.iconLabel || button.title || 'Refresh System Scripts';
        button.disabled = true;
        setIconButtonLabel(button, 'Refreshing system scripts...');
        setStatus(elements.refreshSystemAuthoringFeedback, 'Refreshing system authoring scripts…', 'info');

        try {
            const response = await fetch('api/system/authoring/seed-refresh', {
                method: 'POST'
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            setStatus(
                elements.refreshSystemAuthoringFeedback,
                result?.message || 'System authoring scripts refreshed.',
                result?.success === false ? 'warning' : 'success'
            );
            await callbacks.refreshStatus?.();
        } catch (error) {
            setStatus(
                elements.refreshSystemAuthoringFeedback,
                `Failed to refresh system authoring scripts: ${error.message}`,
                'error'
            );
        } finally {
            button.disabled = false;
            setIconButtonLabel(button, originalLabel);
            state.isRefreshingSystemAuthoring = false;
        }
    }

    async function handleCleanupVaultState() {
        if (!elements.cleanupVaultStateBtn || state.isCleaningVaultState) return;

        state.isCleaningVaultState = true;
        const button = elements.cleanupVaultStateBtn;
        const originalLabel = button.dataset.iconLabel || button.title || 'Clean Up Vault State';
        button.disabled = true;
        setIconButtonLabel(button, 'Cleaning vault state...');
        setStatus(elements.cleanupVaultStateFeedback, 'Cleaning expired vault-state artifacts…', 'info');

        try {
            const response = await fetch('api/vault-state/cleanup', {
                method: 'POST'
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            const message = result?.message || (
                `Deleted ${result?.expired_mutation_rows_deleted ?? 0} mutation row(s), `
                + `${result?.expired_snapshot_rows_deleted ?? 0} snapshot row(s), `
                + `${result?.snapshot_files_deleted ?? 0} snapshot file(s).`
            );
            setStatus(elements.cleanupVaultStateFeedback, message, 'success');
        } catch (error) {
            setStatus(
                elements.cleanupVaultStateFeedback,
                `Failed to clean vault state: ${error.message}`,
                'error'
            );
        } finally {
            button.disabled = false;
            setIconButtonLabel(button, originalLabel);
            state.isCleaningVaultState = false;
        }
    }

    async function loadSystemJobs() {
        if (!elements.systemJobsList || state.isLoadingSystemJobs) return;

        state.isLoadingSystemJobs = true;
        const button = elements.refreshSystemJobsBtn;
        const originalLabel = button ? (button.dataset.iconLabel || button.title || 'Refresh System Jobs') : '';
        if (button) {
            button.disabled = true;
            setIconButtonLabel(button, 'Refreshing system jobs...');
        }

        try {
            const response = await fetch('api/status');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const payload = await response.json();
            const jobs = payload?.scheduler?.job_details || [];
            state.systemJobs = jobs.filter((job) => job.job_type === 'system');
            renderSystemJobs();
        } catch (error) {
            elements.systemJobsList.innerHTML = `
                <div class="state-error">Failed to load system jobs: ${escapeHtml(error.message)}</div>
            `;
        } finally {
            if (button) {
                button.disabled = false;
                setIconButtonLabel(button, originalLabel);
            }
            state.isLoadingSystemJobs = false;
        }
    }

    function renderSystemJobs() {
        if (!elements.systemJobsList) return;

        if (!state.systemJobs.length) {
            elements.systemJobsList.innerHTML = `
                <div class="text-sm text-txt-secondary">No system scheduler jobs are currently registered.</div>
            `;
            return;
        }

        const rows = state.systemJobs.map((job) => {
            const lastRun = formatDateTime(job.last_run_time);
            const nextRun = formatDateTime(job.next_run_time);
            const status = job.last_status || 'not run';
            const error = job.last_error
                ? `<div class="state-error text-xs mt-1">${escapeHtml(job.last_error)}</div>`
                : '';
            return `
                <tr>
                    <td>
                        <strong>${escapeHtml(job.name || job.id)}</strong>
                        <div class="cell-xs cell-mono subtle">${escapeHtml(job.id)}</div>
                    </td>
                    <td class="cell-xs">${escapeHtml(status)}${error}</td>
                    <td class="cell-xs">${escapeHtml(lastRun)}</td>
                    <td class="cell-xs">${escapeHtml(nextRun)}</td>
                </tr>
            `;
        }).join('');

        elements.systemJobsList.innerHTML = `
            <div class="overflow-x-auto">
                <table class="dashboard-table">
                    <thead>
                        <tr>
                            <th>Job</th>
                            <th>Status</th>
                            <th>Last Run</th>
                            <th>Next Run</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
    }

    async function loadSystemMigrations() {
        if (!elements.systemMigrationsStatus || state.isLoadingSystemMigrations) return;

        state.isLoadingSystemMigrations = true;
        const button = elements.refreshSystemMigrationsBtn;
        const originalLabel = button ? (button.dataset.iconLabel || button.title || 'Refresh Database Migrations') : '';
        if (button) {
            button.disabled = true;
            setIconButtonLabel(button, 'Refreshing database migrations...');
        }

        try {
            const response = await fetch('api/system/migrations/status');
            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            state.systemMigrations = await response.json();
            renderSystemMigrations();
            setStatus(elements.systemMigrationsFeedback, state.systemMigrations.message || '', 'info');
        } catch (error) {
            elements.systemMigrationsStatus.innerHTML = `
                <div class="state-error">Failed to load database migrations: ${escapeHtml(error.message)}</div>
            `;
            updateSystemMigrationsButton(null);
        } finally {
            if (button) {
                button.disabled = false;
                setIconButtonLabel(button, originalLabel);
            }
            state.isLoadingSystemMigrations = false;
        }
    }

    async function handleRunSystemMigrations() {
        if (!elements.runSystemMigrationsBtn || state.isRunningSystemMigrations) return;
        const pendingCount = Number(state.systemMigrations?.pending_count ?? 0);
        if (pendingCount <= 0) return;

        state.isRunningSystemMigrations = true;
        const button = elements.runSystemMigrationsBtn;
        button.disabled = true;
        setIconButtonLabel(button, 'Running database migrations...');
        if (elements.refreshSystemMigrationsBtn) {
            elements.refreshSystemMigrationsBtn.disabled = true;
        }
        setStatus(elements.systemMigrationsFeedback, 'Running system database migrations…', 'info');

        try {
            const response = await fetch('api/system/migrations/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ backup: true }),
            });

            if (!response.ok) {
                const errorData = await safeJson(response);
                throw new Error(errorData?.message || `HTTP ${response.status}`);
            }

            const result = await response.json();
            state.systemMigrations = result;
            renderSystemMigrations();
            const backupCount = Array.isArray(result.backups_created) ? result.backups_created.length : 0;
            const backupText = backupCount > 0 ? ` ${backupCount} backup file(s) created.` : '';
            setStatus(elements.systemMigrationsFeedback, `${result.message || 'System database migrations completed.'}${backupText}`, 'success');
            await callbacks.refreshStatus?.();
        } catch (error) {
            setStatus(elements.systemMigrationsFeedback, `Failed to run database migrations: ${error.message}`, 'error');
        } finally {
            updateSystemMigrationsButton(state.systemMigrations);
            if (elements.refreshSystemMigrationsBtn) {
                elements.refreshSystemMigrationsBtn.disabled = false;
            }
            state.isRunningSystemMigrations = false;
        }
    }

    function renderSystemMigrations() {
        if (!elements.systemMigrationsStatus) return;

        const payload = state.systemMigrations;
        const targets = Array.isArray(payload?.targets) ? payload.targets : [];
        if (!targets.length) {
            elements.systemMigrationsStatus.innerHTML = `
                <div class="text-sm text-txt-secondary">No registered system database migrations were found.</div>
            `;
            updateSystemMigrationsButton(payload);
            return;
        }

        const rows = targets.map((target) => {
            const applied = formatVersionList(target.applied_versions);
            const pending = formatVersionList(target.pending_versions);
            const exists = target.exists ? 'yes' : 'no';
            const backup = target.backup_path
                ? `<div class="cell-xs cell-mono subtle mt-1">backup: ${escapeHtml(target.backup_path)}</div>`
                : '';
            return `
                <tr>
                    <td>
                        <strong>${escapeHtml(target.db_name)}</strong>
                        <div class="cell-xs cell-mono subtle">${escapeHtml(target.namespace)}</div>
                        ${backup}
                    </td>
                    <td class="cell-xs">${escapeHtml(exists)}</td>
                    <td class="cell-xs">${escapeHtml(applied)}</td>
                    <td class="cell-xs">${escapeHtml(pending)}</td>
                </tr>
            `;
        }).join('');

        const pendingCount = Number(payload?.pending_count ?? 0);
        const summaryTone = pendingCount > 0 ? 'state-warning' : 'state-success';
        const summary = payload?.message || (
            pendingCount > 0
                ? `${pendingCount} system database migration(s) pending.`
                : 'All registered system database migrations are applied.'
        );

        elements.systemMigrationsStatus.innerHTML = `
            <div class="${summaryTone} mb-2">${escapeHtml(summary)}</div>
            <div class="overflow-x-auto">
                <table class="dashboard-table">
                    <thead>
                        <tr>
                            <th>Database</th>
                            <th>Exists</th>
                            <th>Applied</th>
                            <th>Pending</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
        updateSystemMigrationsButton(payload);
    }

    function formatVersionList(value) {
        if (!Array.isArray(value) || value.length === 0) return 'none';
        return value.map((version) => String(version)).join(', ');
    }

    function updateSystemMigrationsButton(payload) {
        const button = elements.runSystemMigrationsBtn;
        if (!button || state.isRunningSystemMigrations) return;

        const pendingCount = Number(payload?.pending_count ?? 0);
        const hasPending = pendingCount > 0;
        button.disabled = !hasPending;
        setIconButtonLabel(button, hasPending ? `Run ${pendingCount} Migration${pendingCount === 1 ? '' : 's'}` : 'Database migrations up to date');
        button.classList.toggle('is-primary', hasPending);
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

    async function refreshAll() {
        await refreshActivityLog();
        await loadProviders();
        await loadGeneralSettings();
        await loadModels();
        await loadSecrets();
        await loadGoogleConnection();
        await loadMcpConnections();
        await loadSystemJobs();
        await loadSystemMigrations();
        await loadImportVaults();
        await loadPurgeSessionsVaults();
        await loadCleanupGoalsVaults();
        state.hasLoadedOnce = true;
    }

    function externalSetRestartRequired() {
        // Configuration panel no longer tracks a dedicated restart banner.
    }

    function init(options = {}) {
        if (state.initialized) return;

        callbacks.refreshMetadata = options.refreshMetadata || null;
        callbacks.refreshStatus = options.refreshStatus || null;
        callbacks.openFile = options.openFile || null;

        cacheElements();
        bindEvents();
        window.addEventListener('pagehide', cancelAllOAuthPolls);

        state.initialized = true;
    }

    function onTabActivated() {
        if (!state.initialized) return;
        refreshAll();
    }

    function onTabDeactivated() {
        cancelAllOAuthPolls();
    }

    async function onDashboardActivated() {
        if (!state.initialized) return;
        await loadSecrets();
        await loadImportVaults();
        await loadImportJobs();
    }

    function renderImportVaults() {
        const select = elements.importVaultSelect;
        if (!select) return;
        const selectedVault = select.value;
        select.innerHTML = '<option value="">Select vault…</option>';
        if (!state.importVaults || state.importVaults.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'No vaults detected';
            opt.disabled = true;
            select.appendChild(opt);
            return;
        }
        state.importVaults.forEach((vault) => {
            const opt = document.createElement('option');
            opt.value = vault;
            opt.textContent = vault;
            select.appendChild(opt);
        });
        if (state.importVaults.includes(selectedVault)) {
            select.value = selectedVault;
        }
    }

    function handleImportVaultChange() {
        state.importResults = null;
        state.importUrlResult = null;
        renderImportResults();
        if (state.isLoadingImportJobs) {
            state.pendingImportJobsReload = true;
        } else {
            loadImportJobs();
        }
    }

    async function loadImportVaults(force = false) {
        if (state.isLoadingImportVaults) return;
        state.isLoadingImportVaults = true;
        setStatus(elements.importStatus, 'Loading vaults…', 'info');

        // Prefer cached metadata from main app if available
        const cachedVaults = window.App && window.App.metadata && Array.isArray(window.App.metadata.vaults)
            ? window.App.metadata.vaults
            : null;
        if (cachedVaults && !force) {
            state.importVaults = cachedVaults;
            renderImportVaults();
            setStatus(elements.importStatus, '', 'info');
        }

        try {
            // Always refresh from API to stay current
            const response = await fetch('api/metadata');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            state.importVaults = Array.isArray(data?.vaults) ? data.vaults : [];
            renderImportVaults();
            setStatus(elements.importStatus, '', 'info');
            // Keep cache in sync for other modules
            window.App = window.App || {};
            window.App.metadata = data;
        } catch (error) {
            if (!cachedVaults) {
                state.importVaults = [];
                renderImportVaults();
            }
            setStatus(elements.importStatus, `Failed to load vaults: ${error.message}`, 'error');
        } finally {
            state.isLoadingImportVaults = false;
        }
    }

    async function handleImportVaultRescan() {
        if (!elements.importRefreshVaultsBtn || state.isLoadingImportVaults) return;
        const btn = elements.importRefreshVaultsBtn;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Refresh Vaults';
        btn.disabled = true;
        setIconButtonLabel(btn, 'Rescanning vaults...');
        setStatus(elements.importStatus, 'Rescanning vaults…', 'info');
        try {
            const response = await fetch('api/vaults/rescan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            // Refresh global status/metadata if caller provided callbacks
            if (callbacks.refreshStatus) await callbacks.refreshStatus();
            if (callbacks.refreshMetadata) await callbacks.refreshMetadata();
            // Reload vault list for import select
            await loadImportVaults(true);
            setStatus(elements.importStatus, 'Vaults refreshed.', 'success');
        } catch (error) {
            setStatus(elements.importStatus, `Rescan failed: ${error.message}`, 'error');
        } finally {
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
        }
    }

    function normalizeOutputPaths(outputs) {
        if (!Array.isArray(outputs)) return [];
        const seen = new Set();
        const normalized = [];
        outputs.forEach((value) => {
            const path = String(value || '').trim();
            if (!path || seen.has(path)) return;
            seen.add(path);
            normalized.push(path);
        });
        return normalized;
    }

    function summarizeImportOutputs(outputs) {
        const paths = normalizeOutputPaths(outputs);
        if (!paths.length) {
            return {
                destinationLabel: 'No output files',
                filesLabel: '0 files',
            };
        }

        const directorySegments = paths.map((path) => {
            const slashIndex = path.lastIndexOf('/');
            const folder = slashIndex >= 0 ? path.slice(0, slashIndex) : '';
            return folder ? folder.split('/').filter(Boolean) : [];
        });

        let commonDir = directorySegments[0] ? directorySegments[0].slice() : [];
        for (let i = 1; i < directorySegments.length; i += 1) {
            const current = directorySegments[i];
            let prefixLen = 0;
            while (
                prefixLen < commonDir.length &&
                prefixLen < current.length &&
                commonDir[prefixLen] === current[prefixLen]
            ) {
                prefixLen += 1;
            }
            commonDir = commonDir.slice(0, prefixLen);
            if (!commonDir.length) break;
        }

        // Collapse companion assets to a useful import destination summary.
        const destinationSegments = (
            commonDir.length >= 2 && commonDir[0] === 'Imported'
                ? commonDir.slice(0, 2)
                : commonDir
        );
        const destinationLabel = destinationSegments.length
            ? destinationSegments.join('/')
            : '(vault root)';

        const extensionCounts = new Map();
        paths.forEach((path) => {
            const slashIndex = path.lastIndexOf('/');
            const filename = slashIndex >= 0 ? path.slice(slashIndex + 1) : path;
            const dotIndex = filename.lastIndexOf('.');
            const extension = dotIndex > 0
                ? filename.slice(dotIndex + 1).toLowerCase()
                : 'no-ext';
            extensionCounts.set(extension, (extensionCounts.get(extension) || 0) + 1);
        });

        const typeParts = Array.from(extensionCounts.entries())
            .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
            .map(([ext, count]) => `${count} ${ext}`);

        const filesLabel = `${paths.length} file${paths.length === 1 ? '' : 's'} (${typeParts.join(', ')})`;

        return { destinationLabel, filesLabel };
    }

    function renderImportOutputLinks(outputs, vault, { compact = false } = {}) {
        const paths = normalizeOutputPaths(outputs);
        if (!paths.length) return '<span class="subtle">No output files</span>';
        const listClass = compact ? 'space-y-1' : 'space-y-1 ml-4';
        return `
            <ul class="${listClass}">
                ${paths.map((path) => `
                    <li class="break-words">
                        <button
                            type="button"
                            class="vault-file-link vault-file-link-code text-left break-all"
                            data-import-output-path="${escapeHtml(path)}"
                            data-import-output-vault="${escapeHtml(vault || '')}"
                            title="Open ${escapeHtml(path)} in the vault viewer"
                        >${escapeHtml(path)}</button>
                    </li>
                `).join('')}
            </ul>
        `;
    }

    function renderImportSource(source) {
        const value = String(source || 'unknown');
        let isWebUrl = false;
        try {
            const parsed = new URL(value);
            isWebUrl = parsed.protocol === 'http:' || parsed.protocol === 'https:';
        } catch (_error) {
            // Local inbox paths are expected and remain plain text.
        }
        if (!isWebUrl) return escapeHtml(value);
        return `<a class="vault-file-link break-all" href="${escapeHtml(value)}" target="_blank" rel="noopener noreferrer">${escapeHtml(value)}</a>`;
    }

    function renderImportJobs() {
        if (!elements.importJobsList) return;
        const jobs = Array.isArray(state.importJobs) ? state.importJobs : [];
        const selectedVault = (elements.importVaultSelect?.value || '').trim();
        if (!selectedVault) {
            if (elements.importJobsSummary) {
                elements.importJobsSummary.textContent = 'Select a vault to view import jobs.';
            }
            elements.importJobsLoadOlderBtn?.classList.add('hidden');
            elements.importJobsList.innerHTML = '<p>Select a vault to load its import history.</p>';
            syncImportJobPolling();
            return;
        }
        if (elements.importJobsSummary) {
            const selectedCounts = selectedImportJobStatuses().map(status => (
                `${Number(state.importJobStatusCounts[status] || 0)} ${status}`
            ));
            elements.importJobsSummary.textContent = state.importJobsTotalMatching
                ? `${selectedCounts.join(' · ')} · ${jobs.length} loaded`
                : 'No recent imports.';
        }
        elements.importJobsLoadOlderBtn?.classList.toggle('hidden', !state.importJobsNextCursor);
        if (!jobs.length) {
            elements.importJobsList.innerHTML = '<p>No import jobs match the selected statuses.</p>';
            syncImportJobPolling();
            return;
        }

        elements.importJobsList.innerHTML = `
            <div class="dashboard-table-wrap import-jobs-table-wrap" role="region" aria-label="Recent import jobs" tabindex="0">
                <table class="dashboard-table">
                    <thead>
                        <tr>
                            <th class="import-job-compact">Job</th>
                            <th class="import-job-source">Source</th>
                            <th class="import-job-compact">Status</th>
                            <th>Strategy</th>
                            <th class="import-job-compact">Updated</th>
                            <th>Output / Error</th>
                            <th class="cell-center import-job-compact">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${jobs.map(job => {
                            const outputs = Array.isArray(job.outputs) ? job.outputs : [];
                            const detail = job.error
                                ? escapeHtml(job.error)
                                : renderImportOutputLinks(outputs, job.vault, { compact: true });
                            const strategy = [
                                job.selected_strategy,
                                job.selected_provider,
                                job.selected_model
                            ].filter(Boolean).join(' · ') || '—';
                            const cancelButton = job.status === 'queued'
                                ? `<button data-import-job-cancel="${escapeHtml(job.id)}" ${iconButton('x', `Cancel import job ${job.id}`, 'is-danger')}>${iconSvg('x')}</button>`
                                : '';
                            const editButton = job.source_type === 'url'
                                ? `<button data-import-job-edit="${escapeHtml(job.id)}" ${iconButton('edit', `Edit import settings for job ${job.id}`, 'is-primary')}>${iconSvg('edit')}</button>`
                                : '';
                            return `
                                <tr>
                                    <td data-label="Job" class="cell-xs cell-mono import-job-compact">${escapeHtml(job.id)}</td>
                                    <td data-label="Source" class="cell-xs import-job-source">${renderImportSource(job.source_uri)}</td>
                                    <td data-label="Status" class="cell-xs import-job-compact">${escapeHtml(job.status || 'unknown')}</td>
                                    <td data-label="Strategy" class="cell-xs">${escapeHtml(strategy)}</td>
                                    <td data-label="Updated" class="cell-xs import-job-compact">${escapeHtml(formatDateTime(job.updated_at))}</td>
                                    <td data-label="Output / Error" class="cell-xs">${detail}</td>
                                    <td data-label="Actions" class="cell-center import-job-actions import-job-compact"><div class="import-job-action-buttons">${cancelButton}${editButton}</div></td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        `;
        syncImportJobPolling();
    }

    function selectedImportJobStatuses() {
        if (!elements.importJobsStatusFilters) return [...DEFAULT_IMPORT_JOB_STATUSES];
        return Array.from(elements.importJobsStatusFilters.querySelectorAll('input[type="checkbox"]:checked'))
            .map(input => input.value);
    }

    function handleImportJobFilterChange(event) {
        if (!selectedImportJobStatuses().length && event.target instanceof HTMLInputElement) {
            event.target.checked = true;
            return;
        }
        if (state.isLoadingImportJobs) {
            state.pendingImportJobsReload = true;
        } else {
            loadImportJobs();
        }
    }

    function syncImportJobPolling() {
        const hasActiveJobs = (state.importJobs || []).some(
            job => job.status === 'queued' || job.status === 'processing'
        );
        if (hasActiveJobs && !importJobPollTimer) {
            importJobPollTimer = window.setInterval(() => loadImportJobs({ silent: true }), 3000);
        } else if (!hasActiveJobs && importJobPollTimer) {
            window.clearInterval(importJobPollTimer);
            importJobPollTimer = null;
        }
    }

    async function loadImportJobs({ silent = false, append = false } = {}) {
        if (state.isLoadingImportJobs) {
            state.pendingImportJobsReload = true;
            return;
        }
        if (append && !state.importJobsNextCursor) return;
        const selectedVault = (elements.importVaultSelect?.value || '').trim();
        if (!selectedVault) {
            state.importJobs = [];
            state.importJobsNextCursor = null;
            state.importJobsTotalMatching = 0;
            state.importJobStatusCounts = {};
            state.hasLoadedImportJobs = true;
            renderImportJobs();
            return;
        }
        state.isLoadingImportJobs = true;
        if (!silent) setStatus(elements.importJobsFeedback, 'Refreshing import status…', 'info');
        try {
            const refreshLimit = silent && !append
                ? Math.min(Math.max(state.importJobs.length, 25), 100)
                : 25;
            const params = new URLSearchParams({ limit: String(refreshLimit) });
            params.set('vault', selectedVault);
            const selectedStatuses = selectedImportJobStatuses();
            selectedStatuses.forEach(status => params.append('status', status));
            if (append) params.set('cursor', state.importJobsNextCursor);
            const scrollTop = elements.importJobsList
                ?.querySelector('.import-jobs-table-wrap')?.scrollTop || 0;
            const response = await fetch(`api/import/jobs?${params.toString()}`, { cache: 'no-store' });
            const data = await safeJson(response);
            if (!response.ok) throw new Error(data?.message || `HTTP ${response.status}`);
            if ((elements.importVaultSelect?.value || '').trim() !== selectedVault) return;
            if (selectedImportJobStatuses().join('\u0000') !== selectedStatuses.join('\u0000')) return;
            const received = Array.isArray(data?.jobs) ? data.jobs : [];
            let nextJobs;
            if (append) {
                const knownIds = new Set(state.importJobs.map(job => job.id));
                nextJobs = [
                    ...state.importJobs,
                    ...received.filter(job => !knownIds.has(job.id))
                ];
            } else {
                nextJobs = received;
            }
            const nextCursor = data?.next_cursor || null;
            const nextTotalMatching = Number(data?.total_matching || 0);
            const nextStatusCounts = data?.status_counts || {};
            const viewChanged = (
                !state.hasLoadedImportJobs
                || JSON.stringify(nextJobs) !== JSON.stringify(state.importJobs)
                || nextCursor !== state.importJobsNextCursor
                || nextTotalMatching !== state.importJobsTotalMatching
                || JSON.stringify(nextStatusCounts) !== JSON.stringify(state.importJobStatusCounts)
            );
            state.importJobs = nextJobs;
            state.importJobsNextCursor = nextCursor;
            state.importJobsTotalMatching = nextTotalMatching;
            state.importJobStatusCounts = nextStatusCounts;
            state.hasLoadedImportJobs = true;
            if (viewChanged) {
                renderImportJobs();
                const tableWrap = elements.importJobsList?.querySelector('.import-jobs-table-wrap');
                if (tableWrap) tableWrap.scrollTop = scrollTop;
            }
            if (!silent) setStatus(elements.importJobsFeedback, '', 'info');
        } catch (error) {
            if (!silent) setStatus(elements.importJobsFeedback, `Failed to load imports: ${error.message}`, 'error');
        } finally {
            state.isLoadingImportJobs = false;
            if (state.pendingImportJobsReload) {
                state.pendingImportJobsReload = false;
                loadImportJobs();
            }
        }
    }

    async function handleImportJobAction(event) {
        const target = event.target;
        if (!(target instanceof Element)) return;
        const outputLink = target.closest('[data-import-output-path]');
        if (outputLink instanceof HTMLElement) {
            const path = outputLink.getAttribute('data-import-output-path');
            const vault = outputLink.getAttribute('data-import-output-vault');
            if (path && callbacks.openFile) callbacks.openFile(path, vault || undefined);
            return;
        }
        const button = target.closest('[data-import-job-cancel], [data-import-job-edit]');
        if (!(button instanceof HTMLElement) || button.disabled) return;
        const editJobId = button.getAttribute('data-import-job-edit');
        if (editJobId) {
            const job = state.importJobs.find(item => String(item.id) === editJobId);
            if (!job || job.source_type !== 'url') return;
            if (elements.importVaultSelect) elements.importVaultSelect.value = job.vault || '';
            if (elements.importPdfModeSelect && elements.importPdfStrategySelect) {
                if (job.selected_strategy === 'pdf_page_images') {
                    elements.importPdfModeSelect.value = 'page_images';
                } else {
                    elements.importPdfModeSelect.value = 'markdown';
                    if (job.selected_strategy === 'pdf_ocr') {
                        elements.importPdfStrategySelect.value = 'ocr';
                    } else if (job.selected_strategy === 'pdf_text') {
                        elements.importPdfStrategySelect.value = 'local_text';
                    } else {
                        elements.importPdfStrategySelect.value = 'default';
                    }
                }
                updateImportOcrAvailability();
            }
            if (elements.importUrlInput) {
                elements.importUrlInput.value = job.source_uri || '';
                elements.importUrlInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
                elements.importUrlInput.focus({ preventScroll: true });
            }
            setStatus(
                elements.importStatus,
                `Loaded URL from job ${editJobId}. Adjust PDF/OCR settings and import again.`,
                'info'
            );
            return;
        }
        const jobId = button.getAttribute('data-import-job-cancel');
        if (!jobId) return;
        button.disabled = true;
        setStatus(elements.importJobsFeedback, `Cancelling import job ${jobId}…`, 'info');
        try {
            const response = await fetch(
                `api/import/jobs/${encodeURIComponent(jobId)}/cancel`,
                { method: 'POST' }
            );
            const data = await safeJson(response);
            if (!response.ok) throw new Error(data?.message || `HTTP ${response.status}`);
            setStatus(elements.importJobsFeedback, `Import job ${jobId} cancelled.`, 'success');
            await loadImportJobs({ silent: true });
        } catch (error) {
            setStatus(
                elements.importJobsFeedback,
                `Could not cancel import: ${error.message}`,
                'error'
            );
            button.disabled = false;
        }
    }

    async function handleRunImportQueueNow() {
        if (!elements.importJobsRunNowBtn || state.isTriggeringImportQueue) return;
        state.isTriggeringImportQueue = true;
        elements.importJobsRunNowBtn.disabled = true;
        setStatus(elements.importJobsFeedback, 'Requesting an ingestion worker run…', 'info');
        try {
            const response = await fetch('api/import/run-now', { method: 'POST' });
            const data = await safeJson(response);
            if (!response.ok) throw new Error(data?.message || `HTTP ${response.status}`);
            const queuedCount = Number(data?.queued_count || 0);
            setStatus(
                elements.importJobsFeedback,
                queuedCount
                    ? `Worker run requested for ${queuedCount} queued import${queuedCount === 1 ? '' : 's'}.`
                    : 'Worker run requested; the queue is currently empty.',
                'success'
            );
            await loadImportJobs({ silent: true });
        } catch (error) {
            setStatus(elements.importJobsFeedback, `Could not run imports: ${error.message}`, 'error');
        } finally {
            state.isTriggeringImportQueue = false;
            elements.importJobsRunNowBtn.disabled = false;
        }
    }

    function renderImportResults() {
        if (!elements.importResults) return;
        const results = state.importResults;
        const urlResult = state.importUrlResult;

        let sections = [];

        if (results) {
            const jobs = Array.isArray(results.jobs_created) ? results.jobs_created : [];
            const skipped = Array.isArray(results.skipped) ? results.skipped : [];
            let html = '';
            if (jobs.length > 0) {
                html += `<div class="space-y-2"><div class="font-medium text-txt-primary">File imports (${jobs.length})</div>`;
                html += '<ul class="list-disc list-inside space-y-1">';
                html += jobs
                    .map((job) => {
                        const outputSummary = summarizeImportOutputs(job.outputs);
                        const status = job.status || 'queued';
                        const source = job.source_uri || 'unknown';
                        return `
                            <li>
                                <span class="text-txt-primary font-medium">${renderImportSource(source)}</span>
                                <span class="subtle">(${escapeHtml(status)})</span>
                                <div class="text-xs text-txt-secondary ml-4">
                                    Destination: <span class="text-txt-primary">${escapeHtml(outputSummary.destinationLabel)}</span>
                                </div>
                                <div class="text-xs text-txt-secondary ml-4">
                                    Files: ${escapeHtml(outputSummary.filesLabel)}
                                </div>
                                <div class="text-xs text-txt-secondary ml-4">Import path:</div>
                                ${renderImportOutputLinks(job.outputs, job.vault)}
                            </li>
                        `;
                    })
                    .join('');
                html += '</ul></div>';
            }
            if (skipped.length > 0) {
                html += `<div class="space-y-2 pt-3 border-t border-border-primary"><div class="font-medium text-txt-primary">Skipped (${skipped.length})</div>`;
                html += '<ul class="list-disc list-inside space-y-1">';
                html += skipped.map((name) => `<li>${escapeHtml(name)}</li>`).join('');
                html += '</ul></div>';
            }
            if (html) sections.push(html);
        }

        if (urlResult) {
            const outputSummary = summarizeImportOutputs(urlResult.outputs);
            const status = urlResult.status || 'unknown';
            const error = urlResult.error;
            const source = urlResult.source_uri || urlResult.url || 'unknown';

            let html = `<div class="space-y-2"><div class="font-medium text-txt-primary">Latest URL import</div>`;
            html += `<div class="text-sm"><span class="text-txt-primary font-medium">${renderImportSource(source)}</span> <span class="subtle">(${escapeHtml(status)})</span></div>`;
            html += `<div class="text-sm text-txt-secondary">Destination: <span class="text-txt-primary">${escapeHtml(outputSummary.destinationLabel)}</span></div>`;
            html += `<div class="text-sm text-txt-secondary">Files: ${escapeHtml(outputSummary.filesLabel)}</div>`;
            html += '<div class="text-sm text-txt-secondary">Import path:</div>';
            html += renderImportOutputLinks(urlResult.outputs, urlResult.vault);
            if (error) {
                html += `<div class="text-sm state-error">Error: ${escapeHtml(error)}</div>`;
            }
            html += '</div>';
            sections.push(html);
        }

        elements.importResults.innerHTML = sections.join('<div class="pt-3 border-t border-border-primary"></div>') || 'No imports yet.';
    }

    async function handleImportScan() {
        if (!elements.importScanBtn || state.isScanningImport) return;

        const vault = elements.importVaultSelect?.value || '';
        if (!vault) {
            setStatus(elements.importStatus, 'Select a vault before scanning.', 'warning');
            return;
        }

        const queueOnly = Boolean(elements.importQueueCheckbox?.checked);
        const captureOcrImages = Boolean(elements.importCaptureOcrImagesCheckbox?.checked);
        const pdfMode = (elements.importPdfModeSelect?.value || 'markdown').trim();
        const pdfStrategies = selectedPdfStrategyOverride();

        const payload = { vault, queue_only: queueOnly };
        if (pdfMode === 'page_images') {
            payload.pdf_mode = 'page_images';
        }
        if (pdfStrategies) payload.strategies = pdfStrategies;
        if (captureOcrImages) {
            payload.capture_ocr_images = true;
        }
        addOcrEnrichmentOptions(payload);

        state.isScanningImport = true;
        // Reset URL status when starting a file import
        state.importUrlResult = null;
        renderImportResults();
        const btn = elements.importScanBtn;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Import Files';
        btn.disabled = true;
        setIconButtonLabel(btn, queueOnly ? 'Queueing import jobs...' : 'Importing files...');
        setStatus(
            elements.importStatus,
            queueOnly ? 'Queueing import jobs…' : 'Scanning import folder and processing…',
            'info'
        );

        try {
            const response = await fetch('api/import/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            state.importResults = data;
            renderImportResults();
            await loadImportJobs({ silent: true });
            setStatus(
                elements.importStatus,
                queueOnly ? 'Jobs queued.' : 'Import completed.',
                'success'
            );
            if (callbacks.refreshStatus) callbacks.refreshStatus();
        } catch (error) {
            setStatus(elements.importStatus, `Import failed: ${error.message}`, 'error');
        } finally {
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
            state.isScanningImport = false;
        }
    }

    async function handleImportUrl() {
        if (!elements.importUrlSubmit || state.isImportingUrl) return;

        const vault = elements.importVaultSelect?.value || '';
        const url = (elements.importUrlInput?.value || '').trim();
        if (!vault || !url) {
            setStatus(elements.importStatus, 'Select a vault and enter a URL.', 'warning');
            return;
        }

        // Reset file status when starting a URL import
        state.importResults = null;
        renderImportResults();
        state.isImportingUrl = true;
        const btn = elements.importUrlSubmit;
        const originalLabel = btn.dataset.iconLabel || btn.title || 'Import URL';
        btn.disabled = true;
        setIconButtonLabel(btn, 'Ingesting URL...');
        setStatus(elements.importStatus, 'Ingesting URL…', 'info');

        const captureOcrImages = Boolean(elements.importCaptureOcrImagesCheckbox?.checked);
        const pdfMode = (elements.importPdfModeSelect?.value || 'markdown').trim();
        const pdfStrategies = selectedPdfStrategyOverride();
        const payload = { vault, url, clean_html: true, pdf_mode: pdfMode };
        if (pdfStrategies) payload.pdf_strategies = pdfStrategies;
        if (captureOcrImages) payload.capture_ocr_images = true;
        addOcrEnrichmentOptions(payload);

        try {
            const response = await fetch('api/import/url', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            state.importUrlResult = data;
            await loadImportJobs({ silent: true });
            setStatus(elements.importStatus, 'URL ingested.', data.error ? 'warning' : 'success');
            if (callbacks.refreshStatus) callbacks.refreshStatus();
            renderImportResults();
        } catch (error) {
            setStatus(elements.importStatus, `Import failed: ${error.message}`, 'error');
        } finally {
            btn.disabled = false;
            setIconButtonLabel(btn, originalLabel);
            state.isImportingUrl = false;
        }
    }

    function addOcrEnrichmentOptions(payload) {
        if (elements.importIncludeOcrBlocksCheckbox?.checked) payload.include_ocr_blocks = true;
        if (elements.importExtractOcrHeaderCheckbox?.checked) payload.extract_ocr_header = true;
        if (elements.importExtractOcrFooterCheckbox?.checked) payload.extract_ocr_footer = true;
        const tableFormat = (elements.importOcrTableFormatSelect?.value || '').trim();
        const confidence = (elements.importOcrConfidenceSelect?.value || '').trim();
        if (tableFormat) payload.ocr_table_format = tableFormat;
        if (confidence) payload.ocr_confidence = confidence;
    }

    function selectedPdfStrategyOverride() {
        const selected = elements.importPdfStrategySelect?.value || 'default';
        if (selected === 'local_text') return ['pdf_text'];
        if (selected === 'ocr') return ['pdf_ocr'];
        return null;
    }

    window.ConfigurationPanel = {
        init,
        onTabActivated,
        onTabDeactivated,
        onDashboardActivated,
        refreshActivityLog,
        onMetadataUpdated: updateImportOcrAvailability,
        setRestartRequired: externalSetRestartRequired
    };
}(window, document));
