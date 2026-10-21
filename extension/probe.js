const $ = id => document.getElementById(id);
let port = null;
let busy = false;
let pendingId = null;
let projectsId = null;
let pendingKind = null;
let stopping = false;
let stopRequestId = null;
let interaction = null;
let responseRequestId = null;
let questionInputs = [];
function clearInteraction() {
  interaction = null;
  responseRequestId = null;
  questionInputs = [];
  $("interaction").hidden = true;
  $("questions").replaceChildren();
}
function showInteraction(message) {
  const details = message.details || {};
  interaction = {requestId: message.requestId, itemId: message.itemId,
    kind: message.kind, approvable: false};
  responseRequestId = null;
  questionInputs = [];
  $("interaction").hidden = false;
  $("questions").replaceChildren();
  $("approval-view").hidden = message.kind === "question";
  $("approve").hidden = message.kind === "question" || !interaction.approvable;
  $("decline").hidden = message.kind === "question";
  $("answer-question").hidden = message.kind !== "question";
  if (message.kind === "question") {
    $("interaction-title").textContent = "追加質問への回答";
    $("interaction-details").textContent = "回答を確認してから送信してください。";
    for (const question of details.questions || []) {
      const label = document.createElement("label");
      label.textContent = `${question.header || "質問"}: ${question.question}`;
      $("questions").append(label);
      let select = null;
      if (question.options?.length) {
        select = document.createElement("select");
        select.append(new Option("選択してください", ""));
        for (const option of question.options) {
          select.append(new Option(`${option.label} — ${option.description || ""}`, option.label));
        }
        if (question.isOther) select.append(new Option("その他（自由入力）", "__other__"));
        $("questions").append(select);
      }
      const textarea = document.createElement("textarea");
      textarea.rows = 3;
      textarea.maxLength = 20000;
      textarea.hidden = Boolean(select);
      textarea.setAttribute("aria-label", `${question.header || "質問"}への自由回答`);
      $("questions").append(textarea);
      if (select) select.addEventListener("change", () => { textarea.hidden = select.value !== "__other__"; });
      questionInputs.push({id: question.id, select, textarea});
    }
    $("status").textContent = "回答待ち";
  } else {
    $("interaction-title").textContent = "Codexが操作の承認を要求しています";
    $("interaction-details").textContent = "";
    $("operation-label").textContent = message.kind === "command" ? "実行コマンド" : "変更対象と差分";
    $("operation-text").textContent = message.kind === "command" ? details.command || "取得できません" :
      details.changes?.length ? details.changes.map(change => `${change.path} (${change.kind || "変更"})\n${change.diff || "差分なし"}`).join("\n\n") : "取得できません";
    $("operation-cwd").textContent = message.kind === "command"
      ? `作業場所: ${details.cwd || "取得できません"}`
      : `追加の書き込み先: ${details.grantRoot || "指定なし"}`;
    $("approval-reason").textContent = `実行理由: ${details.reason || "Codexから提供されていません"}`;
    $("approval-scope").textContent = "承認範囲: この種類の操作は承認不可";
    $("approval-warning").textContent = "Focodexでは汎用の操作承認を許可していません。拒否か停止を選んでください。";
    $("status").textContent = "承認待ち";
  }
}
function replyInteraction(payload) {
  if (!port || !interaction || stopping || responseRequestId) return;
  if (payload.decision === "accept" && !interaction.approvable) return;
  responseRequestId = crypto.randomUUID();
  try {
    port.postMessage({type: "respond", id: responseRequestId, targetId: pendingId,
      requestId: interaction.requestId, itemId: interaction.itemId, ...payload});
    $("status").textContent = "回答を送信中…";
  } catch (error) {
    responseRequestId = null;
    $("status").textContent = `回答送信エラー: ${error.message}`;
  }
  controls();
}
function savedModel() {
  try { return localStorage.getItem(`focodex:model:${$("project").value}`); }
  catch { return null; }
}
function clearModels() {
  $("model").replaceChildren(new Option("モデルを選択してください", ""));
}
function gitStatusLabel(code) {
  if (code === "??") return "未追跡";
  const labels = {M: "変更", A: "追加", D: "削除", R: "名前変更", C: "コピー", U: "競合"};
  return [...new Set([...code].filter(char => char !== " ").map(char => labels[char] || char))].join("・") || "変更";
}
function showGitFiles(files) {
  $("git-files").replaceChildren();
  if (!files.length) {
    $("git-files").textContent = "変更なし";
    return;
  }
  $("git-files").textContent = "";
  for (const file of files) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `${file.path} — ${gitStatusLabel(file.status)}` +
      (file.originalPath ? `（元: ${file.originalPath}）` : "");
    details.append(summary);
    for (const [title, diff] of [["未ステージ・未追跡", file.unstagedDiff],
                                  ["ステージ済み", file.stagedDiff]]) {
      if (!diff) continue;
      const heading = document.createElement("h3");
      heading.textContent = title;
      const pre = document.createElement("pre");
      pre.textContent = diff;
      details.append(heading, pre);
    }
    if (!file.unstagedDiff && !file.stagedDiff) {
      const note = document.createElement("p");
      note.textContent = "表示できるテキスト差分はありません。";
      details.append(note);
    }
    $("git-files").append(details);
  }
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
function fetchGitSnapshot() {
  if (!port || busy || !$("project").value) return;
  pendingId = crypto.randomUUID();
  pendingKind = "git_snapshot";
  busy = true;
  $("git-message").textContent = "Git状態を取得中…";
  $("git-files").replaceChildren();
  try {
    port.postMessage({type: "git_snapshot", id: pendingId, projectId: $("project").value});
  } catch (error) {
    busy = false;
    pendingId = null;
    pendingKind = null;
    $("git-message").textContent = `Git取得エラー: ${error.message}`;
  }
  controls();
}
$("project").addEventListener("change", () => {
  $("git-message").textContent = "";
  $("git-files").replaceChildren();
  fetchModels();
});
$("refresh-models").addEventListener("click", fetchModels);
$("git-refresh").addEventListener("click", fetchGitSnapshot);
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
  $("git-refresh").disabled = busy || !port || !$("project").value;
  $("run").disabled = $("project").disabled || $("model").disabled || !$("model").value || !$("prompt").value.trim();
  $("stop").disabled = !port || !busy || pendingKind !== "run" || stopping;
  $("prompt").disabled = busy;
  const cannotReply = !interaction || stopping || Boolean(responseRequestId);
  $("approve").disabled = cannotReply || !interaction?.approvable;
  $("decline").disabled = cannotReply;
  $("answer-question").disabled = cannotReply;
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
    stopping = false;
    stopRequestId = null;
    clearInteraction();
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
    } else if (message.type === "git_snapshot" && message.id === pendingId && pendingKind === "git_snapshot") {
      busy = false;
      pendingId = null;
      pendingKind = null;
      showGitFiles(Array.isArray(message.files) ? message.files : []);
      $("git-message").textContent = "取得完了";
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
    } else if (message.type === "stopping" && message.id === pendingId) {
      stopping = true;
      $("status").textContent = "停止要求中…";
    } else if (message.type === "interaction" && message.id === pendingId && !stopping) {
      showInteraction(message);
    } else if (message.type === "answered" && message.id === pendingId && message.requestId === interaction?.requestId) {
      clearInteraction();
      $("status").textContent = "Codex実行中…";
    } else if (message.type === "interaction_resolved" && message.id === pendingId &&
               message.requestId === interaction?.requestId && message.itemId === interaction?.itemId) {
      clearInteraction();
      $("status").textContent = "承認要求は失効しました。Codex実行中…";
    } else if (message.type === "result" && message.id === pendingId) {
      const wasStopping = stopping;
      const diagnostics = message.stopDiagnostics;
      busy = false;
      pendingId = null;
      pendingKind = null;
      stopping = false;
      stopRequestId = null;
      clearInteraction();
      const labels = {completed: "完了", failed: "失敗", interrupted: "中断", timeout: "時間切れ", unsupported_request: "承認・質問は未対応"};
      $("status").textContent = wasStopping && message.status === "interrupted" && diagnostics?.process?.processGroupGone ? "停止済み" : labels[message.status] ?? `未知の状態: ${message.status}`;
      $("answer").textContent = message.text || "（応答本文なし）";
      $("info").textContent = JSON.stringify(message, null, 2);
      if (diagnostics?.requested) {
        const acknowledged = diagnostics.interruptAcknowledged ? "受信" : "未受信";
        const fallback = diagnostics.fallbackUsed ? "実施" : "なし";
        const group = diagnostics.process?.processGroupGone ? "消滅確認" : "確認不可";
        $("diagnostics").textContent = `内部状態: Codex終了=${diagnostics.turnStatus || "未受信"}、中断応答=${acknowledged}、5秒後の強制終了=${fallback}、専用プロセス群=${group}`;
      } else {
        $("diagnostics").textContent = "";
      }
      if (message.error) {
        $("status").textContent += `：${typeof message.error === "string" ? message.error : JSON.stringify(message.error)}`;
      }
    } else if (message.type === "error") {
      // Validation errors do not cancel a different in-flight request.
      if (message.id === pendingId && pendingKind === "git_snapshot") {
        $("git-message").textContent = `Git取得エラー: ${message.error}`;
      } else {
        $("status").textContent = `エラー: ${message.error}`;
      }
      if (message.id === stopRequestId) {
        stopping = false;
        stopRequestId = null;
      }
      if (message.id === responseRequestId) responseRequestId = null;
      if (message.id === pendingId || message.id == null) {
        busy = false;
        pendingId = null;
        pendingKind = null;
        stopping = false;
      }
    }
    controls();
  });
  projectsId = crypto.randomUUID();
  connection.postMessage({type: "projects", id: projectsId});
  controls();
});
$("approve").addEventListener("click", () => replyInteraction({decision: "accept", approvalScope: "once"}));
$("decline").addEventListener("click", () => replyInteraction({decision: "decline"}));
$("answer-question").addEventListener("click", () => {
  if (!interaction || interaction.kind !== "question") return;
  const answers = {};
  for (const {id, select, textarea} of questionInputs) {
    const answer = select && select.value !== "__other__" ? select.value : textarea.value.trim();
    if (!answer) {
      $("status").textContent = "全ての質問に回答してください。";
      return;
    }
    answers[id] = answer;
  }
  replyInteraction({answers});
});
$("stop").addEventListener("click", () => {
  if (!port || !busy || pendingKind !== "run" || stopping) return;
  stopping = true;
  stopRequestId = crypto.randomUUID();
  $("status").textContent = "停止要求中…";
  try {
    port.postMessage({type: "stop", id: stopRequestId, targetId: pendingId});
  } catch (error) {
    stopping = false;
    stopRequestId = null;
    $("status").textContent = `停止要求エラー: ${error.message}`;
  }
  controls();
});
$("form").addEventListener("submit", event => {
  event.preventDefault();
  if (busy || !port || !$("project").value || !$("model").value || !$("prompt").value.trim()) return;
  pendingId = crypto.randomUUID();
  pendingKind = "run";
  stopping = false;
  stopRequestId = null;
  clearInteraction();
  busy = true;
  $("answer").textContent = "";
  $("info").textContent = "";
  $("diagnostics").textContent = "";
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
