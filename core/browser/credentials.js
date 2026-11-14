(node, boundedText) => {
    const pieces = ['type', 'autocomplete', 'name', 'id', 'aria-label', 'placeholder']
        .map(key => node.getAttribute(key) || '');
    if (pieces.some(value => value.length > 256)) return true;
    for (let i = 0; i < Math.min(node.labels?.length || 0, 3); i++) {
        pieces.push(boundedText(node.labels[i], 256, 256));
    }
    return /password|passwd|passcode|one.time.code|one.time.password|\botp\b|verification.?code|auth.?code/i.test(pieces.join(' '));
}
