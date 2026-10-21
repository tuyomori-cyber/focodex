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
let workspaces = new Map();
let standaloneProjects = [];
let managerRequestId = null;
let repositoryRows = new Map();
let configuredDiscoveryRoots = new Set();
let scanAfterRootAdd = null;
let managerOperation = null;
let editingWorkspaceId = null;
function currentWorkspace() { return workspaces.get($("workspace").value); }
function currentProject() { return $("project").value; }
function showWorkspaceProjects() {
  const workspace = currentWorkspace();
  const standalone = $("workspace").value === "__standalone__";
  $("workspace-choice").hidden = false;
  $("project").replaceChildren();
  $("git-message").textContent = "";
  $("git-files").replaceChildren();
  if (standalone) {
    for (const project of standaloneProjects) $("project").append(new Option(`${project.name} (${project.path})`, project.id));
    $("workspace-members").textContent = "許可対象: 開始プロジェクトとして選んだリポジトリ1件";
    editingWorkspaceId = null;
    $("editing-workspace").textContent = "単独リポジトリは管理対象のワークスペースではありません。";
    return;
  }
  if (!workspace) {
    $("workspace-members").textContent = "";
    return;
  }
  for (const project of workspace.projects) {
    $("project").append(new Option(`${project.name} (${project.path})`, project.id));
  }
  if (workspace.projects.some(project => project.id === workspace.defaultProjectId)) {
    $("project").value = workspace.defaultProjectId;
  }
  $("workspace-members").textContent = `許可対象: ${workspace.projects.map(project => `${project.name}（${project.path}）`).join("、")}`;
  showManagerWorkspace();
}
function selectedRepositoryPaths() {
  return [...repositoryRows.values()].filter(row => row.checked).map(row => row.path);
}
function refreshManagerDefaults() {
  const selected = selectedRepositoryPaths();
  $("workspace-name-field").hidden = selected.length <= 1;
  const current = $("manager-default-project").value;
  $("manager-default-project").replaceChildren();
  for (const path of selected) {
    const row = repositoryRows.get(path);
    $("manager-default-project").append(new Option(`${row?.name || path} (${path})`, path));
  }
  if (selected.includes(current)) $("manager-default-project").value = current;
}
function showRepositoryRows() {
  $("repository-results").replaceChildren();
  for (const row of [...repositoryRows.values()].sort((a, b) => a.path.localeCompare(b.path))) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = row.path;
    input.checked = Boolean(row.checked);
    input.addEventListener("change", () => { row.checked = input.checked; refreshManagerDefaults(); });
    label.append(input, `${row.name} — ${row.path}${row.kind === "worktree" ? "（worktree）" : ""}`);
    $("repository-results").append(label);
  }
  refreshManagerDefaults();
}
function showManagerWorkspace() {
  $("manager-workspace").replaceChildren(new Option("新規作成", "__new__"));
  for (const workspace of workspaces.values()) $("manager-workspace").append(new Option(workspace.name, workspace.id));
  const current = currentWorkspace();
  selectManagerWorkspace(editingWorkspaceId || current?.id || "__new__");
}
function selectManagerWorkspace(id) {
  $("manager-workspace").value = id;
  const workspace = workspaces.get(id);
  if (!workspace) {
    editingWorkspaceId = null;
    $("workspace-name").value = "";
    $("editing-workspace").textContent = "新規作成: 含めるリポジトリを選択してください。";
    for (const row of repositoryRows.values()) row.checked = false;
    showRepositoryRows();
    return;
  }
  editingWorkspaceId = workspace.id;
  $("workspace-name").value = workspace.name;
  $("editing-workspace").textContent = `編集中: ${workspace.name}`;
  const selected = new Set(workspace.projects.map(project => project.path));
  for (const project of workspace.projects) {
    repositoryRows.set(project.path, {...project, kind: "registered", checked: true});
  }
  for (const row of repositoryRows.values()) row.checked = selected.has(row.path);
  showRepositoryRows();
  const defaultProject = workspace.projects.find(project => project.id === workspace.defaultProjectId);
  if (defaultProject) $("manager-default-project").value = defaultProject.path;
}
function applyWorkspaceConfig(message) {
  workspaces = new Map((message.workspaces || []).map(workspace => [workspace.id, workspace]));
  standaloneProjects = message.standaloneProjects || [];
  const previous = $("workspace").value;
  $("workspace").replaceChildren();
  for (const workspace of workspaces.values()) $("workspace").append(new Option(workspace.name, workspace.id));
  $("workspace").append(new Option("単独リポジトリ", "__standalone__"));
  if (workspaces.has(message.selectedWorkspaceId)) $("workspace").value = message.selectedWorkspaceId;
  else if (workspaces.has(previous)) $("workspace").value = previous;
  editingWorkspaceId = currentWorkspace()?.id || null;
  $("discovery-root").replaceChildren();
  configuredDiscoveryRoots = new Set(message.discoveryRoots || []);
  for (const root of configuredDiscoveryRoots) $("discovery-root").append(new Option(root, root));
  for (const root of message.discoveryRootCandidates || []) {
    if (!configuredDiscoveryRoots.has(root)) $("discovery-root").append(new Option(`${root}（候補）`, root));
  }
  if (!$("new-discovery-root").value && message.discoveryRootCandidates?.length) {
    $("new-discovery-root").value = message.discoveryRootCandidates[0];
  }
  showWorkspaceProjects();
}
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
  try { return localStorage.getItem(`focodex:model:${$("workspace").value}:${currentProject()}`); }
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
    port.postMessage({type: "models", id: pendingId, workspaceId: $("workspace").value, projectId: currentProject()});
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
function managerRequest(type, payload = {}) {
  if (!port || busy || managerRequestId) return;
  managerRequestId = crypto.randomUUID();
  managerOperation = type;
  try {
    port.postMessage({type, id: managerRequestId, ...payload});
  } catch (error) {
    managerRequestId = null;
    $("manager-message").textContent = `管理操作エラー: ${error.message}`;
  }
  controls();
}
$("project").addEventListener("change", () => {
  $("git-message").textContent = "";
  $("git-files").replaceChildren();
  fetchModels();
});
$("workspace").addEventListener("change", () => {
  showWorkspaceProjects();
  fetchModels();
  controls();
});
$("manage-workspaces").addEventListener("click", () => {
  $("workspace-manager").hidden = !$("workspace-manager").hidden;
  if (!$("workspace-manager").hidden) showManagerWorkspace();
});
$("help").addEventListener("click", () => {
  $("help-panel").hidden = !$("help-panel").hidden;
  $("help").setAttribute("aria-expanded", String(!$("help-panel").hidden));
});
$("manager-workspace").addEventListener("change", () => {
  selectManagerWorkspace($("manager-workspace").value);
  controls();
});
$("add-discovery-root").addEventListener("click", () => {
  const path = $("new-discovery-root").value.trim();
  if (!path) return;
  $("manager-message").textContent = "検出ルートを保存中…";
  managerRequest("discovery_root_add", {path});
});
$("scan-repositories").addEventListener("click", () => {
  const root = $("discovery-root").value;
  if (!root) return;
  if (configuredDiscoveryRoots.has(root)) {
    $("manager-message").textContent = "リポジトリを検出中…";
    managerRequest("repository_scan", {root});
  } else {
    scanAfterRootAdd = root;
    $("manager-message").textContent = "検出ルートを追加してからリポジトリを検出します…";
    managerRequest("discovery_root_add", {path: root});
  }
});
$("save-workspace").addEventListener("click", () => {
  const projectPaths = selectedRepositoryPaths();
  const defaultProjectPath = $("manager-default-project").value;
  const name = projectPaths.length === 1 ? repositoryRows.get(projectPaths[0])?.name : $("workspace-name").value.trim();
  if (!name || !projectPaths.length || !defaultProjectPath) {
    $("manager-message").textContent = "リポジトリと開始プロジェクトを指定してください。複数選択時は名前も指定してください。";
    return;
  }
  $("manager-message").textContent = "ワークスペースを保存中…";
  managerRequest("workspace_save", {workspaceId: editingWorkspaceId, name, projectPaths, defaultProjectPath});
});
$("delete-workspace").addEventListener("click", () => {
  const workspaceId = editingWorkspaceId;
  if (!workspaceId) return;
  $("manager-message").textContent = "ワークスペースを設定から削除中…";
  managerRequest("workspace_delete", {workspaceId});
});
$("refresh-models").addEventListener("click", fetchModels);
$("git-refresh").addEventListener("click", fetchGitSnapshot);
$("model").addEventListener("change", () => {
  try { localStorage.setItem(`focodex:model:${$("workspace").value}:${currentProject()}`, $("model").value); }
  catch { /* Selection still works when local storage is unavailable. */ }
  controls();
});
function controls() {
  $("connect").disabled = Boolean(port);
  $("manage-workspaces").disabled = busy || !port || Boolean(managerRequestId);
  $("workspace").disabled = busy || !port || !$("workspace").options.length;
  $("project").disabled = busy || !port || !$("project").options.length;
  $("model").disabled = busy || !port || $("model").options.length < 2;
  $("refresh-models").disabled = $("project").disabled;
  $("git-refresh").disabled = busy || !port || !$("project").value || $("workspace").value === "__standalone__";
  $("run").disabled = $("project").disabled || $("model").disabled || !$("model").value || !$("prompt").value.trim();
  $("stop").disabled = !port || !busy || pendingKind !== "run" || stopping;
  $("prompt").disabled = busy;
  const cannotReply = !interaction || stopping || Boolean(responseRequestId);
  $("approve").disabled = cannotReply || !interaction?.approvable;
  $("decline").disabled = cannotReply;
  $("answer-question").disabled = cannotReply;
  // A detection root only updates Focodex's local configuration.  Keep that
  // action available while a model-list request is in progress, so connecting
  // to a slow Codex CLI never traps the user in a disabled manager UI.
  $("add-discovery-root").disabled = !port || Boolean(managerRequestId);
  for (const id of ["scan-repositories", "save-workspace"]) {
    $(id).disabled = busy || !port || Boolean(managerRequestId);
  }
  $("manager-workspace").disabled = busy || !port || Boolean(managerRequestId);
  $("delete-workspace").disabled = busy || !port || Boolean(managerRequestId) || !editingWorkspaceId;
}
$("prompt").addEventListener("input", controls);
$("connect").addEventListener("click", () => {
  if (port) return;
  const connection = browser.runtime.connectNative("focodex");
  port = connection;
  workspaces = new Map();
  $("workspace").replaceChildren();
  $("project").replaceChildren();
  $("workspace-members").textContent = "";
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
      applyWorkspaceConfig(message);
      $("status").textContent = "接続済み・待機。モデルを選択する場合は一覧を取得してください。";
    } else if (message.type === "workspace_config" && message.id === managerRequestId) {
      const completedOperation = managerOperation;
      managerRequestId = null;
      managerOperation = null;
      applyWorkspaceConfig(message);
      const root = scanAfterRootAdd;
      scanAfterRootAdd = null;
      $("new-discovery-root").value = "";
      if (root && configuredDiscoveryRoots.has(root)) {
        $("manager-message").textContent = "リポジトリを検出中…";
        managerRequest("repository_scan", {root});
      } else {
        const messages = {
          discovery_root_add: "検出ルートを保存しました。",
          workspace_save: "ワークスペースを保存しました。",
          workspace_delete: "ワークスペースを設定から削除しました。",
          workspace_config: "設定を読み込みました。"
        };
        $("manager-message").textContent = messages[completedOperation] || "設定を更新しました。";
      }
    } else if (message.type === "repository_scan" && message.id === managerRequestId) {
      managerRequestId = null;
      managerOperation = null;
      for (const row of message.repositories || []) {
        const existing = repositoryRows.get(row.path);
        repositoryRows.set(row.path, {...row, checked: existing?.checked || false});
      }
      showRepositoryRows();
      $("manager-message").textContent = `${(message.repositories || []).length}件のリポジトリを検出しました。`;
    } else if (message.type === "git_snapshot" && message.id === pendingId && pendingKind === "git_snapshot") {
      busy = false;
      pendingId = null;
      pendingKind = null;
      showGitFiles(Array.isArray(message.files) ? message.files : []);
      $("diff-panel").open = true;
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
      $("response-panel").open = true;
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
      if (message.id === managerRequestId) {
        managerRequestId = null;
        managerOperation = null;
        $("manager-message").textContent = `管理操作エラー: ${message.error}`;
      }
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
    port.postMessage({type: "run", id: pendingId, workspaceId: $("workspace").value,
      projectId: currentProject(), model: $("model").value, text: $("prompt").value});
  } catch (error) {
    busy = false;
    pendingId = null;
    pendingKind = null;
    $("status").textContent = `送信エラー: ${error.message}`;
  }
  controls();
});
