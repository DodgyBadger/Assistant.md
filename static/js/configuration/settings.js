(function configurationFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, callbacks, constants, elements, helpers, resources, state, timers } = runtime;
    const { ACTIVITY_LOG_LEVELS, BUILT_IN_PROVIDER_NAMES, DEFAULT_IMPORT_JOB_STATUSES, SECRET_METADATA } = constants;
    const { escapeHtml, formatDateTime, iconButton, iconSvg, notifyConfigChanged, safeJson, setIconButtonLabel, setStatus, withRestartNotice } = helpers;

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
            actions.updateImportOcrAvailability();
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
            actions.updateImportOcrAvailability();
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


    Object.assign(actions, {
        loadGeneralSettings,
        renderSettings,
        normalizeSearchText,
        settingMatchesFilter,
        groupSettingsByCategory,
        renderSettingViewCard,
        renderSettingEditCard,
        handleSettingsTableClick,
        handleSettingsInputChange,
        handleSettingsFilterInput,
        startSettingEdit,
        focusSettingInput,
        cancelSettingEdit,
        saveSettingValue
    });
}(window, document));
