(prefix) => {
    // Runs in a CDP isolated world: website scripts cannot replace these
    // prototypes or obtain the private node set/dispatch closure.
    const doc = document;
    const nativeClick = HTMLElement.prototype.click;
    const setters = {
        INPUT: Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set,
        TEXTAREA: Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set,
        SELECT: Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set,
    };
    const textTypes = new Set(['text', 'search', 'email', 'tel', 'url']);
    const nodes = [], signatures = [], rectangles = [];
    let active = true, dirty = false, optionBudget = 60;
    const viewport = [innerWidth, innerHeight, scrollX, scrollY];
    const sensitive = node => {
        const pieces = ['type', 'autocomplete', 'name', 'id', 'aria-label', 'placeholder']
            .map(key => node.getAttribute(key) || '');
        if (pieces.some(value => value.length > 256)) return true;
        for (let i = 0; i < Math.min(node.labels?.length || 0, 3); i++) pieces.push(boundedText(node.labels[i], 256, 256));
        const hints = pieces.join(' ');
        return /password|passwd|passcode|one.time.code|one.time.password|\botp\b|verification.?code|auth.?code/i.test(hints);
    };
    const rect = node => {
        const r = node.getBoundingClientRect();
        return [r.x, r.y, r.width, r.height];
    };
    const visible = node => {
        const r = node.getBoundingClientRect(), style = getComputedStyle(node);
        return r.width > 0 && r.height > 0 && style.visibility === 'visible' &&
            style.display !== 'none' && Number(style.opacity) > 0 &&
            r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
    };
    const boundedText = (root, limit, scanLimit) => {
        const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let result = '', scanned = 0, node;
        while (result.length < limit && scanned++ < scanLimit && (node = walker.nextNode())) {
            const parent = node.parentElement;
            if (!parent || parent.closest('input,textarea,select,script,style,noscript,[contenteditable]')) continue;
            if (!parent.getClientRects().length || getComputedStyle(parent).visibility !== 'visible') continue;
            result += ' ' + node.textContent.slice(0, limit - result.length).replace(/\s+/g, ' ').trim();
        }
        return result.trim().slice(0, limit).toWellFormed();
    };
    const fullLabel = node => (node.getAttribute('aria-label') ||
        (node.labels?.length ? boundedText(node.labels[0], 257, 512) : '') ||
        (['BUTTON', 'A'].includes(node.tagName) ? boundedText(node, 257, 512) : '') ||
        node.getAttribute('placeholder') || node.getAttribute('name') || node.tagName.toLowerCase());
    const label = node => fullLabel(node).slice(0, 120).toWellFormed();
    const signature = node => JSON.stringify([
        node.tagName, node.type, node.name, node.id, fullLabel(node), node.href,
        node.disabled, node.readOnly, node.required, node.checked,
        node.getAttribute('autocomplete'), node.getAttribute('aria-disabled'),
        node.getAttribute('aria-readonly'), node.getAttribute('formaction'),
        node.form?.action, node.form?.method, node.form?.target,
        node.form?.enctype, node.value,
        node.tagName === 'SELECT' ? Array.from(node.options).slice(0, 61)
            .map(option => [option.value, option.text, option.disabled]) : null,
    ]);
    const supported = node => {
        if (!['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(node.tagName)) return false;
        if (sensitive(node) || node.closest('[contenteditable]')) return false;
        const attributes = ['name', 'id', 'href', 'autocomplete', 'aria-label', 'placeholder', 'formaction', 'aria-disabled', 'aria-readonly'];
        if (attributes.some(key => (node.getAttribute(key) || '').length > 2048) ||
            (node.href || '').length > 2048 || fullLabel(node).length > 256 ||
            (node.value || '').length > 4096 || (node.form?.action || '').length > 2048 ||
            (node.form?.target || '').length > 256 || (node.form?.enctype || '').length > 256) return false;
        if (node.tagName === 'INPUT') return textTypes.has(node.type) || ['button', 'submit', 'checkbox', 'radio'].includes(node.type);
        if (node.tagName === 'SELECT') {
            if (node.multiple || node.options.length > 60) return false;
            const values = new Set();
            for (const option of node.options) {
                if (!option.value.isWellFormed() || option.value.length > 80 || option.text.length > 80 || values.has(option.value)) return false;
                values.add(option.value);
            }
            return true;
        }
        if (node.tagName === 'A') return ['http:', 'https:'].includes(new URL(node.href, doc.baseURI).protocol);
        return ['TEXTAREA', 'BUTTON'].includes(node.tagName);
    };
    const elements = [];
    const walker = doc.createTreeWalker(doc.body || doc.documentElement, NodeFilter.SHOW_ELEMENT);
    let scanned = 0, node;
    while (scanned++ < 4000 && elements.length < 60 && (node = walker.nextNode())) {
        if (!supported(node) || !visible(node)) continue;
        const options = [];
        if (node.tagName === 'SELECT') {
            // Omit selects whose full choices would exceed the global budget.
            if (node.options.length > optionBudget) continue;
            optionBudget -= node.options.length;
            for (const option of node.options) options.push({value: option.value, label: option.text.toWellFormed(), disabled: option.matches(':disabled'), selected: option.selected});
        }
        const index = nodes.length;
        nodes.push(node); signatures.push(signature(node)); rectangles.push(rect(node));
        elements.push({ref: prefix + '.' + index, tag: node.tagName.toLowerCase(), label: label(node), options,
            checked: ['checkbox', 'radio'].includes(node.type) ? node.checked : null});
    }
    const observer = new MutationObserver(records => inspect(records));
    const inspect = records => {
        if (records.length > 64) { dirty = true; return; }
        for (const record of records) {
            if (dirty) return;
            if (record.removedNodes.length > 64) { dirty = true; return; }
            if (nodes.some(node => node === record.target || node.contains(record.target) ||
                Array.from(record.removedNodes).some(removed => removed === node || removed.contains?.(node)))) dirty = true;
        }
    };
    observer.observe(doc, {subtree: true, childList: true, attributes: true, characterData: true});
    const changed = () => { dirty = true; };
    addEventListener('resize', changed);
    addEventListener('scroll', changed, true);
    const dispose = () => {
        active = false;
        observer.disconnect();
        removeEventListener('resize', changed);
        removeEventListener('scroll', changed, true);
    };
    const view = {observation_ref: prefix, text: boundedText(doc.body || doc.documentElement, 12000, 6000),
        elements, truncated: elements.length >= 60 || scanned >= 4000};
    if (view.text.length >= 12000) view.truncated = true;
    // Include JSON escaping in the budget. Leave room for browser/page metadata
    // and the separately bounded URL in the outer response.
    while (JSON.stringify(view).length > 42000) {
        view.truncated = true;
        if (view.text.length) view.text = view.text.slice(0, Math.max(0, view.text.length - 4096)).toWellFormed();
        else { elements.pop(); nodes.pop(); signatures.pop(); rectangles.pop(); }
    }
    return {view, dispose, act: request => {
        inspect(observer.takeRecords());
        const index = request.index, target = index === null ? null : nodes[index];
        let reason = null;
        if (!active || dirty || doc !== document || viewport.some((n, i) => n !== [innerWidth, innerHeight, scrollX, scrollY][i])) reason = 'stale_reference';
        if (!reason && target && sensitive(target)) reason = 'sensitive_target';
        if (!reason && nodes.some((node, i) => !node.isConnected || node.ownerDocument !== doc ||
            sensitive(node) || !supported(node) || signature(node) !== signatures[i] || rect(node).some((n, j) => Math.abs(n - rectangles[i][j]) > 0.5))) reason = 'stale_reference';
        if (!reason && request.operation !== 'scroll' && !target) reason = 'stale_reference';
        if (!reason && target && (!supported(target) || !visible(target) || target.matches(':disabled') || target.readOnly ||
            target.getAttribute('aria-disabled') === 'true' || target.getAttribute('aria-readonly') === 'true')) reason = 'blocked_target';
        if (!reason && target) {
            const r = target.getBoundingClientRect();
            const hit = doc.elementFromPoint(Math.min(innerWidth - 1, Math.max(0, r.x + r.width / 2)), Math.min(innerHeight - 1, Math.max(0, r.y + r.height / 2)));
            if (!hit || !(hit === target || target.contains(hit))) reason = 'blocked_target';
        }
        // Consume the whole observation before any dispatch, including rejected
        // attempts with a valid token. No native wait, locator or keyboard path.
        dispose();
        if (reason) return reason;
        if (request.operation === 'scroll') { scrollBy({top: request.scroll_y, behavior: 'instant'}); return 'completed'; }
        if (request.operation === 'click') {
            const navigation = target.tagName === 'A' ||
                ((target.tagName === 'BUTTON' || target.tagName === 'INPUT') && target.type === 'submit' && target.form);
            let event;
            const remember = received => { event = received; };
            target.addEventListener('click', remember, {once: true});
            try { nativeClick.call(target); }
            finally { target.removeEventListener('click', remember); }
            return navigation && !event?.defaultPrevented ? 'navigating' : 'completed';
        }
        if (request.operation === 'fill' && ((target.tagName === 'INPUT' && textTypes.has(target.type)) || target.tagName === 'TEXTAREA')) {
            setters[target.tagName].call(target, request.value);
            target.dispatchEvent(new Event('input', {bubbles: true}));
            target.dispatchEvent(new Event('change', {bubbles: true}));
            return 'completed';
        }
        if (request.operation === 'select' && target.tagName === 'SELECT') {
            if (!Array.from(target.options).some(option => option.value === request.value && !option.matches(':disabled'))) return 'invalid_value';
            setters.SELECT.call(target, request.value);
            target.dispatchEvent(new Event('input', {bubbles: true}));
            target.dispatchEvent(new Event('change', {bubbles: true}));
            return 'completed';
        }
        return 'unsupported_target';
    }};
}
