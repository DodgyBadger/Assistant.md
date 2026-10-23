(function configurationFeature(window, document) {
    const runtime = window.ConfigurationPanelRuntime;
    if (!runtime) throw new Error('ConfigurationPanelRuntime must load first.');

    const { actions, elements, helpers, state } = runtime;
    const { escapeHtml, iconButton, iconSvg, notifyConfigChanged, safeJson, setStatus, withRestartNotice } = helpers;

    let settingsRequestId = 0;
    let editRequestId = 0;
    let metadataRefreshPromise = null;
    let metadataRefreshRequested = false;

    async function loadGeneralSettings(preserveDraft = false, { silent = false } = {}) {
        if (!elements.settingsList) return;
        const requestId = ++settingsRequestId;
        if (!preserveDraft) ++editRequestId;

        state.isLoadingSettings = true;
        if (!silent) setStatus(elements.settingsFeedback, 'Loading settings…', 'info');

        try {
            const response = await fetch('api/system/settings/general');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            if (requestId !== settingsRequestId) return;
            if (!Array.isArray(data)) throw new Error('Invalid settings response');
            state.settings = data;
            state.settingsValidationError = '';
            if (!preserveDraft) {
                state.settingEditKey = null;
                state.settingDraftValue = '';
            }
            renderSettings();
            actions.updateImportOcrAvailability();
            if (!silent) setStatus(elements.settingsFeedback, '', 'info');
        } catch (error) {
            if (requestId !== settingsRequestId) return;
            state.settingsValidationError = `Settings validation is unavailable: ${error.message}`;
            renderSettings(true);
            if (!silent) setStatus(elements.settingsFeedback, `Failed to load settings: ${error.message}`, 'error');
        } finally {
            if (requestId === settingsRequestId) state.isLoadingSettings = false;
        }
    }

    function setSettingMutationBusy(busy) {
        // Invalidate reads from before this mutation, including late edit preflights.
        ++settingsRequestId;
        state.isLoadingSettings = false;
        if (busy) ++editRequestId;
        state.isSavingSetting = busy;
        if (elements.settingsUseDefaults) elements.settingsUseDefaults.disabled = busy;
        renderSettings();
    }

    function refreshSettingMetadata() {
        metadataRefreshRequested = true;
        if (!metadataRefreshPromise) {
            metadataRefreshPromise = (async () => {
                while (metadataRefreshRequested) {
                    metadataRefreshRequested = false;
                    await notifyConfigChanged({ refreshSettings: false });
                }
            })().catch((error) => {
                console.error('Failed to refresh configuration after a setting change:', error);
            }).finally(() => {
                metadataRefreshPromise = null;
                // Include requests arriving as the previous refresh settles.
                if (metadataRefreshRequested) return refreshSettingMetadata();
            });
        }
        return metadataRefreshPromise;
    }

    async function refreshAfterSettingMutation() {
        // Update visible settings independently of potentially slow metadata/status.
        await Promise.all([
            loadGeneralSettings(true, { silent: true }),
            refreshSettingMetadata(),
        ]);
    }

    function renderSettings(emptyOnError = false) {
        if (!elements.settingsList) return;
        renderSettingsValidation();

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
            <div class="setting-card text-txt-primary rounded-lg border border-border-primary bg-app-card px-5 py-4 shadow-sm hover:shadow transition-shadow" data-setting="${escapeHtml(setting.key)}" data-mode="view" style="max-width: 1400px;">
                <div class="flex flex-col md:flex-row gap-4 md:items-center md:justify-between">
                    <div class="flex-1">
                        <div class="flex items-center gap-2 flex-wrap"><span class="font-semibold text-txt-primary text-sm">${escapeHtml(setting.key)}</span><span class="text-xs text-txt-secondary border border-border-secondary rounded px-2 py-1">${setting.source === 'default' ? 'Default' : 'Custom'}</span></div>
                        ${description}
                        ${renderSettingIssues(setting)}
                    </div>
                    <div class="flex items-center gap-4">
                        <div class="text-sm text-txt-primary font-mono bg-app-elevated px-3 py-2 rounded border border-border-primary break-words inline-block max-w-xs">${escapeHtml(formatSettingDisplay(setting))}</div>
                        ${setting.source === 'custom' && setting.default_value != null ? `<button data-action="restore-setting-default" ${state.isSavingSetting ? 'disabled' : ''} ${iconButton('undo', 'Use default', 'shrink-0')}>${iconSvg('undo')}</button>` : ''}
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
            <div class="setting-card text-txt-primary rounded-lg border border-border-primary bg-app-card editing-highlight px-5 py-4 shadow-sm" data-setting="${escapeHtml(setting.key)}" data-mode="edit" style="max-width: 1400px;">
                <div class="space-y-4">
                    <div>
                        <div class="flex items-center gap-2 flex-wrap"><span class="font-semibold text-txt-primary text-sm">${escapeHtml(setting.key)}</span><span class="text-xs text-txt-secondary border border-border-secondary rounded px-2 py-1">${setting.source === 'default' ? 'Default' : 'Custom'}</span></div>
                        ${description}
                    </div>
                    <div class="space-y-2">
                        ${renderSettingIssues(setting)}
                        ${(setting.issues || []).length ? `<p class="text-xs text-txt-secondary break-words">Saved value: <code>${escapeHtml(setting.value)}</code></p>` : ''}
                        ${renderSettingControl(setting, draftValue)}
                    </div>
                    <div class="flex justify-end gap-2">
                        <button data-action="cancel-setting" ${iconButton('circleX', 'Cancel setting edit')}>${iconSvg('circleX')}</button>
                        <button data-action="save-setting" ${iconButton('save', 'Save setting', 'is-primary')}>${iconSvg('save')}</button>
                    </div>
                </div>
            </div>
        `;
    }

    function renderSettingsValidation() {
        if (!elements.settingsValidation) return;
        const problems = state.settings.flatMap((setting) =>
            (setting.issues || []).map((issue) => `${setting.key}: ${issue.message}`));
        if (state.settingsValidationError) problems.unshift(state.settingsValidationError);
        elements.settingsValidation.hidden = problems.length === 0;
        elements.settingsValidation.innerHTML = problems.length
            ? `<p>Some settings need attention.</p><ul class="list-disc pl-5">${problems.map((problem) => `<li>${escapeHtml(problem)}</li>`).join('')}</ul>`
            : '';
    }

    function renderSettingIssues(setting) {
        return (setting.issues || []).length
            ? `<div class="text-sm state-warning mt-1">${setting.issues.map((issue) => escapeHtml(issue.message)).join('<br>')}</div>`
            : '';
    }

    function formatSettingDisplay(setting) {
        if (setting.editor?.kind === 'boolean' && !(setting.issues || []).length) {
            return setting.value === 'true' ? 'On' : 'Off';
        }
        if (['string_list', 'ordered_list', 'multiselect'].includes(setting.editor?.kind)) {
            try {
                const items = JSON.parse(setting.value);
                if (Array.isArray(items) && items.every((item) => typeof item === 'string')) return items.join(', ') || 'None';
            } catch { /* Invalid configured values remain visible as saved. */ }
        }
        if (setting.editor?.kind === 'select' && !(setting.issues || []).length) {
            return setting.editor.choices.find((choice) => choice.value === setting.edit_value)?.label || setting.value;
        }
        return setting.value ?? '';
    }

    const settingInputClass = 'w-full min-w-0 px-3 py-2 border border-border-secondary rounded-md focus:outline-none focus:ring-2 focus:ring-accent focus:border-accent font-mono text-sm bg-app-card text-txt-primary transition-colors';

    function renderSettingControl(setting, value) {
        const editor = setting.editor || { kind: 'text' };
        const label = escapeHtml(setting.key);
        if (['boolean', 'select'].includes(editor.kind)) {
            const available = editor.choices.some((choice) => choice.value === value);
            return `<select data-field="setting-value" aria-label="${label}" class="${settingInputClass}">
                ${available ? '' : `<option value="${escapeHtml(value)}" selected disabled>${escapeHtml(value)} (unavailable)</option>`}
                ${editor.choices.map((choice) => `<option value="${escapeHtml(choice.value)}" ${choice.value === value ? 'selected' : ''}>${escapeHtml(choice.label)}</option>`).join('')}
            </select>`;
        }
        if (['multiselect', 'ordered_list', 'string_list'].includes(editor.kind)) {
            let items;
            try { items = JSON.parse(value); } catch { items = null; }
            if (!Array.isArray(items)) {
                return `<p class="text-sm state-warning">Saved value: ${escapeHtml(value)}. Replace it to edit this list.</p><button type="button" data-action="list-reset" class="ui-button-secondary">Replace with an empty list</button>`;
            }
            const help = editor.help ? `<p class="text-xs text-txt-secondary">${escapeHtml(editor.help)}</p>` : '';
            if (editor.kind === 'multiselect') {
                const unknown = items.map((item, index) => ({ item, index })).filter(({ item }) => !editor.choices.some((choice) => choice.value === item));
                return `<fieldset class="min-w-0"><legend class="text-xs font-medium text-txt-primary mb-1.5">${label}</legend><div class="flex flex-wrap gap-2">${editor.choices.map((choice) => `<div class="tool-checkbox-wrapper setting-choice-pill"><label><input type="checkbox" class="sr-only" data-field="setting-list-choice" value="${escapeHtml(choice.value)}" ${items.includes(choice.value) ? 'checked' : ''}><span class="tool-checkbox-name">${escapeHtml(choice.label)}</span></label></div>`).join('')}</div>
                    ${unknown.map(({ item, index }) => `<div>${escapeHtml(JSON.stringify(item))} (unavailable) <button type="button" data-action="list-remove" data-index="${index}" class="ui-button-secondary">Remove</button></div>`).join('')}</fieldset>${help}`;
            }
            const rows = items.map((item, index) => `<div class="flex items-center gap-2" data-list-row>
                ${editor.kind === 'string_list'
                    ? `<input data-field="setting-list-item" data-index="${index}" aria-label="${label} item ${index + 1}" class="${settingInputClass}" value="${escapeHtml(typeof item === 'string' ? item : JSON.stringify(item))}">`
                    : `<span class="flex-1 min-w-0 break-words text-sm">${index + 1}. ${escapeHtml(editor.choices.find((choice) => choice.value === item)?.label || JSON.stringify(item))}</span>`}
                ${editor.kind === 'ordered_list' ? `<button type="button" data-action="list-up" data-index="${index}" aria-label="Move item ${index + 1} up" ${index === 0 ? 'disabled' : ''} class="ui-button-secondary">↑</button><button type="button" data-action="list-down" data-index="${index}" aria-label="Move item ${index + 1} down" ${index === items.length - 1 ? 'disabled' : ''} class="ui-button-secondary">↓</button>` : ''}
                <button type="button" data-action="list-remove" data-index="${index}" aria-label="Remove item ${index + 1}" class="ui-button-secondary">Remove</button>
            </div>`).join('');
            const choices = (editor.choices || []).filter((choice) => !items.includes(choice.value));
            return `<fieldset class="min-w-0 space-y-2"><legend class="text-xs font-medium text-txt-primary mb-1.5">${label}</legend>${rows}
                ${editor.kind === 'ordered_list' ? `<select data-field="setting-add-choice" aria-label="Add ${label} strategy" class="${settingInputClass}" ${choices.length ? '' : 'disabled'}>${choices.map((choice) => `<option value="${escapeHtml(choice.value)}">${escapeHtml(choice.label)}</option>`).join('')}</select>` : ''}
                <button type="button" data-action="list-add" class="ui-button-secondary" ${editor.kind === 'ordered_list' && !choices.length ? 'disabled' : ''}>Add</button>
            </fieldset>${help}`;
        }
        const numeric = ['integer', 'number'].includes(editor.kind);
        const bounds = numeric
            ? `${editor.minimum !== null && !editor.exclusive_minimum ? ` min="${editor.minimum}"` : ''}${editor.maximum !== null ? ` max="${editor.maximum}"` : ''} step="${editor.kind === 'integer' ? '1' : 'any'}" required`
            : '';
        return `<input data-field="setting-value" type="${numeric ? 'number' : 'text'}" aria-label="${label}" class="${settingInputClass}" value="${escapeHtml(value)}"${bounds}>`;
    }

    function parseListDraft() {
        try {
            const items = JSON.parse(state.settingDraftValue);
            return Array.isArray(items) ? items : null;
        } catch { return null; }
    }

    function handleSettingListAction(action, button, card) {
        if (state.isSavingSetting || state.settingEditKey !== card.dataset.setting) return;
        const setting = state.settings.find((entry) => entry.key === state.settingEditKey);
        let items = parseListDraft();
        if (action === 'list-reset') items = [];
        if (!items) return;
        const index = Number(button.dataset.index);
        if (action === 'list-remove') items.splice(index, 1);
        if (action === 'list-up' && index > 0) [items[index - 1], items[index]] = [items[index], items[index - 1]];
        if (action === 'list-down' && index < items.length - 1) [items[index + 1], items[index]] = [items[index], items[index + 1]];
        if (action === 'list-add') {
            const choice = card.querySelector('[data-field="setting-add-choice"]')?.value;
            if (setting.editor.kind === 'ordered_list') {
                if (!choice || items.includes(choice)) return;
                items.push(choice);
            } else items.push('');
        }
        state.settingDraftValue = JSON.stringify(items);
        renderSettings();
        requestAnimationFrame(() => {
            const current = elements.settingsList?.querySelector('[data-mode="edit"]');
            const target = current?.querySelector(action === 'list-add'
                ? `[data-field="setting-list-item"][data-index="${items.length - 1}"]`
                : `[data-action="list-add"]`);
            target?.focus();
        });
    }

    function validateSettingDraft(setting, raw) {
        const editor = setting?.editor;
        if (!editor) return '';
        if (['select', 'boolean'].includes(editor.kind) && !editor.choices.some((choice) => choice.value === raw)) return 'Select an available option.';
        if (['integer', 'number'].includes(editor.kind)) {
            const value = Number(raw);
            if (!raw.trim() || !Number.isFinite(value)) return 'Enter a finite number.';
            if (editor.kind === 'integer' && !Number.isInteger(value)) return 'Enter a whole number.';
            if (editor.minimum !== null && (value < editor.minimum || (editor.exclusive_minimum && value === editor.minimum))) return `Value must be ${editor.exclusive_minimum ? 'greater than' : 'at least'} ${editor.minimum}.`;
            if (editor.maximum !== null && value > editor.maximum) return `Value must be at most ${editor.maximum}.`;
        }
        if (['multiselect', 'ordered_list', 'string_list'].includes(editor.kind)) {
            let items;
            try { items = JSON.parse(raw); } catch { return 'Enter a valid list.'; }
            if (!Array.isArray(items) || items.some((item) => typeof item !== 'string' || !item.trim())) return 'Each list entry must be a non-empty string.';
            if (items.length < editor.min_items) return `Select at least ${editor.min_items} item(s).`;
            if (editor.unique_items && new Set(items).size !== items.length) return 'Remove duplicate list entries.';
            if (editor.kind !== 'string_list' && items.some((item) => !editor.choices.some((choice) => choice.value === item))) return 'Remove or replace unavailable list entries.';
        }
        return '';
    }

    function handleSettingsTableClick(event) {
        const actionButton = event.target.closest('[data-action]');
        if (!actionButton) return;

        const card = actionButton.closest('[data-setting]');
        const settingKey = card?.dataset.setting;
        if (!settingKey) return;

        const action = actionButton.dataset.action;

        if (action.startsWith('list-')) {
            handleSettingListAction(action, actionButton, card);
        } else if (action === 'edit-setting') {
            startSettingEdit(settingKey);
        } else if (action === 'cancel-setting') {
            cancelSettingEdit();
        } else if (action === 'restore-setting-default') {
            restoreSettingDefault(settingKey);
        } else if (action === 'save-setting') {
            saveSettingValue(settingKey);
        }
    }

    function handleSettingsInputChange(event) {
        if (!state.settingEditKey || state.isSavingSetting) return;
        const target = event.target;
        if (target.dataset.field === 'setting-value') {
            state.settingDraftValue = target.value;
        } else if (target.dataset.field === 'setting-list-item') {
            const items = parseListDraft();
            if (!items) return;
            items[Number(target.dataset.index)] = target.value;
            state.settingDraftValue = JSON.stringify(items);
        } else if (target.dataset.field === 'setting-list-choice') {
            const items = parseListDraft();
            if (!items) return;
            const next = items.filter((item) => item !== target.value);
            if (target.checked) next.push(target.value);
            state.settingDraftValue = JSON.stringify(next);
        }
    }

    function handleSettingsFilterInput(event) {
        state.settingsFilter = event.target.value || '';
        renderSettings();
    }

    async function startSettingEdit(settingKey) {
        if (state.isSavingSetting) return;

        const requestId = ++editRequestId;
        await loadGeneralSettings(true);
        if (requestId !== editRequestId || state.isSavingSetting) return;
        const setting = state.settings.find((s) => s.key === settingKey);
        if (!setting) return;

        state.settingEditKey = setting.key;
        state.settingDraftValue = setting.edit_value ?? setting.value ?? '';
        renderSettings();
        setStatus(elements.settingsFeedback, `Editing '${setting.key}'.`, 'info');
        focusSettingInput();
    }

    function focusSettingInput() {
        requestAnimationFrame(() => {
            const card = elements.settingsList?.querySelector('[data-setting][data-mode="edit"]');
            const input = card?.querySelector('input:not([disabled]), select:not([disabled]), textarea, button:not([disabled])');
            if (input) input.focus();
        });
    }

    function cancelSettingEdit(message = true) {
        ++editRequestId;
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
        const setting = state.settings.find((entry) => entry.key === settingKey);
        const input = elements.settingsList?.querySelector('[data-mode="edit"] [data-field="setting-value"]');
        if (input && !input.reportValidity()) return;
        const draftError = validateSettingDraft(setting, value);
        if (draftError) {
            setStatus(elements.settingsFeedback, draftError, 'error');
            return;
        }
        setSettingMutationBusy(true);
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
            const resultMessage = withRestartNotice(`Saved setting '${result.key}'.`, result);
            setStatus(elements.settingsFeedback, resultMessage.text, resultMessage.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.settingsFeedback, `Failed to save setting: ${error.message}`, 'error');
        } finally {
            setSettingMutationBusy(false);
        }
        await refreshAfterSettingMutation();
    }


    async function restoreSettingDefault(settingKey) {
        if (state.isSavingSetting) return;
        setSettingMutationBusy(true);
        setStatus(elements.settingsFeedback, 'Restoring default…', 'info');
        try {
            const response = await fetch(`api/system/settings/general/${encodeURIComponent(settingKey)}/override`, { method: 'DELETE' });
            const result = await safeJson(response);
            if (!response.ok) throw new Error(result?.message || `HTTP ${response.status}`);
            state.settings = state.settings.map((setting) => setting.key === result.key ? result : setting);
            actions.updateImportOcrAvailability();
            renderSettings();
            const notice = withRestartNotice(`Setting '${result.key}' now uses its default.`, result);
            setStatus(elements.settingsFeedback, notice.text, notice.restart ? 'warning' : 'success');
            requestAnimationFrame(() => {
                const card = Array.from(elements.settingsList?.querySelectorAll?.('[data-setting]') || []).find((entry) => entry.dataset.setting === settingKey);
                card?.querySelector('[data-action="edit-setting"]')?.focus();
            });
        } catch (error) {
            setStatus(elements.settingsFeedback, `Failed to restore default: ${error.message}`, 'error');
        } finally {
            setSettingMutationBusy(false);
        }
        await refreshAfterSettingMutation();
    }

    async function restoreManagedDefaults() {
        if (state.isSavingSetting) return;
        const confirmed = window.confirm('Use shipped defaults for all application settings and managed configuration? Custom models, providers, tools, and secrets will be preserved. A backup will be saved first. Any unsaved configuration edits will be discarded.');
        if (!confirmed) return;
        setSettingMutationBusy(true);
        setStatus(elements.settingsFeedback, 'Restoring managed defaults…', 'info');
        try {
            const response = await fetch('api/system/settings/defaults', { method: 'POST' });
            const result = await safeJson(response);
            if (!response.ok) throw new Error(result?.message || `HTTP ${response.status}`);
            cancelSettingEdit(false);
            actions.cancelModelEdit?.(false);
            actions.cancelProviderEdit?.(false);
            // The mutation has committed; refresh other cards without holding its lock.
            void Promise.all([actions.loadModels?.({ fresh: true }), actions.loadProviders?.({ fresh: true })]).catch((error) => {
                console.error('Failed to refresh managed configuration:', error);
            });
            const notice = withRestartNotice(result.message, result);
            setStatus(elements.settingsFeedback, notice.text, notice.restart ? 'warning' : 'success');
        } catch (error) {
            setStatus(elements.settingsFeedback, `Failed to restore defaults: ${error.message}`, 'error');
        } finally {
            setSettingMutationBusy(false);
            elements.settingsUseDefaults?.focus();
        }
        await refreshAfterSettingMutation();
    }


    Object.assign(actions, {
        loadGeneralSettings,
        renderSettingsValidation,
        renderSettingControl,
        validateSettingDraft,
        handleSettingListAction,
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
        saveSettingValue,
        restoreSettingDefault,
        restoreManagedDefaults
    });
}(window, document));
