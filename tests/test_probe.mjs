import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor() { this.value = ''; this.options = []; this.listeners = {}; this.disabled = false; this.textContent = ''; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  fire(name) { this.listeners[name]?.({preventDefault() {}}); }
  replaceChildren(...options) { this.options = options; this.value = options[0]?.value ?? ''; }
  append(option) { this.options.push(option); if (this.options.length === 1) this.value = option.value; }
}
const elements = Object.fromEntries(['connect', 'project', 'model', 'refresh-models', 'prompt', 'form', 'run', 'status', 'answer', 'info'].map(id => [id, new Element()]));
elements.prompt.value = '接続確認';
const sent = [];
const storage = new Map();
const port = {
  postMessage: message => sent.push(message),
  onMessage: {addListener(callback) { this.emit = callback; }},
  onDisconnect: {addListener(callback) { this.emit = callback; }}
};
let sequence = 0;
vm.runInNewContext(fs.readFileSync(new URL('../extension/probe.js', import.meta.url), 'utf8'), {
  document: {getElementById: id => elements[id], createElement: () => new Element()},
  Option: function (label, value) { this.textContent = label; this.value = value; },
  browser: {runtime: {connectNative: () => port}},
  crypto: {randomUUID: () => `request-${++sequence}`},
  localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)}
});
const reply = value => port.onMessage.emit(value);
elements.connect.fire('click');
reply({type: 'projects', id: sent.at(-1).id, projects: [{id: 'project', name: 'Project', path: '/project'}]});
assert.equal(sent.at(-1).type, 'models');
assert.equal(elements.run.disabled, true);
reply({type: 'models', id: sent.at(-1).id, models: [{model: 'first', displayName: 'First'}, {model: 'second', displayName: 'Second'}]});
assert.equal(elements.run.disabled, true, '送信前の明示選択が必要');
elements.model.value = 'first';
elements.model.fire('change');
assert.equal(elements.run.disabled, false);
elements.form.fire('submit');
assert.equal(sent.at(-1).model, 'first');
assert.equal(elements.model.disabled, true, '実行中の選択変更を防ぐ');
reply({type: 'result', id: sent.at(-1).id, status: 'failed', error: 'model unavailable'});
assert.equal(elements.model.disabled, false);
assert.equal(elements.prompt.value, '接続確認', '失敗しても指示を保持する');
elements.model.value = 'second';
elements.model.fire('change');
elements.form.fire('submit');
assert.equal(sent.at(-1).model, 'second', '失敗後に別モデルで再試行できる');
reply({type: 'result', id: sent.at(-1).id, status: 'completed', text: '成功'});
assert.equal(elements.answer.textContent, '成功');
elements['refresh-models'].fire('click');
reply({type: 'models', id: sent.at(-1).id, models: [{model: 'second'}]});
assert.equal(elements.model.value, 'second', '保存した選択が一覧に残っていれば復元する');
elements['refresh-models'].fire('click');
reply({type: 'models', id: sent.at(-1).id, models: [{model: 'third'}]});
assert.equal(elements.model.value, '', '候補から消えた選択は復元しない');
assert.equal(elements.run.disabled, true);
elements['refresh-models'].fire('click');
reply({type: 'result', id: sent.at(-1).id, status: 'failed', error: 'list failed'});
assert.equal(elements['refresh-models'].disabled, false, '一覧取得失敗後も再取得できる');
assert.equal(elements.run.disabled, true);
console.log('モデル選択・失敗後の再選択・保存復元・一覧取得失敗からの復帰: OK');
