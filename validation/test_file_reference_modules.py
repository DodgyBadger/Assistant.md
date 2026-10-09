"""Frontend smoke tests for the file reference module graph."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LINKS_MODULE = _PROJECT_ROOT / "static/js/file-reference-links.js"
_REFERENCES_MODULE = _PROJECT_ROOT / "static/js/file-references.js"


def test_file_references_composes_link_resolver() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
global.HTMLTextAreaElement = class {
    constructor() { this.value = 'See '; this.selectionStart = 4; this.selectionEnd = 4; }
    focus() {}
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
    dispatchEvent() {}
};
const chatInput = new HTMLTextAreaElement();
let importRequest = null;
global.fetch = async (url, options) => {
    importRequest = { url, options };
    return { ok: true, json: async () => ({ job_id: 17 }) };
};
for (const path of process.argv.slice(1)) {
    vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
}

let pickerOptions = null;
const controller = FileReferences.create({
    state: { workspaceExists: true },
    elements: {
        vaultSelector: { value: 'ChatVault' },
        workspacePathInput: { value: 'Projects' },
        chatInput,
    },
    icons: {},
    utils: {},
    callbacks: {
        openPathPicker(options) { pickerOptions = options; },
        setWorkspace() { return true; },
    },
});

controller.insertReference('Library/! Primary Sources/index.md');
assert.strictEqual(chatInput.value, 'See [[Library/! Primary Sources/index.md]]');
assert.strictEqual(chatInput.selectionStart, chatInput.value.length);

for (const name of [
    'openPicker',
    'openExplorer',
    'openFile',
    'enhanceFileLinks',
    'syncInteractionLocks',
]) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing file reference method: ${name}`);
    }
}

controller.openExplorer({ vaultName: 'ArchiveVault' });
assert.strictEqual(pickerOptions.workspacePath, '');
assert.strictEqual(pickerOptions.onAddReference, undefined);
assert.strictEqual(pickerOptions.onSetWorkspace, undefined);

controller.openExplorer({
    vaultName: 'ArchiveVault',
    importSources: ['Incoming/report.pdf'],
});
assert.deepStrictEqual(pickerOptions.importSources, ['Incoming/report.pdf']);
assert.strictEqual(typeof pickerOptions.onImportSources, 'function');
pickerOptions.onImportSources({ sources: ['Incoming/report.pdf'] }).then((result) => {
    assert.deepStrictEqual(result, { job_id: 17 });
    assert.strictEqual(importRequest.url, 'api/import/sources');
    assert.deepStrictEqual(JSON.parse(importRequest.options.body), {
        sources: ['Incoming/report.pdf'],
        vault: 'ArchiveVault',
    });
}).catch((error) => {
    console.error(error);
    process.exitCode = 1;
});

controller.openExplorer();
assert.strictEqual(pickerOptions.workspacePath, 'Projects');
assert.strictEqual(typeof pickerOptions.onAddReference, 'function');
assert.strictEqual(typeof pickerOptions.onSetWorkspace, 'function');
pickerOptions.onSetWorkspace('New Workspace').then((saved) => {
    assert.strictEqual(saved, true);
}).catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
"""
    subprocess.run(
        ["node", "-e", harness, str(_LINKS_MODULE), str(_REFERENCES_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_file_reference_links_load_before_controller() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    links_position = markup.index(
        '<script src="static/js/file-reference-links.js"></script>'
    )
    references_position = markup.index(
        '<script src="static/js/file-references.js"></script>'
    )

    assert links_position < references_position


def test_adjacent_at_path_references_do_not_merge_across_prose() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
const source = fs.readFileSync(process.argv[1], 'utf8').replace(
    'return Object.freeze({ enhanceFileLinks });',
    'global.__candidateMatches = candidateMatches; return Object.freeze({ enhanceFileLinks });'
);
vm.runInThisContext(source, { filename: process.argv[1] });
FileReferenceLinks.create({
    callbacks: {
        selectedVault() { return 'PHHC'; },
        workspacePath() { return ''; },
        openDirectory() {},
        openFile() {},
    },
});

const text = 'The working package and full preview are at @PHHC/Cana reports/2026-10/ and @PHHC/Cana reports/2026-10/Board summary and publication preview.md.';
assert.deepStrictEqual(
    global.__candidateMatches(text).map(({ raw, candidate }) => ({ raw, candidate })),
    [
        {
            raw: '@PHHC/Cana reports/2026-10/',
            candidate: 'PHHC/Cana reports/2026-10',
        },
        {
            raw: '@PHHC/Cana reports/2026-10/Board summary and publication preview.md',
            candidate: 'PHHC/Cana reports/2026-10/Board summary and publication preview.md',
        },
    ]
);

// Punctuation inside a real vault path is not sentence punctuation. Keep the
// complete candidate so existence resolution can distinguish files from prose.
const punctuationText = 'Done. Start your proposal research at @Library/! Primary Sources/Lehmann Springs/index.md.';
assert.deepStrictEqual(
    global.__candidateMatches(punctuationText).map(({ raw, candidate }) => ({ raw, candidate })),
    [{
        raw: '@Library/! Primary Sources/Lehmann Springs/index.md',
        candidate: 'Library/! Primary Sources/Lehmann Springs/index.md',
    }]
);
"""
    subprocess.run(
        ["node", "-e", harness, str(_LINKS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_explicit_links_resolve_in_source_context_and_preserve_labels() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
global.window = global;
global.NodeFilter = { SHOW_TEXT: 4 };
class Element {
    constructor(text = '', attrs = {}) { this.textContent = text; this.attrs = attrs; this.dataset = {}; this.events = {}; }
    getAttribute(name) { return this.attrs[name]; }
    closest() { return null; }
    setAttribute(name, value) { this.attrs[name] = value; }
    removeAttribute(name) { delete this.attrs[name]; }
    addEventListener(name, callback) { this.events[name] = callback; }
    replaceWith(next) {
        const index = this.container.children.indexOf(this);
        this.container.children[index] = next;
        next.container = this.container;
    }
}
class Anchor extends Element { constructor(...args) { super(...args); this.tagName = 'A'; } }
global.HTMLElement = Element;
global.HTMLAnchorElement = Anchor;
global.document = {
    createTreeWalker() { return { nextNode() { return false; } }; },
    createElement() { return new Anchor(); },
    createTextNode(text) { return new Element(text); },
};
function container(children) {
    const root = {
        children,
        contains(element) { return this.children.includes(element); },
        querySelectorAll(selector) {
            return this.children.filter((element) => {
                if (selector === '[data-vault-wikilink]') return 'vaultWikilink' in element.dataset;
                if (selector === '[data-vault-reference-candidate]') return 'vaultReferenceCandidate' in element.dataset;
                if (selector === 'a[href]') return element instanceof Anchor && element.getAttribute('href');
                return false;
            });
        },
    };
    children.forEach(element => element.container = root);
    return root;
}
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const requests = [], opened = [], directories = [];
global.fetch = async (url, options) => {
    const body = JSON.parse(options.body);
    requests.push({ url, ...body });
    return { ok: true, json: async () => ({ items: body.paths.map(path => ({
        requested_path: path, path, kind: path === 'missing.md' ? 'missing' : path === 'Library' ? 'directory' : 'file',
    })) }) };
};
const controller = FileReferenceLinks.create({ callbacks: {
    selectedVault() { return 'ChatVault'; }, workspacePath() { return 'Workspace'; },
    openFile(path, options) { opened.push({ path, ...options }); },
    openExplorer(options) { directories.push(options); },
} });
async function run() {
    const wiki = new Element('Library/! Primary Sources/a_b.md');
    wiki.dataset.vaultWikilink = wiki.textContent;
    const literalPercent = new Element('Library/100%20 literal.md');
    literalPercent.dataset.vaultWikilink = literalPercent.textContent;
    const relative = new Anchor('Sibling', { href: '../Sources/a%20b.md' });
    const absolute = new Anchor('Root', { href: '/Library/root.md' });
    const external = new Anchor('Web', { href: 'https://example.com/x.md' });
    const email = new Anchor('Email', { href: 'mailto:test@example.com' });
    const fragment = new Anchor('Heading', { href: '#heading' });
    const invalid = new Anchor('Unsafe', { href: '../../../outside.md' });
    const root = container([wiki, literalPercent, relative, absolute, external, email, fragment, invalid]);
    await controller.enhanceFileLinks(root, { vaultName: 'NotesVault', sourcePath: 'Projects/Plan/index.md' });
    assert.deepStrictEqual(requests[0], {
        url: 'api/vaults/NotesVault/file-refs/resolve', workspace_path: '',
        paths: ['Library/! Primary Sources/a_b.md', 'Library/100%20 literal.md', 'Projects/Sources/a b.md', 'Library/root.md'],
    });
    assert.deepStrictEqual(root.children.map(node => node.textContent), [
        wiki.textContent, literalPercent.textContent, 'Sibling', 'Root', 'Web', 'Email', 'Heading', 'Unsafe',
    ]);
    assert.strictEqual(root.children[4], external);
    assert.strictEqual(root.children[6], fragment);
    assert(!(root.children[7] instanceof Anchor));
    root.children[0].events.click({ preventDefault() {} });
    assert.strictEqual(opened[0].vaultName, 'NotesVault');
    opened[0].onBack();
    assert.strictEqual(directories[0].vaultName, 'NotesVault');
    await controller.enhanceFileLinks(root, { vaultName: 'NotesVault', sourcePath: 'Projects/Plan/index.md' });
    assert.strictEqual(requests.length, 1, 'Repeated enhancement must not relink resolved paths.');

    const chat = container([new Anchor('Full path', { href: 'Library/root.md' }), new Anchor('Folder', { href: 'Library/' }), new Anchor('Missing', { href: 'missing.md' }), new Anchor('Legacy target', { href: '@Library/other.md' })]);
    await controller.enhanceFileLinks(chat);
    assert.deepStrictEqual(requests[1].paths, ['Library/root.md', 'Library', 'missing.md', 'Library/other.md']);
    assert.strictEqual(requests[1].workspace_path, '', 'Explicit chat links are vault-root-relative.');
    assert(!(chat.children[2] instanceof Anchor));
    chat.children[1].events.click({ preventDefault() {} });
    assert.strictEqual(directories[1].revealPath, 'Library');

    const legacy = new Element('@README.md');
    legacy.dataset.vaultReferenceCandidate = 'README.md';
    await controller.enhanceFileLinks(container([legacy]));
    assert.strictEqual(requests[2].workspace_path, 'Workspace', 'Legacy basename fallback remains compatible.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(
        ["node", "-e", harness, str(_LINKS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
