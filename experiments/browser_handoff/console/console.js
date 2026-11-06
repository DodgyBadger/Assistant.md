'use strict';

const element = id => document.getElementById(id);
const baseUrl = new URL('.', document.baseURI);
try { history.replaceState(null, '', location.pathname + location.search); }
catch { /* Sandboxed frames cannot modify history. */ }
let state = null;
let queue = Promise.resolve();
let frameUrl = null;
let polling = false;

function message(text) { element('message').textContent = text; }
function render(next) {
  if (state && next.revision < state.revision) return;
  state = next;
  element('owner').textContent = next.owner;
  element('take').disabled = next.owner !== 'agent';
  element('give').disabled = next.owner !== 'human';
  element('go').disabled = next.owner !== 'human';
  element('close').disabled = next.owner === 'closed';
  element('run').disabled = next.owner !== 'agent';
  if (document.activeElement !== element('url')) element('url').value = next.url === 'about:blank' ? '' : next.url;
}

async function api(path, body) {
  const response = await fetch(new URL(path.replace(/^\/+/, ''), baseUrl), {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  });
  if (!response.ok) {
    const error = await response.json();
    if (error.detail === 'credential_field_still_populated') throw new Error('Submit or clear the password field before giving back control.');
    throw new Error(error.detail || 'Browser request failed');
  }
  return response;
}

function enqueue(action) {
  queue = queue.then(action).catch(error => message(error.message));
  return queue;
}

async function control(owner) {
  render(await (await api('/human/control', {owner, revision: state.revision})).json());
  message('');
}

function input(body) {
  if (!state || state.owner !== 'human') return;
  enqueue(async () => render(await (await api('/human/input', {...body, revision: state.revision})).json()));
}

async function refresh() {
  if (polling || document.hidden) return;
  polling = true;
  try {
    render(await (await api('/human/state')).json());
    if (state.owner !== 'closed') {
      const blob = await (await api('/human/frame')).blob();
      const previous = frameUrl;
      frameUrl = URL.createObjectURL(blob);
      element('screen').src = frameUrl;
      if (previous) URL.revokeObjectURL(previous);
    } else {
      element('screen').removeAttribute('src');
      if (frameUrl) URL.revokeObjectURL(frameUrl);
      frameUrl = null;
    }
  } catch (error) { message(error.message); }
  finally { polling = false; }
}

element('take').addEventListener('click', () => enqueue(() => control('human')));
element('give').addEventListener('click', () => enqueue(() => control('agent')));
element('close').addEventListener('click', () => enqueue(async () => render(await (await api('/human/close', {})).json())));
element('navigation').addEventListener('submit', event => {
  event.preventDefault();
  const url = element('url').value;
  enqueue(async () => render(await (await api('/human/navigate', {url, revision: state.revision})).json()));
});
element('demo').addEventListener('click', () => enqueue(async () => {
  if (state.owner === 'agent') await control('human');
  const demo = await (await api('/human/demo')).json();
  element('demo-password').textContent = demo.password;
  render(await (await api('/human/navigate', {url: demo.url, revision: state.revision})).json());
}));
element('run').addEventListener('click', () => enqueue(async () => {
  const result = await (await api('/human/demo-run', {})).json();
  element('result').textContent = result.text;
  await refresh();
}));
element('screen').addEventListener('click', event => {
  element('screen').focus();
  const rect = element('screen').getBoundingClientRect();
  input({kind: 'click', x: Math.min(1279, Math.max(0, (event.clientX - rect.left) * 1280 / rect.width)), y: Math.min(799, Math.max(0, (event.clientY - rect.top) * 800 / rect.height))});
});
element('screen').addEventListener('keydown', event => {
  if (!state || state.owner !== 'human') return;
  let key = event.key;
  if ((event.ctrlKey || event.metaKey) && key.toLowerCase() === 'a') key = 'ControlOrMeta+A';
  else if (key === 'Tab' && event.shiftKey) key = 'Shift+Tab';
  else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault(); input({kind: 'text', text: key}); return;
  }
  if (['Enter', 'Tab', 'Shift+Tab', 'Escape', 'Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown', 'ControlOrMeta+A'].includes(key)) {
    event.preventDefault(); input({kind: 'key', key});
  }
});
element('screen').addEventListener('paste', event => {
  event.preventDefault(); input({kind: 'text', text: event.clipboardData.getData('text/plain').slice(0, 4096)});
});
element('screen').addEventListener('wheel', event => {
  if (!state || state.owner !== 'human') return;
  event.preventDefault(); input({kind: 'scroll', delta_x: Math.max(-2000, Math.min(2000, event.deltaX)), delta_y: Math.max(-2000, Math.min(2000, event.deltaY))});
}, {passive: false});
window.addEventListener('pagehide', () => { if (frameUrl) URL.revokeObjectURL(frameUrl); });
refresh();
setInterval(refresh, 750);
