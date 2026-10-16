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
        finally:
            await command("Target.closeTarget", {"targetId": target["targetId"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-url", default="ws://127.0.0.1:9223/devtools/browser")
    asyncio.run(run(parser.parse_args().cdp_url))
