const $ = id => document.getElementById(id);
let port = null;
let busy = false;
let pendingId = null;
let projectsId = null;
let pendingKind = null;
function savedModel() {
  try { return localStorage.getItem(`focodex:model:${$("project").value}`); }
  catch { return null; }
}
function clearModels() {
  $("model").replaceChildren(new Option("モデルを選択してください", ""));
}
function fetchModels() {
  if (!port || busy || !$("project").value) return;
  clearModels();
  pendingId = crypto.randomUUID();
  pendingKind = "models";
  busy = true;
  $("status").textContent = "モデル一覧を取得中…";
  try {
    port.postMessage({type: "models", id: pendingId, projectId: $("project").value});
  } catch (error) {
    busy = false;
    pendingId = null;
    pendingKind = null;
    $("status").textContent = `モデル取得エラー: ${error.message}`;
  }
  controls();
}
$("project").addEventListener("change", fetchModels);
$("refresh-models").addEventListener("click", fetchModels);
$("model").addEventListener("change", () => {
  try { localStorage.setItem(`focodex:model:${$("project").value}`, $("model").value); }
  catch { /* Selection still works when local storage is unavailable. */ }
  controls();
});
function controls() {
  $("connect").disabled = Boolean(port);
  $("project").disabled = busy || !port || !$("project").options.length;
  $("model").disabled = busy || !port || $("model").options.length < 2;
  $("refresh-models").disabled = $("project").disabled;
  $("run").disabled = $("project").disabled || $("model").disabled || !$("model").value || !$("prompt").value.trim();
  $("prompt").disabled = busy;
}
$("prompt").addEventListener("input", controls);
$("connect").addEventListener("click", () => {
  if (port) return;
  const connection = browser.runtime.connectNative("focodex");
  port = connection;
  $("project").replaceChildren();
  clearModels();
  $("status").textContent = "Hostへ接続中…";
  connection.onDisconnect.addListener(() => {
    if (port !== connection) return;
    const error = connection.error?.message;
    port = null;
    busy = false;
    pendingId = null;
    pendingKind = null;
    $("status").textContent = error ? `接続エラー: ${error}` : "接続が切れました。実行結果は未確認です。";
    controls();
  });
  connection.onMessage.addListener(message => {
    if (port !== connection) return;
    if (message.type === "projects" && message.id === projectsId) {
      for (const project of message.projects) {
        const option = document.createElement("option");
        option.value = project.id;
        option.textContent = `${project.name} (${project.path})`;
        $("project").append(option);
      }
      $("status").textContent = "接続済み・待機";
      fetchModels();
    } else if (message.type === "models" && message.id === pendingId && pendingKind === "models") {
      busy = false;
      pendingId = null;
      pendingKind = null;
      clearModels();
      for (const model of message.models) {
        const label = `${model.displayName || model.model} (${model.model})${model.isDefault ? "・推奨" : ""}`;
        $("model").append(new Option(label, model.model));
      }
      const saved = savedModel();
      if (message.models.some(model => model.model === saved)) $("model").value = saved;
      $("status").textContent = message.models.length ? "モデルを選択して送信してください。" : "モデル候補がありません。一覧を再取得してください。";
    } else if (message.type === "started" && message.id === pendingId) {
      $("status").textContent = pendingKind === "models" ? "モデル一覧を取得中…" : "Codex実行中…";
    } else if (message.type === "result" && message.id === pendingId) {
      busy = false;
      pendingId = null;
      pendingKind = null;
      const labels = {completed: "完了", failed: "失敗", interrupted: "中断", timeout: "時間切れ", unsupported_request: "承認・質問は未対応"};
      $("status").textContent = labels[message.status] ?? `未知の状態: ${message.status}`;
      $("answer").textContent = message.text || "（応答本文なし）";
      $("info").textContent = JSON.stringify(message, null, 2);
      if (message.error) {
        $("status").textContent += `：${typeof message.error === "string" ? message.error : JSON.stringify(message.error)}`;
      }
    } else if (message.type === "error") {
      // Validation errors do not cancel a different in-flight request.
      $("status").textContent = `エラー: ${message.error}`;
      if (message.id === pendingId || message.id == null) {
        busy = false;
        pendingId = null;
        pendingKind = null;
      }
    }
    controls();
  });
  projectsId = crypto.randomUUID();
  connection.postMessage({type: "projects", id: projectsId});
  controls();
});
$("form").addEventListener("submit", event => {
  event.preventDefault();
  if (busy || !port || !$("project").value || !$("model").value || !$("prompt").value.trim()) return;
  pendingId = crypto.randomUUID();
  pendingKind = "run";
  busy = true;
  $("answer").textContent = "";
  $("info").textContent = "";
  $("status").textContent = "送信中…";
  try {
    port.postMessage({type: "run", id: pendingId, projectId: $("project").value, model: $("model").value, text: $("prompt").value});
  } catch (error) {
    busy = false;
    pendingId = null;
    pendingKind = null;
    $("status").textContent = `送信エラー: ${error.message}`;
  }
  controls();
});
