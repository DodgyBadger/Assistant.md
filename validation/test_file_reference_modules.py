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
global.document = { getElementById() { return null; } };
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


def test_file_modal_back_history_and_tree_navigation() -> None:
    harness = r"""
const assert = require('assert'), fs = require('fs'), vm = require('vm');
global.window = global;
let overlay = null, picker = null, discard = true;
class Element {
    constructor() {
        this.value = ''; this.scrollTop = 0; this.dataset = {}; this.events = {}; this.nodes = new Map();
        const classes = new Set();
        this.classList = { add:name=>classes.add(name), remove:name=>classes.delete(name),
            toggle(name, enabled){if(enabled)classes.add(name);else classes.delete(name);}, contains:name=>classes.has(name) };
    }
    querySelector(selector) {
        if (selector === '[data-vault-file-back]' && !this.innerHTML?.includes('data-vault-file-back')) return null;
        if (selector === '[data-vault-file-tree]' && !this.innerHTML?.includes('data-vault-file-tree')) return null;
        if (!this.nodes.has(selector)) this.nodes.set(selector, selector.includes('editor') ? new Textarea() : selector.includes('button') || selector.includes('save') ? new Button() : new Element());
        return this.nodes.get(selector);
    }
    querySelectorAll() { return []; }
    addEventListener(name, callback) { this.events[name] = callback; }
    remove() { if (overlay === this) overlay = null; }
    setAttribute() {}
}
class Textarea extends Element {}
class Button extends Element {}
global.Element = global.HTMLElement = Element;
global.HTMLTextAreaElement = Textarea; global.HTMLButtonElement = Button;
global.document = { createElement:()=>new Element(), getElementById:()=>overlay, body:{appendChild(node){overlay=node;}} };
global.confirm = () => discard;
global.requestAnimationFrame = callback => callback();
global.fetch = async url => ({ok:true,json:async()=>({content:`Body ${url}`,sha256:'hash'})});
for (const file of process.argv.slice(1)) vm.runInThisContext(fs.readFileSync(file,'utf8'));
const vaultSelector = {value:'OriginalVault'};
const controller = FileReferences.create({state:{}, elements:{vaultSelector}, icons:{},
    utils:{escapeHtml:value=>String(value),formatShortDate:value=>value},
    callbacks:{openPathPicker(options){picker=options;},renderMarkdownPreview(){}}});
const editor = () => overlay.querySelector('#vault-file-modal-editor');
const preview = () => overlay.querySelector('#vault-file-modal-preview');
async function click(selector, dataset = {}) {
    const current = overlay;
    const button = Object.assign(new Button(), {dataset});
    await current.events.click({target:Object.assign(new Element(),{closest:value=>value===selector ? button : null})});
}
async function run() {
    await controller.openFile('A.md');
    assert(!overlay.querySelector('[data-vault-file-back]'), 'Direct chat open has no previous view.');
    assert(overlay.querySelector('[data-vault-file-tree]'), 'A separate tree action is always available.');
    preview().scrollTop=240;
    await controller.openFile('B.md', {vaultName:'OriginalVault'});
    preview().scrollTop=90;
    await controller.openFile('C.md', {vaultName:'OriginalVault'});
    vaultSelector.value='ChangedVault';
    await click('[data-vault-file-back="true"]');
    assert(editor().value.includes('path=B.md'), 'Back restores the previous file, not the tree.');
    assert.strictEqual(preview().scrollTop,90);
    editor().value='Unsaved'; discard=false;
    await click('[data-vault-file-back="true"]');
    assert.strictEqual(editor().value,'Unsaved', 'Declined departure must preserve view and history.');
    await click('[data-vault-file-tree="true"]');
    assert.strictEqual(picker,null, 'Tree navigation also respects unsaved changes.');
    discard=true;
    await click('[data-vault-file-back="true"]');
    assert(editor().value.includes('OriginalVault') && editor().value.includes('path=A.md'));
    assert.strictEqual(preview().scrollTop,240);
    assert(!overlay.querySelector('[data-vault-file-back]'));
    await click('[data-vault-file-tree="true"]');
    assert.strictEqual(picker.vaultName,'OriginalVault');
    assert.strictEqual(picker.revealInitialPath,'A.md');
    await picker.onOpenFile('D.md');
    assert(overlay.querySelector('[data-vault-file-back]'), 'Opening from a tree records that origin.');
    await controller.openFile('E.md',{vaultName:'OriginalVault'});
    await click('[data-vault-file-back="true"]');
    assert(editor().value.includes('path=D.md'));
    await click('[data-vault-file-back="true"]');
    assert.strictEqual(overlay,null, 'Back from first tree-opened file reveals the original tree.');
    await controller.openFile('Direct.md',{vaultName:'OriginalVault'});
    await controller.openFile('Direct.md',{vaultName:'OriginalVault'});
    assert(!overlay.querySelector('[data-vault-file-back]'), 'Refreshing a file must not create a duplicate history entry.');
    await click('[data-vault-file-mode]', {vaultFileMode:'edit'});
    editor().scrollTop=55;
    await controller.openFile('Next.md',{vaultName:'OriginalVault'});
    await click('[data-vault-file-back="true"]');
    assert(!editor().classList.contains('hidden'), 'Back restores edit mode as well as preview mode.');
    assert.strictEqual(editor().scrollTop,55);
    let release;
    global.fetch = () => new Promise(resolve=>{release=resolve;});
    const pending=controller.openFile('Late.md',{vaultName:'OriginalVault'});
    controller.closeFileModal();
    release({ok:true,json:async()=>({content:'Late',sha256:'late'})});
    await pending;
    assert.strictEqual(overlay,null, 'A late load cannot reopen a closed view.');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
"""
    subprocess.run(
        ["node", "-e", harness, str(_LINKS_MODULE), str(_REFERENCES_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


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
    assert.strictEqual(opened[0].onBack, undefined, 'File navigation history belongs to the modal, not the link resolver.');
    await controller.enhanceFileLinks(root, { vaultName: 'NotesVault', sourcePath: 'Projects/Plan/index.md' });
    assert.strictEqual(requests.length, 1, 'Repeated enhancement must not relink resolved paths.');

    const chat = container([new Anchor('Full path', { href: 'Library/root.md' }), new Anchor('Folder', { href: 'Library/' }), new Anchor('Missing', { href: 'missing.md' }), new Anchor('Legacy target', { href: '@Library/other.md' })]);
    await controller.enhanceFileLinks(chat);
    assert.deepStrictEqual(requests[1].paths, ['Library/root.md', 'Library', 'missing.md', 'Library/other.md']);
    assert.strictEqual(requests[1].workspace_path, '', 'Explicit chat links are vault-root-relative.');
    assert(!(chat.children[2] instanceof Anchor));
    chat.children[1].events.click({ preventDefault() {} });
    assert.strictEqual(directories[0].revealPath, 'Library');
    assert.strictEqual(directories[0].vaultName, 'ChatVault');

    const legacy = new Element('@README.md');
    legacy.dataset.vaultReferenceCandidate = 'README.md';
    await controller.enhanceFileLinks(container([legacy]));
    assert.strictEqual(requests[2].workspace_path, 'Workspace', 'Legacy basename fallback remains compatible.');

    const regional = 'Library/! Primary Sources/Gates Creek/Sources/Background/Regional significance';
    const lookup = new Map([
        [regional + '.md', 'file'], ['Library/Exact note', 'file'], ['Library/Exact note.md', 'file'],
        ['Library/Directory', 'directory'], ['Library/Directory.md', 'file'],
        ['Library/Été (2026), 100%20 & notes.md', 'file'],
    ]);
    const wikiRequests = [];
    global.fetch = async (url, options) => {
        const body = JSON.parse(options.body);
        assert(body.paths.length <= 100, 'Resolution requests must stay within the API limit.');
        wikiRequests.push({ url, ...body });
        return { ok: true, json: async () => ({ items: body.paths.map(path => ({
            requested_path: path, path, kind: lookup.get(path) || 'missing',
        })) }) };
    };
    function wikilink(path, label = path) {
        const node = new Element(label);
        node.dataset.vaultWikilink = path;
        return node;
    }
    const original = container([
        wikilink(regional, 'Regional significance'), wikilink('Library/Exact note', 'Exact'),
        wikilink('Library/Directory', 'Folder'), wikilink('Library/Été (2026), 100%20 & notes', 'Literal'),
        wikilink('../outside', 'Unsafe'), wikilink('javascript:alert(1)', 'Scheme'),
        wikilink('Library/missing', 'Missing'), wikilink('Library/explicit.txt', 'Text'),
    ]);
    await controller.enhanceFileLinks(original, { vaultName: 'NotesVault', sourcePath: 'Different/index.md' });
    assert(original.children[0] instanceof Anchor, 'An extensionless aliased wikilink must resolve a Markdown note.');
    assert.strictEqual(original.children[0].textContent, 'Regional significance');
    assert.strictEqual(original.children[0].dataset.vaultFilePath, regional + '.md');
    assert.strictEqual(original.children[1].dataset.vaultFilePath, 'Library/Exact note', 'Exact file wins over Markdown fallback.');
    assert.strictEqual(original.children[2].dataset.vaultFileKind, 'directory', 'Exact directory wins over Markdown fallback.');
    assert.strictEqual(original.children[3].dataset.vaultFilePath, 'Library/Été (2026), 100%20 & notes.md');
    for (const index of [4, 5, 6, 7]) assert(!(original.children[index] instanceof Anchor));
    assert(wikiRequests.every(request => request.workspace_path === '' && request.url.includes('NotesVault')));
    assert(!wikiRequests.flatMap(request => request.paths).includes('Library/explicit.txt.md'), 'Explicit extensions are never rewritten.');
    original.children[0].events.click({ preventDefault() {} });
    assert.strictEqual(opened.at(-1).path, regional + '.md');
    assert.strictEqual(opened.at(-1).vaultName, 'NotesVault');

    const plain = container([new Anchor('Ordinary Markdown', { href: regional })]);
    await controller.enhanceFileLinks(plain, { vaultName: 'NotesVault' });
    assert(!(plain.children[0] instanceof Anchor), 'Wikilink fallback must not change ordinary Markdown lookup through cache reuse.');

    const large = container(Array.from({ length: 105 }, (_, index) => {
        const path = `Library/Batch ${index}`;
        lookup.set(path + '.md', 'file');
        return wikilink(path);
    }));
    await controller.enhanceFileLinks(large, { vaultName: 'NotesVault' });
    assert(large.children.every(node => node instanceof Anchor));
    const count = wikiRequests.length;
    await controller.enhanceFileLinks(large, { vaultName: 'NotesVault' });
    assert.strictEqual(wikiRequests.length, count, 'Resolved links must not be enhanced twice.');

    lookup.set('Library/missing.md', 'file');
    await controller.enhanceFileLinks(original, { vaultName: 'NotesVault' });
    assert(original.children[6] instanceof Anchor, 'Missing targets must be retried rather than negatively cached.');

    let release;
    global.fetch = () => new Promise(resolve => { release = resolve; });
    const detached = container([wikilink('Library/Late')]);
    const pending = controller.enhanceFileLinks(detached, { vaultName: 'NotesVault' });
    detached.children = [];
    release({ ok: true, json: async () => ({ items: [{ requested_path: 'Library/Late.md', path: 'Library/Late.md', kind: 'file' }] }) });
    await pending;
    assert.strictEqual(detached.children.length, 0, 'Late responses must not restore replaced preview content.');

    const retry = container([wikilink('Library/Retry', 'Retry')]);
    const errors = [];
    const originalError = console.error;
    console.error = (...args) => errors.push(args);
    try {
        global.fetch = async () => ({ ok: false, status: 503, json: async () => ({}) });
        await controller.enhanceFileLinks(retry, { vaultName: 'NotesVault' });
        assert(!(retry.children[0] instanceof Anchor), 'Failed resolution must leave no navigable application URL.');
        assert.strictEqual(errors.length, 1, 'Resolution failure must remain diagnosable.');
        global.fetch = async () => ({ ok: true, json: async () => ({ items: [
            { requested_path: 'Library/Retry', path: 'Library/Retry', kind: 'missing' },
            { requested_path: 'Library/Retry.md', path: 'Library/Retry.md', kind: 'file' },
        ] }) });
        await controller.enhanceFileLinks(retry, { vaultName: 'NotesVault' });
        assert(retry.children[0] instanceof Anchor, 'Failed resolution must be retryable.');
    } finally {
        console.error = originalError;
    }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(
        ["node", "-e", harness, str(_LINKS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
