"""Run focused UI behavior checks through an existing Obscura CDP server."""

import argparse
import asyncio
import json
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
    assert(elements.fileApprovalToggle.disabled && requests.length === count, 'Active run prevents mode changes');
    state.isLoading = false; elements.chatModeSelector.value = 'normal'; controller.syncFileApprovalToggle();
    assert(elements.fileApprovalToggle.getAttribute('aria-pressed') === 'false', 'Loading another session updates the visible toggle');
    return 7;
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


_SETTINGS_SETUP = r"""(() => {
    document.body.innerHTML = '<div id="settings"></div><p id="feedback"></p>';
    const state = {settings:[],settingsFilter:'',settingEditKey:null,settingDraftValue:'',isSavingSetting:false};
    const noop = () => {};
    window.ConfigurationPanelRuntime = {
        state, elements:{settingsList:document.querySelector('#settings'),settingsFeedback:document.querySelector('#feedback')},
        actions:{updateImportOcrAvailability:noop},constants:{},callbacks:{},resources:{},timers:{},
        helpers:{ escapeHtml:value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
            iconButton:()=>'', iconSvg:()=>'',setStatus:(element,text)=>{element.textContent=text;},
            notifyConfigChanged:async()=>{},safeJson:response=>response.json(),withRestartNotice:(_text,result)=>({text:'Saved',restart:result.restart_required}) }
    };
})()"""

_SETTINGS_CHECK = r"""(async () => {
    const {state,actions,elements} = ConfigurationPanelRuntime;
    const assert = (condition,message) => {if (!condition) throw new Error(message);};
    state.settings = [
        {key:'bool',value:'false',control:{value_type:'boolean',choices:[]}},
        {key:'int',value:'0',control:{value_type:'integer',choices:[]}},
        {key:'float',value:'1.5',control:{value_type:'number',choices:[]}},
        {key:'text',value:'007',control:{value_type:'string',choices:[]}},
        {key:'json',value:'[]',control:{value_type:'json',choices:[]}},
        {key:'mode',value:'unfamiliar',control:{value_type:'string',choices:['normal','inline_edit']}},
    ];
    let requests = [];
    window.fetch = async (url, options) => {
        const body = JSON.parse(options.body); requests.push({url,...body});
        const key = decodeURIComponent(url.split('/').at(-1));
        return {ok:true,json:async()=>({...state.settings.find(item=>item.key===key),value:body.value})};
    };
    elements.settingsList.addEventListener('input',actions.handleSettingsInputChange);
    elements.settingsList.addEventListener('click',actions.handleSettingsTableClick);
    const field = () => elements.settingsList.querySelector('[data-field="setting-value"]');
    const change = value => {field().value=value;field().dispatchEvent(new Event('input',{bubbles:true}));};
    actions.startSettingEdit('bool');
    assert(field().type==='checkbox' && !field().checked,'False is an unchecked checkbox');
    await actions.saveSettingValue('bool');
    assert(requests.at(-1).value==='false','False survives save');
    actions.startSettingEdit('bool');field().checked=true;field().dispatchEvent(new Event('input',{bubbles:true}));
    await actions.saveSettingValue('bool');
    assert(requests.at(-1).value==='true','Checkbox sends a boolean string');
    actions.startSettingEdit('int');
    assert(field().type==='number' && field().value==='0' && field().step==='1','Zero uses an integer input');
    await actions.saveSettingValue('int');
    assert(requests.at(-1).value==='0','Zero survives save');
    actions.startSettingEdit('float');
    assert(field().step==='any','Float input accepts decimals');
    actions.startSettingEdit('text');
    assert(field().type==='text' && field().value==='007','Numeric-looking strings stay text');
    actions.startSettingEdit('mode');
    assert(field().tagName==='SELECT' && field().value==='unfamiliar','Unknown current choice is preserved');
    elements.settingsList.querySelector('[data-action="toggle-setting-raw"]').click();
    assert(field().type==='text' && field().value==='unfamiliar','Raw editor preserves the current value');
    change('normal');await actions.saveSettingValue('mode');
    assert(requests.at(-1).value==='normal','Raw fallback still uses the normal save contract');
    actions.startSettingEdit('json');assert(field().tagName==='TEXTAREA','JSON uses a multiline field');
    change('{');const count=requests.length;await actions.saveSettingValue('json');
    assert(requests.length===count && field().value==='{','Invalid JSON remains editable without a request');
    change('["value"]');await actions.saveSettingValue('json');
    assert(requests.at(-1).value==='["value"]','JSON round-trips as an explicit string payload');
    return 11;
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
            await evaluate(_SETTINGS_SETUP)
            await evaluate((_ROOT / "static/js/configuration/settings.js").read_text())
            count = await evaluate(_SETTINGS_CHECK)
            print(f"Obscura CDP: {count} settings-control checks passed")
            await evaluate((_ROOT / "static/js/deferred-reviews.js").read_text())
            count = await evaluate(_MKDIR_CHECK)
            print(f"Obscura CDP: {count} mkdir-review checks passed")
        finally:
            await command("Target.closeTarget", {"targetId": target["targetId"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-url", default="ws://127.0.0.1:9223/devtools/browser")
    asyncio.run(run(parser.parse_args().cdp_url))
