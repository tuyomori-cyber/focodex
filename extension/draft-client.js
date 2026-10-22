/* UI for the durable receive buffer. Receiving never changes an input automatically. */
(() => {
  const $ = id => document.getElementById(id);
  let port, sequence = 0, buffer = [], ready = false, active = false, queue = Promise.resolve();
  const calls = new Map();
  const undoTexts = {prompt: null, "commit-message": null};
  const targets = [["prompt", "prompt"], ["commit-message", "commit"]];
  function report(text) { $("draft-connection-status").textContent = text; }
  function rpc(type, requestId) {
    if (!port) return Promise.reject(Error("受信接続が切れています。"));
    const id = ++sequence;
    return new Promise((resolve, reject) => { const timer = setTimeout(() => reject(Error("受信確認がタイムアウトしました。")), 5000); calls.set(id, {resolve, reject, timer}); port.postMessage({rpc: id, type, ...(requestId ? {requestId} : {})}); });
  }
  function render() {
    const section = $("received-draft"), list = $("received-text");
    for (const [target, suffix] of targets) {
      $("undo-" + suffix).hidden = undoTexts[target] === null;
      $("undo-" + suffix).disabled = !ready || active || undoTexts[target] === null;
      $("clear-" + suffix).hidden = !$(target).value;
      $("clear-" + suffix).disabled = !ready || active || !$(target).value;
    }
    section.hidden = buffer.length === 0; list.textContent = "";
    for (const item of [...buffer].reverse()) {
      const row = document.createElement("section"), text = document.createElement("pre"), actions = document.createElement("div"), apply = document.createElement("button"), remove = document.createElement("button");
      row.className = "received-item"; text.className = "received-item-text"; actions.className = "received-item-actions";
      text.textContent = item.text; apply.type = remove.type = "button"; apply.textContent = "指示文へ"; remove.textContent = "削除";
      apply.disabled = !ready || active; remove.disabled = !ready || active;
      apply.addEventListener("click", () => { if (!ready || active) return; try { undoTexts.prompt = $("prompt").value; $("prompt").value = item.text; $("prompt").dispatchEvent(new Event("input", {bubbles: true})); render(); } catch { report("指示文へ反映できません。"); } });
      remove.addEventListener("click", () => queue = queue.then(async () => { await rpc("remove", item.requestId); await refresh(); }).catch(error => report(error.message)));
      const commitApply = document.createElement("button");
      commitApply.type = "button"; commitApply.textContent = "Commit欄へ";
      commitApply.disabled = !ready || active;
      commitApply.addEventListener("click", () => {
        if (!ready || active) return;
        undoTexts["commit-message"] = $("commit-message").value;
        $("commit-message").value = item.text;
        $("commit-message").dispatchEvent(new Event("input", {bubbles: true})); render();
      });
      actions.append(apply, commitApply, remove); row.append(text, actions); list.append(row);
    }
  }
  async function refresh() { const result = await rpc("get"); buffer = result.buffer; ready = true; window.focodexDraftReady = true; window.controls?.(); render(); report(""); }
  function connect() {
    if (port) port.disconnect(); ready = false; window.focodexDraftReady = false; window.controls?.(); port = browser.runtime.connect({name: "focodex-buffer-v1"});
    port.onMessage.addListener(message => { const call = calls.get(message.rpc); if (!call) return; clearTimeout(call.timer); calls.delete(message.rpc); message.error ? call.reject(Error(message.error)) : call.resolve(message); });
    port.onDisconnect.addListener(() => { if (!port) return; port = null; ready = false; window.focodexDraftReady = false; window.controls?.(); render(); report("受信接続が切れました。再接続してください。"); });
    refresh().catch(error => report(error.message));
  }
  browser.runtime.onMessage.addListener(message => { if (message?.type === "focodex.buffer.changed" && port) refresh().catch(error => report(error.message)); });
  $("draft-reconnect").addEventListener("click", connect);
  for (const [target, suffix] of targets) {
    $("undo-" + suffix).addEventListener("click", () => {
      if (undoTexts[target] === null || active || !ready) return;
      $(target).value = undoTexts[target]; undoTexts[target] = null;
      $(target).dispatchEvent(new Event("input", {bubbles: true})); render();
    });
    $("clear-" + suffix).addEventListener("click", () => {
      if (!$(target).value || active || !ready) return;
      undoTexts[target] = $(target).value; $(target).value = "";
      $(target).dispatchEvent(new Event("input", {bubbles: true})); render();
    });
  }
  window.focodexDraftRefresh = running => { active = Boolean(running); render(); };
  connect();
})();
