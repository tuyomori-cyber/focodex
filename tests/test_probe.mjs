import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor() { this.value = ''; this.options = []; this.listeners = {}; this.disabled = false; this.textContent = ''; this.open = false; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  fire(name) { this.listeners[name]?.({preventDefault() {}}); }
  replaceChildren(...options) { this.options = options; this.value = options[0]?.value ?? ''; }
  append(...options) { this.options.push(...options); if (this.options.length === options.length) this.value = options[0]?.value; }
  setAttribute() {}
}
const elements = Object.fromEntries(['connect', 'project', 'model', 'refresh-models', 'prompt', 'form', 'run', 'stop', 'status', 'diagnostics', 'interaction', 'interaction-title', 'interaction-details', 'approval-view', 'operation-label', 'operation-text', 'operation-cwd', 'approval-reason', 'approval-scope', 'approval-warning', 'questions', 'approve', 'decline', 'answer-question', 'answer', 'info', 'git-refresh', 'git-message', 'git-files'].map(id => [id, new Element()]));
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
elements['git-refresh'].fire('click');
assert.equal(sent.at(-1).type, 'git_snapshot');
assert.equal(elements['git-refresh'].disabled, true);
reply({type: 'git_snapshot', id: sent.at(-1).id, files: [
  {path: 'src/app.py', status: ' M', unstagedDiff: '+change', stagedDiff: ''},
  {path: 'test.txt', status: '??', unstagedDiff: '+test', stagedDiff: ''}]});
assert.equal(elements['git-files'].options.length, 2);
assert.equal(elements['git-files'].options[0].open, false, '差分は初期状態で閉じる');
assert.match(elements['git-files'].options[0].options[0].textContent, /src\/app.py.*変更/);
assert.equal(elements['git-files'].options[0].options[2].textContent, '+change');
assert.match(elements['git-files'].options[1].options[0].textContent, /test.txt.*未追跡/);
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
assert.equal(elements.stop.disabled, false, '実行中は停止できる');
elements.stop.fire('click');
assert.equal(sent.at(-1).type, 'stop');
assert.equal(elements.stop.disabled, true, '停止要求を重複送信しない');
reply({type: 'stopping', id: sent.at(-1).targetId});
assert.equal(elements.status.textContent, '停止要求中…');
reply({type: 'result', id: sent.at(-1).targetId, status: 'interrupted', stopDiagnostics: {
  requested: true, interruptAcknowledged: true, fallbackUsed: false,
  turnStatus: 'interrupted', process: {processGroupGone: true}
}});
assert.equal(elements.status.textContent, '停止済み');
assert.match(elements.diagnostics.textContent, /専用プロセス群=消滅確認/);
elements.form.fire('submit');
elements.stop.fire('click');
reply({type: 'result', id: sent.at(-1).targetId, status: 'interrupted', stopDiagnostics: {
  requested: true, interruptAcknowledged: false, fallbackUsed: true,
  turnStatus: null, process: {processGroupGone: true}
}});
assert.equal(elements.status.textContent, '停止済み');
assert.match(elements.diagnostics.textContent, /Codex終了=未受信/);
assert.match(elements.diagnostics.textContent, /5秒後の強制終了=実施/);
elements.form.fire('submit');
elements.stop.fire('click');
reply({type: 'result', id: sent.at(-1).targetId, status: 'failed', stopDiagnostics: {
  requested: true, interruptAcknowledged: false, fallbackUsed: true,
  turnStatus: null, process: {processGroupGone: false}
}});
assert.equal(elements.status.textContent, '失敗');
assert.match(elements.diagnostics.textContent, /専用プロセス群=確認不可/);
elements.form.fire('submit');
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
elements['refresh-models'].fire('click');
reply({type: 'models', id: sent.at(-1).id, models: [{model: 'third'}]});
elements.model.value = 'third';
elements.model.fire('change');
elements.form.fire('submit');
let runId = sent.at(-1).id;
reply({type: 'interaction', id: runId, requestId: 'approval-1', itemId: 'item-1', kind: 'command',
  approvable: true, approvalScope: 'once', details: {command: 'echo test', cwd: '/project', reason: 'test'}});
assert.equal(elements.status.textContent, '承認待ち');
assert.equal(elements['operation-text'].textContent, 'echo test');
assert.match(elements['approval-reason'].textContent, /test/);
assert.match(elements['approval-scope'].textContent, /承認不可/);
assert.equal(elements.interaction.hidden, false);
assert.equal(elements.approve.hidden, true, '汎用コマンドは承認できない');
elements.decline.fire('click');
assert.equal(sent.at(-1).decision, 'decline');
reply({type: 'answered', id: runId, requestId: 'approval-1'});
assert.equal(elements.interaction.hidden, true);
reply({type: 'result', id: runId, status: 'completed', text: '承認後に完了'});
elements.form.fire('submit');
runId = sent.at(-1).id;
reply({type: 'interaction', id: runId, requestId: 'missing-reason', itemId: 'item-1',
  kind: 'command', approvable: true, approvalScope: 'once',
  details: {command: 'echo test', cwd: '/project', reason: null}});
assert.equal(elements.approve.hidden, true);
assert.match(elements['approval-reason'].textContent, /提供されていません/);
assert.match(elements['approval-warning'].textContent, /許可していません/);
reply({type: 'result', id: runId, status: 'completed'});
elements.form.fire('submit');
runId = sent.at(-1).id;
reply({type: 'interaction', id: runId, requestId: 'approval-resolved', itemId: 'item-1',
  kind: 'command', approvable: true, approvalScope: 'once',
  details: {command: 'echo test', cwd: '/project', reason: 'test'}});
reply({type: 'interaction_resolved', id: runId, requestId: 'approval-resolved', itemId: 'item-1'});
assert.equal(elements.interaction.hidden, true, '失効した承認画面を閉じる');
reply({type: 'result', id: runId, status: 'completed'});
elements.form.fire('submit');
runId = sent.at(-1).id;
reply({type: 'interaction', id: runId, requestId: 'file-1', itemId: 'file-item', kind: 'file',
  approvable: false, approvalScope: 'once',
  details: {grantRoot: '/tmp/other', reason: 'test'}});
assert.equal(elements.approve.hidden, true, '変更内容を表示できない承認は選ばせない');
assert.match(elements['approval-warning'].textContent, /許可していません/);
elements.decline.fire('click');
assert.equal(sent.at(-1).decision, 'decline');
reply({type: 'answered', id: runId, requestId: 'file-1'});
reply({type: 'result', id: runId, status: 'completed'});
elements.form.fire('submit');
runId = sent.at(-1).id;
reply({type: 'interaction', id: runId, requestId: 'file-2', itemId: 'file-item', kind: 'file',
  approvable: true, approvalScope: 'once', details: {reason: 'test',
    changes: [{path: '/tmp/test.txt', kind: 'add', diff: '+test'}]}});
assert.match(elements['operation-text'].textContent, /\/tmp\/test.txt/);
assert.match(elements['operation-text'].textContent, /\+test/);
assert.equal(elements.approve.hidden, true);
elements.decline.fire('click');
assert.equal(sent.at(-1).decision, 'decline');
reply({type: 'answered', id: runId, requestId: 'file-2'});
reply({type: 'result', id: runId, status: 'completed'});
elements.form.fire('submit');
runId = sent.at(-1).id;
reply({type: 'interaction', id: runId, requestId: 'question-1', itemId: 'question-item', kind: 'question', details: {questions: [
  {id: 'choice', header: '種類', question: 'どちらですか？', options: [{label: 'A'}, {label: 'B'}]},
  {id: 'detail', header: '詳細', question: '説明してください', options: null}
]}});
assert.equal(elements.status.textContent, '回答待ち');
const [, choice, , , detail] = elements.questions.options;
choice.value = 'B';
detail.value = '自由回答';
elements['answer-question'].fire('click');
assert.equal(sent.at(-1).answers.choice, 'B');
assert.equal(sent.at(-1).answers.detail, '自由回答');
assert.equal(sent.at(-1).itemId, 'question-item');
reply({type: 'answered', id: runId, requestId: 'question-1'});
reply({type: 'result', id: runId, status: 'completed'});
console.log('モデル選択・停止診断・承認・追加質問の画面ロジック: OK');
