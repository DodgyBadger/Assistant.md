(function configurationStatusModule(window, document) {
    const RESTART_NOTICE_TEXT = 'Restart the container to apply changes.';
    const RESTART_STORAGE_KEY = 'assistantmd_restart_required';

    function createConfigurationStatus({ state, elements, browserStorage, icons, utils, callbacks }) {
        function getWarnings() {
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

        function update() {
            if (!elements.statusBanner || !elements.statusMessages || !elements.configTab) return;

            const warnings = getWarnings();
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

            if (state.metadata?.vaults?.length === 0) {
                noticeLines.push('No vaults found. Review installation instructions.');
            }

            if (noticeLines.length === 0) {
                elements.statusBanner.classList.add('hidden');
                elements.statusMessages.innerHTML = '';
                elements.configTab.classList.remove('font-semibold', 'bg-app-elevated', 'px-3', 'rounded-t-md', 'text-accent');
                elements.configTab.classList.add('text-txt-secondary');
                elements.configTab.style.borderColor = '';
                elements.configTab.textContent = 'System';
                return;
            }

            elements.statusBanner.classList.remove('hidden');
            const noticeHtml = noticeLines.map(line => `<div>• ${utils.escapeHtml(line)}</div>`).join('');
            let messageHtml = noticeHtml;
            if (repairNeeded) {
                messageHtml = `
                    <div class="flex flex-wrap items-start gap-2">
                        <button id="repair-settings-btn" type="button" class="ui-icon-button is-primary shrink-0" data-icon="wrench" data-icon-label="Repair settings from template"></button>
                        <div class="flex-1 min-w-0 space-y-1">${noticeHtml}</div>
                    </div>
                `;
            }
            elements.statusMessages.innerHTML = messageHtml;
            icons.hydrateIconButtons(elements.statusMessages);
            elements.configTab.classList.remove('text-txt-secondary', 'text-txt-primary');
            elements.configTab.classList.add('text-accent', 'font-semibold', 'bg-app-elevated', 'px-3', 'rounded-t-md');
            elements.configTab.style.borderColor = 'rgb(var(--border-primary))';
            elements.configTab.textContent = 'System ⚠️';
            const repairBtn = document.getElementById('repair-settings-btn');
            if (repairBtn) {
                repairBtn.addEventListener('click', async () => {
                    const confirmed = window.confirm(
                        'Repair settings from template?\n\nThis will add missing keys and metadata from settings.template.yaml, prune unknown settings, and remove unknown non-user-editable tools/models/providers. Existing values for matching keys will be preserved.\nA backup will be written to system/settings.bak. Reload the page after repair to see changes.'
                    );
                    if (!confirmed) return;

                    repairBtn.disabled = true;
                    icons.setIconButtonLabel(repairBtn, 'Repairing settings...');
                    let alertEl = document.getElementById('config-repair-alert');
                    if (!alertEl && elements.statusMessages) {
                        alertEl = document.createElement('div');
                        alertEl.id = 'config-repair-alert';
                        alertEl.className = 'mt-2 text-sm';
                        elements.statusMessages.appendChild(alertEl);
                    }
                    const showAlert = (text, tone = 'info') => {
                        if (!alertEl) return;
                        alertEl.textContent = text;
                        alertEl.className = `mt-2 text-sm ${tone === 'error' ? 'state-error' : 'text-txt-secondary'}`;
                    };
                    try {
                        const response = await fetch('api/system/settings/repair', { method: 'POST' });
                        if (!response.ok) throw new Error(await response.text() || 'Repair failed');
                        await callbacks.refreshStatus();
                        showAlert('Settings repaired. Backup saved to system/settings.bak. Reload the page to see new defaults.', 'info');
                    } catch (error) {
                        console.error('Settings repair failed', error);
                        showAlert(`Settings repair failed: ${error.message}`, 'error');
                    } finally {
                        repairBtn.disabled = false;
                        icons.setIconButtonLabel(repairBtn, 'Repair settings from template');
                    }
                });
            }
        }

        function setRestartRequired(required = true) {
            const currentStartup = state.systemStatus?.system?.startup_time || null;

            if (!required) {
                state.restartRequired = false;
                browserStorage.removeItem(RESTART_STORAGE_KEY);
                window.ConfigurationPanel?.setRestartRequired?.(false);
                update();
                return;
            }

            browserStorage.setItem(RESTART_STORAGE_KEY, JSON.stringify({
                required: true,
                startupTime: currentStartup
            }));
            state.restartRequired = true;
            window.ConfigurationPanel?.setRestartRequired?.(true);
            update();
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
                    browserStorage.setItem(RESTART_STORAGE_KEY, JSON.stringify({
                        required: true,
                        startupTime: currentStartup
                    }));
                }
                if (!state.restartRequired) {
                    state.restartRequired = true;
                    window.ConfigurationPanel?.setRestartRequired?.(true);
                    update();
                }
                return;
            }

            if (state.restartRequired) {
                state.restartRequired = false;
                window.ConfigurationPanel?.setRestartRequired?.(false);
                update();
            }
            browserStorage.removeItem(RESTART_STORAGE_KEY);
        }

        return Object.freeze({
            update,
            setRestartRequired,
            syncRestartFlagWithStorage,
        });
    }

    window.ConfigurationStatus = Object.freeze({
        create: createConfigurationStatus,
    });
})(window, document);
