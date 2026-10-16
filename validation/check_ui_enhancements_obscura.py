"""Run focused UI behavior checks through an existing Obscura CDP server."""

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

_ROOT = Path(__file__).resolve().parents[1]

_APPROVAL_CHECK = r"""(async () => {
    document.body.innerHTML = '<select id="vault"><option value="Vault">Vault</option></select><select id="mode"><option value="normal">Off</option><option value="inline_edit">On</option></select><button id="toggle"></button>';
    const elements = { vaultSelector: document.querySelector('#vault'), chatModeSelector: document.querySelector('#mode'), fileApprovalToggle: document.querySelector('#toggle') };
    const state = { metadata: { settings: { default_chat_mode: 'inline_edit' } }, sessionId: null, sessions: [] };
    let requests = [], fail = false, errors = [];
    window.fetch = async (url, options) => { requests.push({url, body: JSON.parse(options.body)}); return { ok: !fail, status: fail ? 500 : 200 }; };
    let controller;
    controller = ChatSelection.create({ state, elements, sessionControls: {}, callbacks: {
        syncControls: () => controller.syncFileApprovalToggle(), addErrorMessage: error => errors.push(error),
    } });
    const assert = (condition, message) => { if (!condition) throw new Error(message); };
    controller.resetChatModeToDefault();
    assert(elements.fileApprovalToggle.getAttribute('aria-pressed') === 'true', 'Default approval state is visible');
    await controller.toggleFileApprovals();
    assert(elements.chatModeSelector.value === 'normal' && requests.length === 0, 'New-session toggle is used by the first send without a PATCH');
    state.sessionId = 'saved'; state.sessions = [{session_id:'saved',chat_mode:'normal'}];
    await controller.toggleFileApprovals();
    assert(requests[0].body.chat_mode === 'inline_edit' && state.sessions[0].chat_mode === 'inline_edit', 'Existing-session toggle persists');
    fail = true;
    await controller.toggleFileApprovals();
    assert(elements.chatModeSelector.value === 'inline_edit' && errors.length === 1, 'Failed save restores the persisted state');
    const count = requests.length;
    state.pendingDeferredReview = {}; controller.syncFileApprovalToggle();
    await controller.toggleFileApprovals();
    assert(elements.fileApprovalToggle.disabled && requests.length === count, 'Pending review prevents mode changes');
    state.pendingDeferredReview = null; state.isLoading = true; controller.syncFileApprovalToggle();
    await controller.toggleFileApprovals();
    assert(elements.fileApprovalToggle.disabled && requests.length === count, 'Startup prevents premature mode changes');
    state.activeChatTaskId = 'accepted-task'; fail = false; controller.syncFileApprovalToggle();
    assert(!elements.fileApprovalToggle.disabled && elements.fileApprovalToggle.title.includes('next message'), 'Accepted response allows next-message approval changes');
    await controller.toggleFileApprovals();
    assert(elements.chatModeSelector.value === 'normal' && requests.length === count+1, 'In-flight approval change persists');
    fail = true; await controller.toggleFileApprovals();
    assert(elements.chatModeSelector.value === 'normal', 'Failed in-flight save rolls back');
    state.isLoading = false; elements.chatModeSelector.value = 'normal'; controller.syncFileApprovalToggle();
    assert(elements.fileApprovalToggle.getAttribute('aria-pressed') === 'false', 'Loading another session updates the visible toggle');
    state.sessions = []; fail = true;
    await controller.toggleFileApprovals();
    assert(elements.chatModeSelector.value === 'normal', 'Failed toggle restores the actual mode even before the session list refreshes');
    return 11;
})()"""


_VERSION_CHECK = r"""(async () => {
    document.body.innerHTML = '<span id="version"></span>';
    const state = {};
    window.fetch = async url => ({ok:true,json:async () => url === 'api/status' ? {system:{app_version:'3.2.1'}} : {tasks:[]}});
    const noop = () => {};
    const controller = SystemStatus.create({state, chatElements:{}, dashElements:{},
        configElements:{applicationVersion:document.querySelector('#version'),advancedShellCoordinates:[]},
        dashboardView:{syncExecutionTaskPolling:noop,displaySystemStatus:noop},
        callbacks:{syncRestartFlag:noop,updateStatus:noop,refreshChatEmptyState:noop}});
    await controller.fetchSystemStatus();
    if (document.querySelector('#version').textContent !== '3.2.1') throw new Error('System tab must show the API application version');
    return 1;
})()"""


_MKDIR_CHECK = r"""(async () => {
    document.body.innerHTML = '<select id="vault"><option value="Vault">Vault</option></select><div id="review"></div>';
    const container = document.querySelector('#review');
    const controller = DeferredReviews.create({state:{sessionId:'session'},elements:{vaultSelector:document.querySelector('#vault')},icons:{},
        utils:{escapeHtml:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')},callbacks:{}});
    const review = {artifact_ref:'mkdir-review',status:'pending',approvals:[{tool_name:'file_write',tool_call_id:'mkdir',args:{operation:'mkdir',path:'Notes/Typo'}}]};
    controller.renderReviewEvent(container,review);
    const input=container.querySelector('[data-arg-name="path"]');
    if (!input || input.value!=='Notes/Typo' || input.disabled) throw new Error('Pending mkdir path must be editable');
    input.value='Notes/Correct';
    let request;
    window.fetch=async(_url,options)=>{request=JSON.parse(options.body);return{ok:true,json:async()=>({status:'submitted'})};};
    container.querySelector('[data-deferred-review-decision="approve"]').click();
    container.querySelector('[data-deferred-review-submit]').click();
    for(let index=0;index<10;index++) await Promise.resolve();
    if (request?.decisions[0]?.override_args?.path!=='Notes/Correct') throw new Error('Approval must submit the edited folder path');
    controller.renderReviewEvent(container,{...review,status:'completed'});
    if (!container.querySelector('[data-arg-name="path"]').disabled) throw new Error('Completed mkdir review must be locked');
    return 3;
})()"""


_MODAL_APPROVAL_CHECK = r"""(async () => {
    const assert = (condition,message) => {if (!condition) throw new Error(message);};
    const state = {sessions:[],sessionId:null,isLoading:false,metadata:{settings:{default_chat_mode:'inline_edit'}}};
    const elements = {vaultSelector:document.querySelector('#vault-selector'),
        chatModeSelector:document.querySelector('#chat-mode-selector'),fileApprovalToggle:document.querySelector('#file-approval-toggle')};
    elements.vaultSelector.innerHTML='<option value="Vault">Vault</option>';
    AssistantMDIcons.hydrateIconButtons(document);
    const originalButton=elements.fileApprovalToggle;
    assert(!document.querySelector('#chat-composer').contains(originalButton),'Main composer must have no approval row');
    const utils={escapeHtml:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')};
    const controls=SessionControls.create({state,elements,icons:AssistantMDIcons,utils,callbacks:{}});
    let selection;
    selection=ChatSelection.create({state,elements,sessionControls:controls,callbacks:{syncControls:()=>selection.syncFileApprovalToggle()}});
    originalButton.addEventListener('click',()=>selection.toggleFileApprovals());
    selection.resetChatModeToDefault();
    controls.openSessionBrowserModal();
    let modal=document.querySelector('#session-browser-modal');
    const header=modal.querySelector('.app-modal-header');
    assert(header.contains(originalButton) && originalButton.closest('.chat-settings-title-row'),'Toggle belongs beside the modal title');
    assert(originalButton.querySelector('svg') && !originalButton.textContent.trim(),'Toggle uses an icon without a visible text label');
    assert(originalButton.getAttribute('aria-label')==='File approvals: On','Icon exposes the enabled state accessibly');
    const buttonBox=originalButton.getBoundingClientRect();
    const closeBox=modal.querySelector('button[data-session-browser-close]').getBoundingClientRect();
    const headerBox=header.getBoundingClientRect();
    const titleBox=header.querySelector('h2').getBoundingClientRect();
    assert(buttonBox.width>0 && buttonBox.left>=headerBox.left && closeBox.right<=headerBox.right+1 && buttonBox.right+16<=closeBox.left && titleBox.right<=buttonBox.left+1,'Title, toggle and close button must fit the header');
    assert(!originalButton.closest('details') && !modal.querySelector('.chat-settings-options').open,'Toggle is available with Options collapsed');
    assert(elements.chatModeSelector.hidden,'Canonical mode selector must not duplicate the header toggle');
    originalButton.click();await Promise.resolve();
    assert(elements.chatModeSelector.value==='normal' && originalButton.getAttribute('aria-pressed')==='false','Header toggle changes the selected mode');
    assert(originalButton.querySelector('svg') && originalButton.getAttribute('aria-label')==='File approvals: Off','Toggling preserves the icon and updates the accessible state');
    modal.querySelector('button[data-session-browser-close]').click();
    assert(!document.querySelector('#session-browser-modal') && document.querySelector('#file-approval-toggle')===originalButton,'Closing preserves the original button');
    assert(originalButton.closest('#chat-settings-modal-controls-source'),'Closed modal parks the button out of the main chat');
    controls.openSessionBrowserModal();
    originalButton.click();await Promise.resolve();
    assert(elements.chatModeSelector.value==='inline_edit' && document.querySelectorAll('#file-approval-toggle').length===1,'Reopen preserves one button and one event handler');
    let checks=11;
    if (innerWidth===390) {
        const swatch=document.createElement('span');swatch.style.color='rgb(var(--accent-primary))';document.body.appendChild(swatch);
        const icon=originalButton.querySelector('svg');
        const settle=()=>new Promise(resolve=>setTimeout(resolve,200));
        const supportsFilters=CSS.supports('filter','drop-shadow(0 0 2px red)');
        const glowRules=[...document.styleSheets].flatMap(sheet=>[...sheet.cssRules]).filter(rule=>
            rule.selectorText?.includes('.chat-settings-approval-toggle[aria-pressed="true"] svg'));
        assert(glowRules.some(rule=>rule.style.getPropertyValue('filter').includes('var(--accent-primary)')),'Glow must use the theme accent');
        checks++;
        for (const theme of ['light','dark','ocean','sunset','lavender','forest']) {
            document.documentElement.dataset.theme=theme;
            elements.chatModeSelector.value='inline_edit';selection.syncFileApprovalToggle();await settle();
            const style=getComputedStyle(icon);
            assert(style.color===getComputedStyle(swatch).color,`Enabled icon must use the ${theme} theme accent`);
            if (supportsFilters) assert(style.filter && style.filter!=='none',`Enabled icon must glow in ${theme}`);
            elements.chatModeSelector.value='normal';selection.syncFileApprovalToggle();await settle();
            assert(getComputedStyle(icon).color!==getComputedStyle(swatch).color,`Off icon must return to a neutral color in ${theme}`);
            if (supportsFilters) assert(!getComputedStyle(icon).filter || getComputedStyle(icon).filter==='none',`Off icon must stop glowing in ${theme}`);
            checks+=2;
        }
        // Obscura currently exposes filter declarations but does not render CSS filters.
        window.approvalGlowFilterSupported=supportsFilters;
        swatch.remove();document.documentElement.removeAttribute('data-theme');
        elements.chatModeSelector.value='inline_edit';selection.syncFileApprovalToggle();
    }
    state.pendingDeferredReview={};selection.syncFileApprovalToggle();
    originalButton.click();await Promise.resolve();
    assert(originalButton.disabled && elements.chatModeSelector.value==='inline_edit','Header respects pending-review locks');
    document.querySelector('#session-browser-modal button[data-session-browser-close]').click();
    return checks;
})()"""


_FLIGHT_CHECK = r"""(async () => {
    const assert=(condition,message)=>{if(!condition)throw new Error(message);};
    const state={isLoading:true,activeChatTaskId:'task',sessionId:'active',metadata:{settings:{}},sessions:[
        {session_id:'active',title:'Active',chat_mode:'inline_edit',can_upgrade_to_v2:true},
        {session_id:'other',title:'Other'}]};
    const ids={vaultSelector:'vault-selector',modelSelector:'model-selector',thinkingSelector:'thinking-selector',sendBtn:'send-btn',chatInput:'chat-input',
        templateSelector:'template-selector',chatModeSelector:'chat-mode-selector',fileApprovalToggle:'file-approval-toggle',
        sessionBrowserTrigger:'session-browser-trigger',newSessionTrigger:'new-session-trigger',
        workspacePathInput:'workspace-path-input',workspacePickerBtn:'workspace-picker-btn',workspaceClearBtn:'workspace-clear-btn'};
    const chatElements=Object.fromEntries(Object.entries(ids).map(([key,id])=>[key,document.getElementById(id)]));
    chatElements.vaultSelector.innerHTML='<option value="Vault">Vault</option>';
    chatElements.chatModeSelector.value='inline_edit';
    chatElements.modelSelector.innerHTML='<option value="first">First</option><option value="next">Next</option>';
    chatElements.templateSelector.innerHTML='<option value="">None</option><option value="Next">Next</option>';
    const utils={escapeHtml:value=>String(value)};
    let switches=0,requests=[];
    window.fetch=async(url,options)=>{requests.push({url,options});return{ok:true};};
    const sessionControls=SessionControls.create({state,elements:chatElements,icons:AssistantMDIcons,utils,callbacks:{
        clearSession:async()=>switches++,loadSession:async()=>switches++}});
    const chatSelection=ChatSelection.create({state,elements:chatElements,sessionControls,callbacks:{
        syncControls:()=>syncLocks(),addErrorMessage:message=>{throw new Error(message);}}});
    const workspacePicker=WorkspacePicker.create({state,elements:chatElements,callbacks:{}});
    const fileReferences={syncInteractionLocks(){}},vaultActivity={syncInteractionLocks(){}};
    const syncSendButtonState=eval('('+window.sendButtonStateSource+')');
    const syncLocks=eval('('+window.chatControlLocksSource+')');
    sessionControls.attachEventListeners();
    chatElements.fileApprovalToggle.addEventListener('click',()=>chatSelection.toggleFileApprovals());
    syncLocks();
    assert(!chatElements.sessionBrowserTrigger.disabled,'Modal opener stays available during a response');
    chatElements.sessionBrowserTrigger.click();
    let modal=document.querySelector('#session-browser-modal');
    assert(modal && !modal.querySelector('[data-chat-settings-flight-notice]').hidden,'In-flight modal explains next-message scope');
    assert(!chatElements.fileApprovalToggle.disabled && !chatElements.thinkingSelector.disabled && !chatElements.modelSelector.disabled && !chatElements.templateSelector.disabled,'Next-message controls are editable');
    assert(chatElements.vaultSelector.disabled && chatElements.workspacePathInput.disabled && chatElements.workspacePickerBtn.disabled && chatElements.workspaceClearBtn.disabled,'Vault and workspace stay locked');
    const newButton=modal.querySelector('[data-session-browser-new]');
    assert(newButton.disabled && chatElements.newSessionTrigger.disabled,'Both new-session controls stay locked');
    const row=modal.querySelector('[data-session-browser-row-id="other"]');
    row.querySelector('.session-dropdown-title').click();newButton.click();
    assert(switches===0 && document.querySelector('#session-browser-modal')===modal,'Session switching stays blocked without closing settings');
    const action=name=>modal.querySelector(`[data-session-action="${name}"][data-session-action-id="active"]`);
    assert(action('delete').disabled && action('upgrade-v2').disabled && !action('export').disabled,'Active-session mutations stay locked and export remains available');
    await sessionControls.deleteCurrent('active');
    assert(requests.length===0,'Programmatic active-session deletion is guarded');
    chatElements.modelSelector.value='next';chatElements.thinkingSelector.value='high';chatElements.templateSelector.value='Next';
    await chatSelection.toggleFileApprovals();
    assert(requests.length===1 && JSON.parse(requests[0].options.body).chat_mode==='normal','Approval changes persist during a response');
    modal.querySelector('[data-session-browser-close]').click();chatElements.sessionBrowserTrigger.click();
    modal=document.querySelector('#session-browser-modal');
    assert(chatElements.modelSelector.value==='next' && chatElements.thinkingSelector.value==='high' && chatElements.templateSelector.value==='Next' && chatElements.chatModeSelector.value==='normal','Reopening preserves next-message settings');
    const filter=modal.querySelector('#session-browser-filter');filter.value='Other';filter.dispatchEvent(new Event('input',{bubbles:true}));
    assert(modal.querySelectorAll('[data-session-browser-row-id]').length===1 && !filter.disabled,'Session filtering remains available');
    state.pendingDeferredReview={};syncLocks();
    assert(chatElements.fileApprovalToggle.disabled,'Pending review still locks approvals');
    state.pendingDeferredReview=null;state.chatModeSavePending={vault:'Vault',sessionId:'active'};syncLocks();
    assert(!chatElements.sendBtn.disabled && chatElements.sendBtn.title.includes('Stop'),'Saving next-message mode must keep Stop available');
    state.isLoading=false;syncLocks();
    assert(chatElements.sendBtn.disabled && !chatElements.chatInput.disabled,'A pending save blocks sending but permits drafting');
    state.chatModeSavePending=null;syncLocks();
    assert(!modal.querySelector('[data-session-browser-new]').disabled && modal.querySelector('[data-chat-settings-flight-notice]').hidden,'Completion restores session controls and removes scope notice');
    modal.querySelector('[data-session-browser-row-id="other"]').click();await Promise.resolve();
    assert(switches===1 && !document.querySelector('#session-browser-modal'),'Session switching works again after completion');
    return 16;
})()"""


async def run(url: str) -> None:
    async with connect(url, max_size=16 * 1024 * 1024) as websocket:
        request_id = 0

        async def command(
            method: str, params: dict[str, Any], session: str | None = None
        ) -> dict[str, Any]:
            nonlocal request_id
            request_id += 1
            request: dict[str, Any] = {
                "id": request_id,
                "method": method,
                "params": params,
            }
            if session:
                request["sessionId"] = session
            await websocket.send(json.dumps(request))
            while True:
                reply = json.loads(await websocket.recv())
                if reply.get("id") == request_id:
                    if "error" in reply:
                        raise RuntimeError(reply["error"])
                    return reply["result"]

        target = await command("Target.createTarget", {"url": "about:blank"})
        attached = await command(
            "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
        )
        session = attached["sessionId"]

        async def evaluate(expression: str) -> Any:
            result = await command(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True, "awaitPromise": True},
                session,
            )
            if "exceptionDetails" in result:
                raise AssertionError(result["exceptionDetails"])
            return result["result"].get("value")

        try:
            await evaluate((_ROOT / "static/js/chat-selection.js").read_text())
            count = await evaluate(_APPROVAL_CHECK)
            print(f"Obscura CDP: {count} approval-toggle checks passed")
            await evaluate((_ROOT / "static/js/system-status.js").read_text())
            count = await evaluate(_VERSION_CHECK)
            print(f"Obscura CDP: {count} version-display checks passed")
            await evaluate((_ROOT / "static/js/deferred-reviews.js").read_text())
            count = await evaluate(_MKDIR_CHECK)
            print(f"Obscura CDP: {count} mkdir-review checks passed")
            markup = (_ROOT / "static/index.html").read_text()
            markup = re.sub(
                r"<script\b[^>]*>[\s\S]*?</script>", "", markup, flags=re.IGNORECASE
            )
            await evaluate((_ROOT / "static/js/icons.js").read_text())
            await evaluate((_ROOT / "static/js/session-controls.js").read_text())
            await evaluate((_ROOT / "static/js/workspace-picker.js").read_text())
            app = (_ROOT / "static/app.js").read_text()
            locks = app[
                app.index("function syncChatControlLocks() {") : app.index(
                    "function syncVaultExplorerButtons() {"
                )
            ].strip()
            await evaluate("window.chatControlLocksSource = " + json.dumps(locks))
            send_state = app[
                app.index("function syncSendButtonState() {") : app.index(
                    "function syncChatControlLocks() {"
                )
            ].strip()
            await evaluate("window.sendButtonStateSource = " + json.dumps(send_state))
            styles = (
                (_ROOT / "static/output.css").read_text()
                + "\n"
                + (_ROOT / "static/app.css").read_text()
            )
            await evaluate(
                "(() => { const style = document.createElement('style'); style.textContent = "
                + json.dumps(styles)
                + "; document.head.appendChild(style); })()"
            )
            for width in (320, 390, 1024):
                await command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": 844,
                        "deviceScaleFactor": 1,
                        "mobile": width < 640,
                    },
                    session,
                )
                await evaluate("document.body.innerHTML = " + json.dumps(markup))
                count = await evaluate(_MODAL_APPROVAL_CHECK)
                print(f"Obscura CDP: {count} modal-approval checks passed at {width}px")
                if width == 390 and not await evaluate(
                    "window.approvalGlowFilterSupported"
                ):
                    print(
                        "Obscura does not render CSS filters; verified glow declaration and theme colors."
                    )
                if width in (390, 1024):
                    await evaluate("document.body.innerHTML = " + json.dumps(markup))
                    count = await evaluate(_FLIGHT_CHECK)
                    print(
                        f"Obscura CDP: {count} in-flight settings checks passed at {width}px"
                    )
        finally:
            await command("Target.closeTarget", {"targetId": target["targetId"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-url", default="ws://127.0.0.1:9223/devtools/browser")
    asyncio.run(run(parser.parse_args().cdp_url))
