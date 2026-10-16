const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
global.window = global;
global.document = {};
vm.runInThisContext(fs.readFileSync('static/js/vault-selection-prompt.js', 'utf8'));
vm.runInThisContext(fs.readFileSync('static/js/chat-composer.js', 'utf8'));

test('quote blocks retain multiline text and keep prompt outside the quote', () => {
    const block = VaultSelectionPrompt.formatBlock('Notes/A.md', 'first\n\nlast', 'Explain\nthis');
    assert.equal(block, '[[Notes/A.md]]\n\n> first\n>\n> last\n\nExplain\nthis');
    assert.equal(VaultSelectionPrompt.formatBlock('A.md', 'quote', ''), '[[A.md]]\n\n> quote');
});

test('append accumulates blocks without replacing selected draft or focusing it', () => {
    let events = 0;
    const chatInput = { value: 'Keep my draft', selectionStart: 0, selectionEnd: 4,
        dispatchEvent() { events++; }, focus() { throw new Error('Stole focus'); } };
    const attachments = [{ file: { name: 'image.png' } }];
    const controller = ChatComposer.create({ state: {}, composeState: { pendingAttachments: attachments },
        elements: { chatInput }, utils: {}, browserStorage: {} });
    controller.appendPromptBlock('first');
    controller.appendPromptBlock('second');
    assert.equal(chatInput.value, 'Keep my draft\n\nfirst\n\nsecond');
    assert.equal(events, 2);
    assert.equal(attachments.length, 1);
});

test('file references preserve literal percent characters and encode link delimiters', () => {
    assert.equal(VaultSelectionPrompt.formatBlock('Notes/A%20B.md', 'text', ''),
        '[[Notes/A%20B.md]]\n\n> text');
    assert.equal(VaultSelectionPrompt.formatBlock('Notes/A]#|.md', 'text', ''),
        '[Notes/A\\]#|.md](</Notes/A%5D%23%7C.md>)\n\n> text');
});
