/** Configuration panel lifecycle and public API. */
(function configurationModule(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, elements, state } = runtime;

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
        elements.importPageImageOptions = document.getElementById('import-page-image-options');
        elements.importCaptureOcrImagesCheckbox = document.getElementById('import-capture-ocr-images');
        elements.importStatus = document.getElementById('import-status');
        elements.importDefaultsSaveBtn = document.getElementById('import-defaults-save');
        elements.importOpenExplorerBtn = document.getElementById('import-open-explorer');
        elements.importRefreshVaultsBtn = document.getElementById('import-refresh-vaults');
        elements.importJobsSummary = document.getElementById('import-jobs-summary');
        elements.importJobsFeedback = document.getElementById('import-jobs-feedback');
        elements.importJobsList = document.getElementById('import-jobs-list');
        elements.importJobsRefreshBtn = document.getElementById('import-jobs-refresh');
        elements.importJobsRunNowBtn = document.getElementById('import-jobs-run-now');
        elements.importJobsStatusFilters = document.getElementById('import-jobs-status-filters');
        elements.importJobsLoadOlderBtn = document.getElementById('import-jobs-load-older');
    }

    function bindEvents() {
        elements.refreshActivityLogBtn?.addEventListener('click', () => actions.refreshActivityLog());
        elements.activityLogSearch?.addEventListener('input', actions.handleActivityLogSearchInput);
        elements.activityLogLevelTrigger?.addEventListener('click', () => actions.setActivityLogFilterMenuOpen('level', !state.activityLogFilterMenus.level));
        elements.activityLogLevelOptions?.addEventListener('change', actions.handleActivityLogFilterChange);
        elements.activityLogTagTrigger?.addEventListener('click', () => actions.setActivityLogFilterMenuOpen('tag', !state.activityLogFilterMenus.tag));
        elements.activityLogTagOptions?.addEventListener('change', actions.handleActivityLogFilterChange);
        elements.activityLogLatestFirst?.addEventListener('change', actions.handleActivityLogFilterChange);
        elements.activityLogLoadOlderBtn?.addEventListener('click', () => actions.refreshActivityLog({ append: true }));
        document.addEventListener('click', actions.handleActivityLogDocumentClick);

        elements.settingsList?.addEventListener('click', actions.handleSettingsTableClick);
        elements.settingsList?.addEventListener('input', actions.handleSettingsInputChange);
        elements.settingsFilter?.addEventListener('input', actions.handleSettingsFilterInput);

        elements.modelAddBtn?.addEventListener('click', actions.startNewModel);
        elements.modelList?.addEventListener('click', actions.handleModelTableClick);
        elements.modelList?.addEventListener('input', actions.handleModelInputChange);
        elements.modelList?.addEventListener('change', actions.handleModelInputChange);

        elements.providerAddBtn?.addEventListener('click', actions.startNewProvider);
        elements.providerList?.addEventListener('click', actions.handleProviderTableClick);
        elements.providerList?.addEventListener('input', actions.handleProviderInputChange);
        elements.providerList?.addEventListener('change', actions.handleProviderInputChange);

        elements.secretAddBtn?.addEventListener('click', actions.startNewSecret);
        elements.secretsList?.addEventListener('click', actions.handleSecretsTableClick);
        elements.secretsList?.addEventListener('input', actions.handleSecretInputChange);
        elements.connectionAddGoogle?.addEventListener('click', actions.startGoogleConnectionDraft);
        elements.connectionAddMcp?.addEventListener('click', () => {
            elements.mcpCreateForm?.classList.remove('hidden');
            elements.mcpCreateForm?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
        elements.googleConnectionsList?.addEventListener('submit', actions.saveGoogleConnection);
        elements.googleConnectionsList?.addEventListener('click', actions.handleGoogleConnectionAction);
        elements.mcpCreateForm?.addEventListener('submit', actions.handleMcpCreate);
        elements.mcpCreateForm?.addEventListener('click', (event) => {
            const parseImport = event.target instanceof Element ? event.target.closest('[data-mcp-create-action="parse-import"]') : null;
            if (parseImport) {
                void actions.parseMcpImport();
                return;
            }
            const cancel = event.target instanceof Element ? event.target.closest('[data-mcp-create-action="cancel"]') : null;
            if (!cancel) return;
            elements.mcpCreateForm.reset();
            elements.mcpCreateForm.classList.add('hidden');
            actions.updateMcpCreateAuthFields();
        });
        elements.mcpConnectionsList?.addEventListener('click', actions.handleMcpConnectionAction);
        elements.mcpCreateForm?.addEventListener('change', actions.updateMcpCreateAuthFields);
        elements.refreshSystemAuthoringBtn?.addEventListener('click', actions.handleRefreshSystemAuthoring);
        elements.purgeExpiredCacheBtn?.addEventListener('click', actions.handlePurgeExpiredCache);
        elements.cleanupVaultStateBtn?.addEventListener('click', actions.handleCleanupVaultState);
        elements.refreshSystemJobsBtn?.addEventListener('click', actions.loadSystemJobs);
        elements.refreshSystemMigrationsBtn?.addEventListener('click', actions.loadSystemMigrations);
        elements.runSystemMigrationsBtn?.addEventListener('click', actions.handleRunSystemMigrations);
        elements.purgeSessionsBtn?.addEventListener('click', actions.handlePurgeSessions);
        elements.cleanupGoalsBtn?.addEventListener('click', actions.handleCleanupGoals);

        elements.importDefaultsSaveBtn?.addEventListener('click', actions.saveImportDefaults);
        elements.importOpenExplorerBtn?.addEventListener('click', actions.openImportExplorer);
        elements.importRefreshVaultsBtn?.addEventListener('click', actions.handleImportVaultRescan);
        elements.importVaultSelect?.addEventListener('change', actions.handleImportVaultChange);
        elements.importJobsRefreshBtn?.addEventListener('click', () => actions.loadImportJobs());
        elements.importJobsRunNowBtn?.addEventListener('click', actions.handleRunImportQueueNow);
        elements.importJobsStatusFilters?.addEventListener('change', actions.handleImportJobFilterChange);
        elements.importJobsLoadOlderBtn?.addEventListener('click', () => actions.loadImportJobs({ append: true }));
        elements.importJobsList?.addEventListener('click', actions.handleImportJobAction);
        elements.importPdfModeSelect?.addEventListener('change', actions.updateImportDefaultControls);
        elements.importPdfStrategySelect?.addEventListener('change', actions.updateImportDefaultControls);
    }


    async function refreshAll() {
        await actions.refreshActivityLog();
        await actions.loadProviders();
        await actions.loadGeneralSettings();
        await actions.loadModels();
        await actions.loadSecrets();
        await actions.loadGoogleConnection();
        await actions.loadMcpConnections();
        await actions.loadSystemJobs();
        await actions.loadSystemMigrations();
        await actions.loadImportVaults();
        await actions.loadPurgeSessionsVaults();
        await actions.loadCleanupGoalsVaults();
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
        callbacks.openExplorer = options.openExplorer || null;

        cacheElements();
        bindEvents();
        window.addEventListener('pagehide', actions.cancelAllOAuthPolls);

        state.initialized = true;
    }

    function onTabActivated() {
        if (!state.initialized) return;
        refreshAll();
    }

    function onTabDeactivated() {
        actions.cancelAllOAuthPolls();
    }

    async function onDashboardActivated() {
        if (!state.initialized) return;
        await actions.loadSecrets();
        await actions.loadImportVaults();
        await actions.loadImportJobs();
    }


    window.ConfigurationPanel = {
        init,
        onTabActivated,
        onTabDeactivated,
        onDashboardActivated,
        refreshActivityLog: actions.refreshActivityLog,
        onMetadataUpdated: actions.updateImportOcrAvailability,
        setRestartRequired: externalSetRestartRequired
    };
}(window, document));
