import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const SOURCE = {id: 'fox-voice-editor@foxbook.example', url: 'moz-extension://voice/editor.html'};
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const receive = (n, text = `文章${n}`) => ({type: 'focodex.buffer.receive', version: 1, requestId: id(n), text});
function event() { const listeners = []; return {addListener(fn) { listeners.push(fn); }, emit(...args) { return listeners.map(fn => fn(...args)); }}; }
function harness(saved = {}) {
  const data = structuredClone(saved), effects = {reads: 0, writes: 0}, env = {failWrite: false};
  const runtime = {id: 'focodex@foxbook.example', onMessageExternal: event(), onConnect: event(), sendMessage: async () => {}};
  const browser = {runtime, storage: {local: {get: async keys => { effects.reads++; const out = {}; for (const key of Array.isArray(keys) ? keys : [keys]) out[key] = structuredClone(data[key]); return out; }, set: async values => { effects.writes++; if (env.failWrite) throw Error('disk'); Object.assign(data, structuredClone(values)); }}}};
  vm.runInNewContext(fs.readFileSync(new URL('../extension/draft-background.js', import.meta.url), 'utf8'), {browser, URL});
  return {data, effects, env, external: (message, sender = SOURCE) => runtime.onMessageExternal.emit(message, sender)[0]};
}
{ const h = harness(); assert.equal((await h.external(receive(1), {...SOURCE, id: 'other'})).reason, 'unauthorized'); assert.equal((await h.external({...receive(1), extra: true})).reason, 'invalid_message'); assert.deepEqual(h.effects, {reads: 0, writes: 0}); }
{ const h = harness(); for (let n = 1; n <= 4; n++) { const result = await h.external(receive(n)); assert.equal(result.status, 'accepted', JSON.stringify(result)); } assert.deepEqual(h.data.focodexReceiveBufferV1.buffer.map(item => item.requestId), [id(2), id(3), id(4)]); assert.equal((await h.external(receive(1))).status, 'duplicate'); assert.equal((await h.external(receive(1, '別文'))).reason, 'request_id_conflict'); }
{ const h = harness(); h.env.failWrite = true; assert.equal((await h.external(receive(1))).reason, 'storage_error'); assert.equal(h.data.focodexReceiveBufferV1, undefined); const old = harness({focodexDraftInboxV1: {version: 1, pending: {requestId: id(9), text: '旧文'}, receipts: []}}); assert.equal((await old.external(receive(1))).status, 'accepted'); assert.equal(old.data.focodexReceiveBufferV1.buffer[0].text, '旧文'); }
{ const h = harness(); await h.external(receive(1)); const reboot = harness(h.data); assert.equal((await reboot.external(receive(1))).status, 'duplicate'); }
console.log('receive buffer tests passed');
