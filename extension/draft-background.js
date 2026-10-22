/* External API: durable receive buffer only. Never forwards to a Native Host. */
(() => {
  const SOURCE = "fox-voice-editor@foxbook.example";
  const KEY = "focodexReceiveBufferV1";
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const MAX_TEXT = 20000, MAX_BUFFER = 3, MAX_RECEIPTS = 100, TTL = 86400000;
  let state; let queue = Promise.resolve(); let recentCalls = [];
  const serial = task => { const result = queue.then(task); queue = result.catch(() => {}); return result; };
  const validText = text => typeof text === "string" && text.length <= MAX_TEXT && Boolean(text.trim());
  const validItem = item => item && UUID.test(item.requestId) && validText(item.text) && Number.isSafeInteger(item.at);
  const validState = value => value && value.version === 2 && Array.isArray(value.buffer) && Array.isArray(value.receipts)
    && value.buffer.length <= MAX_BUFFER && value.receipts.length <= MAX_RECEIPTS
    && value.buffer.every(validItem) && value.receipts.every(validItem);
  async function load() {
    if (state) return;
    const saved = await browser.storage.local.get([KEY, "focodexDraftInboxV1"]);
    if (saved[KEY] !== undefined) { if (!validState(saved[KEY])) throw Error("storage_invalid"); state = saved[KEY]; return; }
    const legacy = saved.focodexDraftInboxV1;
    if (legacy !== undefined && (!legacy || legacy.version !== 1 || (legacy.pending !== null &&
        (!legacy.pending || !UUID.test(legacy.pending.requestId) || !validText(legacy.pending.text))))) throw Error("storage_invalid");
    state = {version: 2, buffer: legacy?.pending ? [{...legacy.pending, at: Date.now()}] : [], receipts: []};
    await browser.storage.local.set({[KEY]: state});
  }
  async function save(next) { await browser.storage.local.set({[KEY]: next}); state = next; }
  const reply = (requestId, status, reason) => ({version: 1, requestId, status, ...(reason ? {reason} : {})});
  function authorized(sender) { try { const url = new URL(sender?.url); return sender?.id === SOURCE && url.protocol === "moz-extension:" && Boolean(url.hostname) && url.pathname === "/editor.html" && !url.search && !url.hash && !url.username && !url.password && !url.port; } catch { return false; } }
  function validExternal(message) {
    if (!message || typeof message !== "object" || Array.isArray(message) || Object.keys(message).length !== 4) return false;
    if (!["type", "version", "requestId", "text"].every(key => Object.prototype.hasOwnProperty.call(message, key))) return false;
    return message.type === "focodex.buffer.receive" && message.version === 1 && typeof message.requestId === "string"
      && UUID.test(message.requestId) && validText(message.text);
  }
  function notify() { browser.runtime.sendMessage({type: "focodex.buffer.changed"}).catch(() => {}); }
  browser.runtime.onMessageExternal.addListener((message, sender) => {
    if (!authorized(sender)) return Promise.resolve(reply(null, "rejected", "unauthorized"));
    if (!validExternal(message)) return Promise.resolve(reply(null, "rejected", "invalid_message"));
    const now = Date.now(); recentCalls = recentCalls.filter(at => now - at < 10000);
    if (recentCalls.length >= 20) return Promise.resolve(reply(message.requestId, "rejected", "rate_limited"));
    recentCalls.push(now);
    return serial(async () => {
      await load();
      const receipts = state.receipts.filter(item => now - item.at < TTL).slice(-MAX_RECEIPTS);
      const existing = receipts.find(item => item.requestId === message.requestId);
      if (existing) return existing.text === message.text ? reply(message.requestId, "duplicate") : reply(message.requestId, "rejected", "request_id_conflict");
      const item = {requestId: message.requestId, text: message.text, at: now};
      await save({version: 2, buffer: [...state.buffer, item].slice(-MAX_BUFFER), receipts: [...receipts, item].slice(-MAX_RECEIPTS)});
      notify(); return reply(message.requestId, "accepted");
    }).catch(() => reply(message.requestId, "rejected", "storage_error"));
  });
  browser.runtime.onConnect.addListener(port => {
    if (port.name !== "focodex-buffer-v1" || port.sender?.id !== browser.runtime.id) { port.disconnect(); return; }
    port.onMessage.addListener(message => serial(async () => {
      if (!message || !Number.isSafeInteger(message.rpc) || message.rpc < 1) return;
      await load();
      if (message.type === "get") { port.postMessage({rpc: message.rpc, buffer: state.buffer}); return; }
      if (message.type !== "remove" || !UUID.test(message.requestId)) { port.postMessage({rpc: message.rpc, error: "invalid_request"}); return; }
      const buffer = state.buffer.filter(item => item.requestId !== message.requestId);
      if (buffer.length === state.buffer.length) { port.postMessage({rpc: message.rpc, error: "stale_request"}); return; }
      await save({...state, buffer}); notify(); port.postMessage({rpc: message.rpc, buffer});
    }).catch(() => { try { port.postMessage({rpc: message?.rpc, error: "storage_error"}); } catch {} }));
  });
})();
