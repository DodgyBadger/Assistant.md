(() => {
    'use strict';

    const originalFetch = window.fetch.bind(window);
    const unsafeMethods = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

    function readCookie(name) {
        const prefix = `${encodeURIComponent(name)}=`;
        for (const part of document.cookie.split(';')) {
            const candidate = part.trim();
            if (candidate.startsWith(prefix)) {
                return decodeURIComponent(candidate.slice(prefix.length));
            }
        }
        return null;
    }

    window.fetch = async (input, init = {}) => {
        const request = input instanceof Request ? input : null;
        const url = new URL(request ? request.url : String(input), window.location.href);
        const method = String(init.method || (request && request.method) || 'GET').toUpperCase();
        const options = { ...init };

        if (url.origin === window.location.origin && unsafeMethods.has(method)) {
            const csrfToken = readCookie('assistantmd_csrf');
            if (csrfToken) {
                const headers = new Headers(request ? request.headers : undefined);
                new Headers(init.headers || undefined).forEach((value, name) => {
                    headers.set(name, value);
                });
                headers.set('X-AssistantMD-CSRF', csrfToken);
                options.headers = headers;
            }
        }

        const response = await originalFetch(input, options);
        if (response.status === 401 && url.origin === window.location.origin) {
            window.location.assign('/auth/login');
        }
        return response;
    };
})();
