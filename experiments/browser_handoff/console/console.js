'use strict';

const element = id => document.getElementById(id);
const baseUrl = new URL(document.currentScript?.dataset.browserBase || '.', document.baseURI);
const integrated = baseUrl.pathname.startsWith('/browser/');
let autoTakeover = integrated && new URLSearchParams(location.search).has('takeover');
if (integrated) {
  document.querySelector('header h1').textContent = 'Browser';
  document.querySelector('header p').textContent = 'Complete the requested step, then return control to continue your chat.';
  document.querySelector('details').hidden = true;
  element('reset').hidden = true;
  element('give').textContent = 'Return control to agent';
}
try { history.replaceState(null, '', location.pathname + location.search); }
catch { /* Sandboxed frames cannot modify history. */ }
let state = null;
let queue = Promise.resolve();
let frameUrl = null;
let polling = false;
let previewing = false;
let busy = false;
let draftUrl = false;
let inputEpoch = 0;
let connectionError = false;
const retiredSessions = new Set();

function message(text) { element('message').textContent = text; }
function clearFrame() {
  element('screen').removeAttribute('src');
  if (frameUrl) URL.revokeObjectURL(frameUrl);
  frameUrl = null;
}
function render(next) {
  if (state && state.session_id !== next.session_id) {
    if (retiredSessions.has(next.session_id)) return;
    retiredSessions.add(state.session_id);
    inputEpoch++;
    draftUrl = false;
    clearFrame();
  } else if (state && next.revision < state.revision) return;
  if (state && state.owner !== next.owner) inputEpoch++;
  state = next;
  element('owner').textContent = next.owner;
  element('take').disabled = busy || next.owner !== 'agent';
  element('give').disabled = busy || next.owner !== 'human';
  element('go').disabled = busy || next.owner !== 'human';
  element('close').disabled = busy || next.owner === 'closed';
  element('run').disabled = busy || next.owner !== 'agent';
  element('demo').disabled = busy || next.owner === 'closed';
  element('reset').disabled = busy;
  if (!draftUrl && document.activeElement !== element('url')) element('url').value = next.url === 'about:blank' ? '' : next.url;
  if (next.owner === 'closed') clearFrame();
}

async function api(path, body, timeout = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(new URL(path.replace(/^\/+/, ''), baseUrl), {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? {} : {
        'Content-Type': 'application/json',
        'x-assistantmd-csrf': decodeURIComponent(document.cookie.split('; ').find(item => item.startsWith('assistantmd_csrf='))?.split('=').slice(1).join('=') || ''),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store', signal: controller.signal,
    });
    if (!response.ok) {
      const error = await response.json();
      if (error.detail === 'credential_field_still_populated') throw new Error('Submit or clear the password field before giving back control.');
      if (error.detail === 'browser_operation_timed_out') throw new Error('The page stopped responding. Try Reset browser.');
      throw new Error(error.detail || 'Browser request failed');
    }
    return response;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The request timed out. Try Reset browser.');
    throw error;
  } finally { clearTimeout(timer); }
}

async function action(label, perform) {
  if (busy) return;
  busy = true;
  if (state) render(state);
  message(label);
  try { await queue; await perform(); message(''); }
  catch (error) { message(error.message); }
  finally { busy = false; if (state) render(state); }
}

async function control(owner) {
  render(await (await api('/human/control', {owner, revision: state.revision})).json());
  if (integrated && owner === 'agent' && window.parent !== window) {
    window.parent.postMessage({type: 'assistantmd-browser-returned'}, location.origin);
  }
}

function input(body) {
  if (!state || state.owner !== 'human') return;
  const epoch = inputEpoch, session = state.session_id;
  queue = queue.then(async () => {
    if (epoch !== inputEpoch || session !== state.session_id || state.owner !== 'human') return;
    render(await (await api('/human/input', {...body, revision: state.revision})).json());
  }).catch(error => message(error.message));
}

async function refresh() {
  if (polling || document.hidden) return;
  polling = true;
  try {
    render(await (await api('/human/state', undefined, 4000)).json());
    if (autoTakeover) {
      autoTakeover = false;
      if (state.owner === 'agent') await action('Taking control…', () => control('human'));
    }
    if (connectionError) message('');
    connectionError = false;
  }
  catch (error) { connectionError = true; message(error.message); }
  finally { polling = false; }
}

async function refreshFrame() {
  if (previewing || document.hidden || !state || state.owner === 'closed') return;
  previewing = true;
  const session = state.session_id;
  try {
    const blob = await (await api('/human/frame', undefined, 5000)).blob();
    if (state.session_id !== session || state.owner === 'closed') return;
    const previous = frameUrl;
    frameUrl = URL.createObjectURL(blob);
    element('screen').src = frameUrl;
    if (previous) URL.revokeObjectURL(previous);
    element('preview-status').textContent = '';
  } catch {
    if (state.session_id === session && state.owner !== 'closed') element('preview-status').textContent = 'Preview delayed. Browser controls are still available.';
  } finally { previewing = false; }
}

element('take').addEventListener('click', () => action('Taking control…', () => control('human')));
element('give').addEventListener('click', () => action('Giving back control…', () => control('agent')));
element('close').addEventListener('click', () => action('Closing browser…', async () => render(await (await api('/human/close', {})).json())));
element('reset').addEventListener('click', () => action('Resetting browser…', async () => render(await (await api('/human/reset', {})).json())));
element('url').addEventListener('input', () => { draftUrl = true; });
element('navigation').addEventListener('submit', event => {
  event.preventDefault();
  const url = element('url').value;
  action('Opening page…', async () => {
    const next = await (await api('/human/navigate', {url, revision: state.revision})).json();
    draftUrl = false;
    render(next);
  });
});
element('demo').addEventListener('click', () => action('Opening demo…', async () => {
  if (state.owner === 'agent') await control('human');
  const demo = await (await api('/human/demo')).json();
  element('demo-password').textContent = demo.password;
  draftUrl = false;
  render(await (await api('/human/navigate', {url: demo.url, revision: state.revision})).json());
}));
element('run').addEventListener('click', () => action('Running form automation…', async () => {
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
setInterval(refresh, 500);
setInterval(refreshFrame, 750);
