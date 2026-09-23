(function appShellModule(window, document) {
    function createAppShell({ browserStorage, callbacks }) {
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

            apply(themeName) {
                const theme = this.themes.find(item => item.name === themeName) || this.themes[0];
                this.current = theme;
                document.documentElement.setAttribute('data-theme', theme.name);
                const button = document.getElementById('theme-toggle');
                if (button) button.title = `Theme: ${theme.label} (click to change)`;
                browserStorage.setItem('theme', theme.name);
            },

            cycle() {
                const currentIndex = this.themes.findIndex(item => item.name === this.current?.name);
                this.apply(this.themes[(currentIndex + 1) % this.themes.length].name);
            },

            init() {
                const saved = browserStorage.getItem('theme');
                const initialTheme = saved || (
                    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
                );
                this.apply(initialTheme);
                document.getElementById('theme-toggle')?.addEventListener('click', () => this.cycle());
            }
        };

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
                callbacks.refreshStatus();
                window.ConfigurationPanel?.onDashboardActivated?.();
            } else if (tabName === 'configuration') {
                callbacks.refreshStatus();
                window.ConfigurationPanel?.onTabActivated?.();
            }
        }

        function init() {
            themeManager.init();
            Object.entries(tabs).forEach(([name, tabControls]) => {
                if (tabControls.button) {
                    tabControls.button.addEventListener('click', () => switchTab(name));
                } else {
                    console.error(`Tab button not found for ${name}`, tabControls);
                }
            });
        }

        return Object.freeze({ init, switchTab });
    }

    window.AppShell = Object.freeze({ create: createAppShell });
})(window, document);
