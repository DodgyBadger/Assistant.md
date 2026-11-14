(isCredential) => {
    // Return only a verdict. Never serialize field values or identifiers.
    const roots = [document];
    let scanned = 0, labelScans = 0;
    const boundedText = (root, limit, scanLimit) => {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let result = '', node, count = 0;
        while (result.length < limit && count++ < scanLimit && (node = walker.nextNode())) {
            if (++labelScans > 8000) throw new Error('Credential inspection limit');
            result += node.textContent.slice(0, limit - result.length);
        }
        return result;
    };
    for (let index = 0; index < roots.length; index++) {
        const walker = document.createTreeWalker(roots[index], NodeFilter.SHOW_ELEMENT);
        let node;
        while ((node = walker.nextNode())) {
            if (++scanned > 4000) return 'unavailable';
            if (node.shadowRoot) {
                if (roots.length >= 16) return 'unavailable';
                roots.push(node.shadowRoot);
            }
            if (['INPUT', 'TEXTAREA', 'SELECT'].includes(node.tagName) &&
                isCredential(node, boundedText) && node.value) return 'credentials_pending';
        }
    }
    return 'clear';
}
