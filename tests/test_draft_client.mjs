import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
class Element {
  constructor() { this.value = ''; this.children = []; this.listeners = {}; }
  addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
  dispatchEvent(event) { for (const fn of this.listeners[event.type] || []) fn(event); }
  fire(type) { this.dispatchEvent({type}); }
  append(...children) { this.children.push(...children); }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text; }
}
const ids = ['draft-connection-status', 'received-draft', 'received-text', 'prompt', 'commit-message',
  'undo-prompt', 'clear-prompt', 'undo-commit', 'clear-commit', 'draft-reconnect'];
const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
elements.prompt.value = '元の指示'; elements['commit-message'].value = '元のCommit文';
const calls = [];
const port = {onMessage: {addListener(fn) { this.emit = fn; }}, onDisconnect: {addListener() {}},
  postMessage(message) {
    calls.push(message);
    this.onMessage.emit({rpc: message.rpc, buffer: [{requestId: 'id', text: '音声の文章'}]});
  }};
const window = {controls() {}};
vm.runInNewContext(fs.readFileSync(new URL('../extension/draft-client.js', import.meta.url), 'utf8'), {
  window, document: {getElementById: id => elements[id], createElement: () => new Element()},
  browser: {runtime: {connect: () => port, onMessage: {addListener() {}}}},
  Event: class {constructor(type) { this.type = type; }}, setTimeout, clearTimeout
});
await new Promise(resolve => setImmediate(resolve));
const actions = () => elements['received-text'].children[0].children[1].children;
actions()[1].fire('click');
assert.equal(elements['commit-message'].value, '音声の文章');
assert.equal(elements.prompt.value, '元の指示');
actions()[0].fire('click');
assert.equal(elements.prompt.value, '音声の文章');
elements['undo-commit'].fire('click');
assert.equal(elements['commit-message'].value, '元のCommit文');
assert.equal(elements.prompt.value, '音声の文章');
elements['undo-prompt'].fire('click');
assert.equal(elements.prompt.value, '元の指示');
elements['clear-commit'].fire('click');
assert.equal(elements['commit-message'].value, '');
elements['undo-commit'].fire('click');
assert.equal(elements['commit-message'].value, '元のCommit文');
window.focodexDraftRefresh(true);
assert.equal(actions()[1].disabled, true);
actions()[1].fire('click');
elements['clear-commit'].fire('click');
assert.equal(elements['commit-message'].value, '元のCommit文');
assert.deepEqual(calls.map(call => call.type), ['get'], '反映・Undo・クリアはHostやGitを呼ばない');
console.log('Commit欄への反映・独立Undo・クリア・実行中抑止: OK');
