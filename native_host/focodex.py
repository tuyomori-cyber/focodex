#!/usr/bin/env python3
"""Linux-only, one-turn Codex Native Messaging proof of concept."""
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import secrets
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid

import diagnostics
import git_ops
import git_remote

CONFIG = Path(os.environ.get('FOCODEX_CONFIG', Path.home() / '.config/focodex/config.json'))
MAX_NATIVE = 128 * 1024
MAX_RPC = 8 * 1024 * 1024
MAX_TEXT = 160_000  # Keeps the UTF-8 native result below Firefox's 1 MiB limit.
TIMEOUT = 180
STOP_TIMEOUT = 5
PERMISSION_PROFILE = 'focodex-project'
GIT = '/usr/bin/git'
MAX_GIT_OUTPUT = 250_000
MAX_UNTRACKED_FILE = 100_000
MAX_DISCOVERY_DEPTH = 3
MAX_DISCOVERED_REPOSITORIES = 100
SKIP_DISCOVERY_NAMES = {'.git', '.cache', 'node_modules', '.venv', 'venv', 'target', '__pycache__'}


def git_read(project, *arguments):
    return git_ops.text(project, *arguments)


def project_file_preview(project, name):
    parts = name.split('/')
    if not parts or any(part in ('', '.', '..') for part in parts) or '\ufffd' in name:
        return 'ファイル名を安全に扱えないため内容を省略しました。\n'
    descriptor = os.open(project, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            next_descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                                  dir_fd=descriptor)
        try:
            if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
                return '通常ファイル以外のため内容を省略しました。\n'
            chunks = []
            remaining = MAX_UNTRACKED_FILE + 1
            while remaining:
                chunk = os.read(file_descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b''.join(chunks)
        finally:
            os.close(file_descriptor)
    except OSError:
        return 'ファイルを安全に読み取れないため内容を省略しました。\n'
    finally:
        os.close(descriptor)
    if len(data) > MAX_UNTRACKED_FILE:
        return 'ファイルが大きいため内容を省略しました。\n'
    if b'\0' in data:
        return 'バイナリファイルのため内容を省略しました。\n'
    try:
        contents = data.decode('utf-8')
    except UnicodeDecodeError:
        return 'UTF-8テキストではないため内容を省略しました。\n'
    if not contents:
        return '空ファイル\n'
    lines = list(difflib.unified_diff([], contents.splitlines(keepends=True),
                                      fromfile='/dev/null', tofile='b/' + name, lineterm='\n'))
    if not contents.endswith('\n'):
        lines.append('\n\\ No newline at end of file\n')
    return ''.join(lines)


def git_changed_files(project):
    records = git_read(project, 'status', '--porcelain=v1', '-z', '--untracked-files=all').split('\0')
    files = []
    index = 0
    while index < len(records) and records[index]:
        record = records[index]
        if len(record) < 4 or record[2] != ' ':
            raise ValueError('Git状態の形式を確認できません。')
        code, name = record[:2], record[3:]
        original = None
        if 'R' in code or 'C' in code:
            index += 1
            if index >= len(records) or not records[index]:
                raise ValueError('Gitの移動元を確認できません。')
            original = records[index]
        names = (name, original) if original else (name,)
        if any(not path or path.startswith('/') or '\ufffd' in path or
               any(part in ('', '.', '..') for part in path.split('/')) for path in names):
            raise ValueError('Gitの変更ファイル名を確認できません。')
        files.append((code, name, original))
        if len(files) > 100:
            raise ValueError('変更ファイルが100件を超えたため表示できません。')
        index += 1
    return files


def git_snapshot(project):
    git_ops.Repository(project)
    rows = []
    used = 0
    for code, name, original in git_changed_files(project):
        paths = [':(literal)' + name] + ([':(literal)' + original] if original else [])
        if code == '??':
            unstaged = project_file_preview(project, name)
            staged = ''
        else:
            unstaged = git_read(project, 'diff', '--no-ext-diff', '--no-textconv',
                                '--no-color', '--', *paths)
            staged = git_read(project, 'diff', '--cached', '--no-ext-diff',
                              '--no-textconv', '--no-color', '--', *paths)
        row = {'path': name, 'status': code, 'originalPath': original,
               'unstagedDiff': unstaged, 'stagedDiff': staged}
        used += len(json.dumps(row, ensure_ascii=False).encode('utf-8'))
        if used > MAX_GIT_OUTPUT:
            raise ValueError('Git差分の合計が表示上限を超えました。')
        rows.append(row)
    return {'files': rows}


def git_remote_preflight(project):
    """Return only non-secret prerequisites for a future remote Git operation."""
    repo = git_ops.Repository(project)
    ref, _ = repo.branch()
    remote, merge, _target = git_remote.upstream_ssh_remote(repo)
    upstream = remote + '/' + merge.removeprefix('refs/heads/')
    try:
        git_remote.dedicated_agent_socket()
        agent_available = True
    except ValueError:
        agent_available = False
    return {'branch': ref, 'upstream': upstream, 'sshAgentAvailable': agent_available,
            'remoteAgentStatus': git_remote.dedicated_agent_status(),
            'bwrapAvailable': Path('/usr/bin/bwrap').is_file(),
            'gitAvailable': Path(git_ops.GIT).is_file(), 'sshAvailable': Path('/usr/bin/ssh').is_file()}


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, dict):
        return '{' + ', '.join(f'{json.dumps(key)} = {toml_value(item)}'
                               for key, item in value.items()) + '}'
    raise TypeError('Unsupported permission profile value')


def codex_args(executable, workspace_roots):
    binary = str(Path(executable).resolve(strict=True))
    profile = {'workspace_roots': {path: True for path in workspace_roots},
               'filesystem': {':root': 'deny', ':minimal': 'read',
                              ':workspace_roots': {'.': 'write'},
                              ':tmpdir': 'deny', ':slash_tmp': 'deny',
                              binary: 'read'},
               'network': {'enabled': False}}
    return [executable, '-c', f'default_permissions={json.dumps(PERMISSION_PROFILE)}',
            '-c', f'permissions.{PERMISSION_PROFILE}={toml_value(profile)}',
            'app-server', '--listen', 'stdio://']


def send(message):
    data = json.dumps(message, ensure_ascii=False).encode('utf-8')
    sys.stdout.buffer.write(struct.pack('=I', len(data)) + data)
    sys.stdout.buffer.flush()


def read_config():
    return json.loads(CONFIG.read_text())


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        sync_directory(path.parent)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_config(data):
    atomic_json(CONFIG, data)


def boot_id():
    # Linux boot identity, not PID liveness: an orphan may outlive its leader.
    return str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))


def execution_path():
    return CONFIG.with_suffix('.execution.json')


def execution_state():
    path = execution_path()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (ValueError, OSError) as error:
        raise ValueError('実行記録を確認できません。Codex・Git操作を禁止します。') from error
    if (not isinstance(data, dict) or data.get('version') != 1 or
            not isinstance(data.get('token'), str) or not data['token'] or
            not isinstance(data.get('bootId'), str)):
        raise ValueError('実行記録が不正です。Codex・Git操作を禁止します。')
    try:
        data['bootId'] = str(uuid.UUID(data['bootId']))
    except ValueError as error:
        raise ValueError('実行記録の起動IDが不正です。Codex・Git操作を禁止します。') from error
    return data


def remove_execution_state():
    execution_path().unlink()
    sync_directory(execution_path().parent)


def require_confirmed_idle():
    """Call only while holding the shared lock, including future Git writes."""
    state = execution_state()
    if state is None:
        return
    if state['bootId'] != boot_id():
        # A reboot proves that the recorded invocation and its descendants died.
        remove_execution_state()
        return
    raise ValueError('前回のCodex終了が未確認です。Codex・Git操作と設定変更を禁止します。'
                     'OS再起動後に再接続してください。実行記録は削除しないでください。')


def config(data=None):
    data = read_config() if data is None else data
    executable = data['codex']
    if not isinstance(executable, str) or not Path(executable).is_absolute() or not os.access(executable, os.X_OK):
        raise ValueError('設定のcodexには実行可能ファイルの絶対パスが必要です。')
    projects = data['projects']
    if not isinstance(projects, dict):
        raise ValueError('projectsにプロジェクトを登録してください。')
    resolved = {}
    for key, value in projects.items():
        if not key or not isinstance(value, dict) or not isinstance(value.get('path'), str):
            raise ValueError('プロジェクト設定が不正です。')
        path = Path(value['path'])
        if not path.is_absolute() or not path.is_dir():
            raise ValueError(f'プロジェクトのディレクトリが見つかりません: {key}')
        resolved[key] = str(path.resolve(strict=True))
    raw_workspaces = data.get('workspaces')
    if raw_workspaces is None:
        if not projects:
            raise ValueError('projectsにプロジェクトを登録してください。')
        workspaces = {key: {'name': item.get('name', key), 'projectIds': [key],
                            'defaultProjectId': key} for key, item in projects.items()}
    else:
        if not isinstance(raw_workspaces, dict):
            raise ValueError('workspacesの設定が不正です。')
        workspaces = raw_workspaces
    normalized = {}
    for key, value in workspaces.items():
        if (not isinstance(key, str) or not key or not isinstance(value, dict) or
                not isinstance(value.get('projectIds'), list) or not value['projectIds']):
            raise ValueError('ワークスペース設定が不正です。')
        members = value['projectIds']
        if (any(not isinstance(member, str) or member not in projects for member in members) or
                len(set(members)) != len(members)):
            raise ValueError(f'ワークスペースのプロジェクトIDが不正です: {key}')
        default = value.get('defaultProjectId', members[0])
        if default not in members:
            raise ValueError(f'ワークスペースの開始プロジェクトが不正です: {key}')
        paths = [resolved[member] for member in members]
        for index, path in enumerate(paths):
            for other in paths[index + 1:]:
                if path == other or Path(path).is_relative_to(other) or Path(other).is_relative_to(path):
                    raise ValueError(f'ワークスペース内のプロジェクト境界が重複しています: {key}')
        normalized[key] = {'name': value.get('name', key), 'projectIds': members,
                           'defaultProjectId': default, 'paths': paths}
    return executable, projects, normalized


def workspace_for(projects, workspaces, workspace_id, project_id):
    # Old extension messages did not contain workspaceId.  They retain only the
    # old single-project boundary, never an inferred multi-project boundary.
    if workspace_id is None and project_id in workspaces and workspaces[project_id]['projectIds'] == [project_id]:
        workspace_id = project_id
    if not isinstance(workspace_id, str) or workspace_id not in workspaces:
        raise ValueError('未登録のワークスペースです。')
    workspace = workspaces[workspace_id]
    if not isinstance(project_id, str) or project_id not in workspace['projectIds']:
        raise ValueError('ワークスペースに属さないプロジェクトです。')
    return workspace_id, workspace, str(Path(projects[project_id]['path']).resolve(strict=True))


def discovery_root(path):
    if not isinstance(path, str) or not path:
        raise ValueError('検出ルートには絶対パスが必要です。')
    candidate = Path(path)
    if not candidate.is_absolute() or not candidate.is_dir() or candidate.is_symlink():
        raise ValueError('検出ルートのディレクトリを確認できません。')
    resolved = candidate.resolve(strict=True)
    home = Path.home().resolve()
    if resolved in (Path('/'), home):
        raise ValueError('ルートディレクトリまたはホーム全体は検出ルートにできません。')
    return str(resolved)


def configured_discovery_roots(data, projects):
    raw = data.get('discoveryRoots', [])
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(path, str) for path in raw):
        raise ValueError('discoveryRootsの設定が不正です。')
    roots = []
    for path in raw:
        root = discovery_root(path)
        if root not in roots:
            roots.append(root)
    candidates = []
    for project in projects.values():
        parent = str(Path(project['path']).resolve(strict=True).parent)
        try:
            parent = discovery_root(parent)
        except ValueError:
            continue
        if parent not in roots and parent not in candidates:
            candidates.append(parent)
    return roots, candidates


def repository_kind(directory):
    marker = directory / '.git'
    if marker.is_dir() and not marker.is_symlink():
        return 'repository'
    if marker.is_file() and not marker.is_symlink():
        return 'worktree'
    return None


def scan_repositories(root):
    root = Path(discovery_root(root))
    found = []
    pending = [(root, 0)]
    seen = set()
    while pending:
        directory, depth = pending.pop()
        resolved = directory.resolve(strict=True)
        if resolved in seen:
            continue
        seen.add(resolved)
        kind = repository_kind(resolved)
        if kind:
            found.append({'path': str(resolved), 'name': resolved.name, 'kind': kind})
            if len(found) > MAX_DISCOVERED_REPOSITORIES:
                raise ValueError('検出したリポジトリが上限を超えました。検出ルートを絞ってください。')
            continue
        if depth >= MAX_DISCOVERY_DEPTH:
            continue
        try:
            entries = list(os.scandir(resolved))
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith('.') or entry.name in SKIP_DISCOVERY_NAMES:
                continue
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            pending.append((Path(entry.path), depth + 1))
    return sorted(found, key=lambda row: row['path'])


def project_id_for(data, path):
    projects = data.setdefault('projects', {})
    for key, project in projects.items():
        if str(Path(project['path']).resolve(strict=True)) == path:
            return key
    stem = re.sub(r'[^a-z0-9_-]+', '-', Path(path).name.lower()).strip('-') or 'project'
    key = stem
    index = 2
    while key in projects:
        key = f'{stem}-{index}'
        index += 1
    projects[key] = {'name': Path(path).name, 'path': path}
    return key


def workspace_id_for(data):
    workspaces = data.setdefault('workspaces', {})
    while True:
        key = f'ws_{uuid.uuid4().hex}'
        if key not in workspaces:
            return key


def standalone_projects(data, projects):
    roots, _ = configured_discovery_roots(data, projects)
    rows = {row['path']: row for root in roots for row in scan_repositories(root)}
    return [{'id': 'repo_' + hashlib.sha256(path.encode()).hexdigest(), **row}
            for path, row in sorted(rows.items())]


def selected_workspace(data, projects, workspaces, workspace_id, project_id):
    if workspace_id != '__standalone__':
        return workspace_for(projects, workspaces, workspace_id, project_id)
    rows = {row['id']: row for row in standalone_projects(data, projects)}
    row = rows.get(project_id)
    if row is None:
        raise ValueError('検出済みリポジトリを選択してください。')
    return workspace_id, {'paths': [row['path']]}, row['path']


class Host:
    def __init__(self):
        self.selector = selectors.DefaultSelector()
        self.selector.register(sys.stdin.buffer, selectors.EVENT_READ, 'native')
        self.native_buffer = bytearray()
        self.rpc_buffer = bytearray()
        self.process = None
        self.lock = None
        self.execution_token = None
        self.git_snapshots = {}
        self.git_reviews = {}
        self.request_id = None
        self.thread_id = None
        self.turn_id = None
        self.messages = {}
        self.started = 0
        self.prompt = ''
        self.cwd = ''
        self.workspace_id = None
        self.workspace_roots = []
        self.agent_version = None
        self.settings = {}
        self.operation = 'run'
        self.model = None
        self.catalogs = {}
        self.model_rows = []
        self.cursors = set()
        self.stop_requested = False
        self.stop_started = 0
        self.interrupt_sent = False
        self.interrupt_acknowledged = False
        self.stop_fallback_used = False
        self.cleanup_uncertain = False
        self.codex_turn_status = None
        self.pending_request = None
        self.items = {}
        self.trace = None
        self.model_page = 0

    def trace_start(self, operation, request_id):
        self.trace = {'id': diagnostics.new_trace_id(), 'operation': operation,
                      'requestId': request_id, 'started': time.monotonic(), 'terminal': False,
                      'available': True}
        self.trace_event('received')

    @staticmethod
    def trace_rejection(operation, error_code):
        trace_id = diagnostics.new_trace_id()
        available = diagnostics.write_operation_diagnostic({'traceId': trace_id, 'operation': operation,
                                                            'stage': 'received', 'elapsedMs': 0})
        available = diagnostics.write_operation_diagnostic({'traceId': trace_id, 'operation': operation,
                                                            'stage': 'terminal', 'elapsedMs': 0,
                                                            'status': 'rejected', 'errorCode': error_code}) and available
        return {'traceId': trace_id, 'diagnosticsUnavailable': not available}

    def trace_event(self, stage, **fields):
        if not self.trace:
            return
        event = {'traceId': self.trace['id'], 'operation': self.trace['operation'], 'stage': stage,
                 'elapsedMs': round((time.monotonic() - self.trace['started']) * 1000)}
        event.update(fields)
        if not diagnostics.write_operation_diagnostic(event):
            self.trace['available'] = False

    def trace_terminal(self, status, error_code=None, process_state=None):
        if not self.trace or self.trace['terminal']:
            return
        fields = {'status': status}
        if error_code:
            fields['errorCode'] = error_code
        if process_state is not None:
            fields['processGroupGone'] = bool(process_state.get('processGroupGone'))
        self.trace_event('terminal', **fields)
        self.trace['terminal'] = True

    def trace_details(self):
        if not self.trace:
            return {}
        return {'traceId': self.trace['id'], 'diagnosticsUnavailable': not self.trace['available']}

    def clear_trace(self):
        self.trace = None

    @staticmethod
    def error_code(error):
        text = str(error)
        if '前回のCodex終了が未確認' in text:
            return 'execution-unconfirmed'
        if '別のFocodexタブで実行中' in text:
            return 'locked'
        if '設定' in text:
            return 'configuration'
        if 'モデル一覧' in text:
            return 'model-catalog'
        return 'validation'

    def rpc(self, method, params, rpc_id=None):
        fields = {'method': method}
        if rpc_id is not None:
            fields['rpcId'] = rpc_id
        if method == 'model/list':
            fields['page'] = self.model_page
        self.trace_event('rpc_sent', **fields)
        value = {'method': method, 'params': params}
        if rpc_id is not None:
            value['id'] = rpc_id
        self.process.stdin.write((json.dumps(value) + '\n').encode())
        self.process.stdin.flush()

    def cleanup(self):
        process_state = None
        try:
            if self.process:
                process = self.process
                process_state = {'leaderExitCode': None, 'processGroupGone': False,
                                 'terminationSignal': None}
                try:
                    self.selector.unregister(process.stdout)
                except KeyError:
                    pass
                # Kill only the dedicated process group created for this invocation.
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, sig)
                        process_state['terminationSignal'] = signal.Signals(sig).name
                    except ProcessLookupError:
                        break
                    if sig == signal.SIGTERM:
                        deadline = time.monotonic() + 1
                        while time.monotonic() < deadline:
                            process.poll()  # Reap the leader before checking its group.
                            try:
                                os.killpg(process.pid, 0)
                            except ProcessLookupError:
                                break
                            time.sleep(0.02)
                process_state['leaderExitCode'] = process.wait(timeout=3)
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    process_state['processGroupGone'] = True
                if not process_state['processGroupGone']:
                    self.cleanup_uncertain = True
                process.stdin.close()
                process.stdout.close()
                self.process = None
            if self.execution_token:
                if process_state and process_state['processGroupGone']:
                    state = execution_state()
                    if state is None or state['token'] != self.execution_token:
                        raise ValueError('実行記録が一致しません。終了状態を確定できません。')
                    remove_execution_state()
                    self.execution_token = None
                else:
                    self.cleanup_uncertain = True
        except BaseException:
            self.cleanup_uncertain = True
            raise
        finally:
            # OS locks disappear on Host death. The durable record does not.
            if self.lock:
                self.lock.close()
                self.lock = None
            self.rpc_buffer.clear()
        return process_state

    def finish(self, status, error=None):
        stop = {'requested': self.stop_requested, 'interruptSent': self.interrupt_sent,
                'interruptAcknowledged': self.interrupt_acknowledged,
                'fallbackUsed': self.stop_fallback_used,
                'turnStatus': self.codex_turn_status}
        result = {'type': 'result', 'id': self.request_id, 'status': status,
                  'text': '\n\n'.join(self.messages.values()), 'error': error,
                  'threadId': self.thread_id, 'turnId': self.turn_id,
                  'agentVersion': self.agent_version, 'settings': self.settings}
        stop['process'] = self.cleanup()
        if self.operation == 'run':
            result['stopDiagnostics'] = stop
            if not stop['process']['processGroupGone']:
                result['status'] = 'failed'
                result['error'] = '専用プロセス群の終了を確認できません。再実行せず状態を確認してください。'
        self.trace_terminal(result['status'], 'process-unconfirmed' if not stop['process']['processGroupGone'] else None,
                            stop['process'])
        result.update(self.trace_details())
        self.request_id = None
        self.stop_requested = False
        self.interrupt_sent = False
        self.interrupt_acknowledged = False
        self.stop_fallback_used = False
        self.codex_turn_status = None
        self.pending_request = None
        self.items = {}
        send(result)
        self.clear_trace()

    def interrupt(self):
        if self.stop_requested and not self.interrupt_sent and self.thread_id and self.turn_id:
            self.rpc('turn/interrupt', {'threadId': self.thread_id, 'turnId': self.turn_id}, 5)
            self.interrupt_sent = True

    def workspace_config_message(self, request_id, data=None, selected_workspace_id=None):
        data = read_config() if data is None else data
        _, projects, workspaces = config(data)
        roots, candidates = configured_discovery_roots(data, projects)
        message = {'type': 'workspace_config', 'id': request_id,
              'discoveryRoots': roots, 'discoveryRootCandidates': candidates,
              'standaloneProjects': standalone_projects(data, projects),
              'workspaces': [
                  {'id': key, 'name': item['name'], 'defaultProjectId': item['defaultProjectId'],
                   'projects': [{'id': project_id, 'name': projects[project_id].get('name', project_id),
                                 'path': str(Path(projects[project_id]['path']).resolve(strict=True))}
                                for project_id in item['projectIds']]}
                  for key, item in workspaces.items()]}
        if selected_workspace_id is not None:
            message['selectedWorkspaceId'] = selected_workspace_id
        send(message)

    def locked_config(self):
        lock = CONFIG.with_suffix('.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise ValueError('別のFocodexタブで実行中です。')
        try:
            require_confirmed_idle()
        except BaseException:
            lock.close()
            raise
        return lock

    def command(self, value):
        if not isinstance(value, dict) or not isinstance(value.get('id'), str):
            raise ValueError('id付きのメッセージが必要です。')
        request_id = value['id']
        operation_kind = value.get('type')
        if operation_kind in ('run', 'models'):
            if self.trace:
                details = self.trace_rejection(operation_kind, 'busy')
                error = ValueError('実行中です。同時実行は1件です。')
                error.trace_details = details
                raise error
            self.trace_start(operation_kind, request_id)
        if value.get('type') == 'projects':
            data = read_config()
            _, projects, workspaces = config(data)
            roots, candidates = configured_discovery_roots(data, projects)
            send({'type': 'projects', 'id': request_id,
                  'discoveryRoots': roots, 'discoveryRootCandidates': candidates,
                  'standaloneProjects': standalone_projects(data, projects),
                  'workspaces': [
                {'id': key, 'name': item['name'], 'defaultProjectId': item['defaultProjectId'],
                 'projects': [{'id': project_id, 'name': projects[project_id].get('name', project_id),
                               'path': str(Path(projects[project_id]['path']).resolve(strict=True))}
                              for project_id in item['projectIds']]}
                for key, item in workspaces.items()]})
            return
        if value.get('type') == 'workspace_config':
            if self.process or self.cleanup_uncertain:
                raise ValueError('Codex実行中または終了未確認のため設定を変更できません。')
            self.workspace_config_message(request_id)
            return
        if value.get('type') == 'discovery_root_add':
            if self.process or self.cleanup_uncertain:
                raise ValueError('Codex実行中または終了未確認のため設定を変更できません。')
            root = discovery_root(value.get('path'))
            lock = self.locked_config()
            try:
                data = read_config()
                _, projects, current_workspaces = config(data)
                roots, _ = configured_discovery_roots(data, projects)
                if root not in roots:
                    data['discoveryRoots'] = roots + [root]
                    write_config(data)
                self.workspace_config_message(request_id, data)
            finally:
                lock.close()
            return
        if value.get('type') == 'repository_scan':
            if self.process or self.cleanup_uncertain:
                raise ValueError('Codex実行中または終了未確認のため検出できません。')
            data = read_config()
            _, projects, _ = config(data)
            roots, _ = configured_discovery_roots(data, projects)
            root = discovery_root(value.get('root'))
            if root not in roots:
                raise ValueError('登録済みの検出ルートだけを走査できます。')
            send({'type': 'repository_scan', 'id': request_id, 'root': root,
                  'repositories': scan_repositories(root)})
            return
        if value.get('type') == 'workspace_save':
            if self.process or self.cleanup_uncertain:
                raise ValueError('Codex実行中または終了未確認のため設定を変更できません。')
            workspace_id = value.get('workspaceId')
            name = value.get('name')
            paths = value.get('projectPaths')
            default_path = value.get('defaultProjectPath')
            if (workspace_id is not None and not isinstance(workspace_id, str) or
                    not isinstance(name, str) or not name.strip() or len(name) > 100 or
                    not isinstance(paths, list) or not paths or len(paths) > 20 or
                    len(set(paths)) != len(paths) or not isinstance(default_path, str)):
                raise ValueError('ワークスペースの保存内容が不正です。')
            lock = self.locked_config()
            try:
                data = read_config()
                _, projects, current_workspaces = config(data)
                roots, _ = configured_discovery_roots(data, projects)
                discovered = {row['path'] for root in roots for row in scan_repositories(root)}
                existing = {str(Path(item['path']).resolve(strict=True)) for item in projects.values()}
                resolved_paths = [str(Path(path).resolve(strict=True)) if isinstance(path, str) else '' for path in paths]
                if (any(not path or path not in discovered and path not in existing for path in resolved_paths) or
                        default_path not in paths):
                    raise ValueError('検出済みまたは既存登録済みのリポジトリだけを保存できます。')
                project_ids = [project_id_for(data, path) for path in resolved_paths]
                default_id = project_ids[paths.index(default_path)]
                if 'workspaces' not in data:
                    data['workspaces'] = {key: {'name': item['name'], 'projectIds': item['projectIds'],
                                                'defaultProjectId': item['defaultProjectId']}
                                          for key, item in current_workspaces.items()}
                if workspace_id is None:
                    workspace_id = workspace_id_for(data)
                elif workspace_id not in data['workspaces']:
                    raise ValueError('更新対象のワークスペースが見つかりません。')
                data['workspaces'][workspace_id] = {
                    'name': name.strip(), 'projectIds': project_ids, 'defaultProjectId': default_id}
                config(data)
                write_config(data)
                self.workspace_config_message(request_id, data, workspace_id)
            finally:
                lock.close()
            return
        if value.get('type') == 'workspace_delete':
            if self.process or self.cleanup_uncertain:
                raise ValueError('Codex実行中または終了未確認のため設定を変更できません。')
            workspace_id = value.get('workspaceId')
            lock = self.locked_config()
            try:
                data = read_config()
                _, _, workspaces = config(data)
                if not isinstance(workspace_id, str) or workspace_id not in workspaces:
                    raise ValueError('削除対象のワークスペースが見つかりません。')
                if 'workspaces' not in data:
                    data['workspaces'] = {key: {'name': item['name'], 'projectIds': item['projectIds'],
                                                'defaultProjectId': item['defaultProjectId']}
                                          for key, item in workspaces.items()}
                del data['workspaces'][workspace_id]
                used = {project_id for item in data['workspaces'].values()
                        for project_id in item['projectIds']}
                data['projects'] = {key: item for key, item in data['projects'].items() if key in used}
                config(data)
                write_config(data)
                self.workspace_config_message(request_id, data)
            finally:
                lock.close()
            return
        if value.get('type') == 'stop':
            if not self.process or self.operation != 'run' or value.get('targetId') != self.request_id:
                raise ValueError('停止対象の実行がありません。')
            if not self.stop_requested:
                self.stop_requested = True
                self.stop_started = time.monotonic()
                self.interrupt()
            send({'type': 'stopping', 'id': self.request_id})
            return
        if value.get('type') == 'respond':
            self.respond(value)
            return
        if value.get('type') in ('git_snapshot', 'git_stage', 'git_review', 'git_commit', 'git_remote_preflight', 'git_remote_sandbox_probe', 'git_remote_relay_probe', 'git_remote_ssh_probe', 'git_fetch', 'git_pull', 'git_push'):
            self.git_command(value)
            return
        if value.get('type') not in ('run', 'models'):
            raise ValueError('未対応の操作です。')
        if self.cleanup_uncertain:
            raise ValueError('前回の専用プロセス群の終了を確認できません。再実行できません。')
        if self.process:
            raise ValueError('実行中です。同時実行は1件です。')
        self.git_snapshots.clear()
        self.git_reviews.clear()
        data = read_config()
        executable, projects, workspaces = config(data)
        workspace_id = value.get('workspaceId')
        project_id = value.get('projectId')
        workspace_id, workspace, cwd = selected_workspace(data, projects, workspaces, workspace_id, project_id)
        operation = value['type']
        prompt = value.get('text', '')
        model = value.get('model')
        if operation == 'run':
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
                raise ValueError('指示は1〜20000文字で入力してください。')
            catalog_key = (workspace_id, cwd)
            if model is not None and (not isinstance(model, str) or model not in self.catalogs.get(catalog_key, set())):
                raise ValueError('モデル一覧を取得し、候補からモデルを選択してください。')
        if any(CONFIG.resolve().is_relative_to(root) or CONFIG.parent.resolve().is_relative_to(root) for root in workspace['paths']):
            raise ValueError('Host設定と実行記録はCodexの編集対象外に配置してください。')
        self.lock = self.locked_config()
        self.cwd = cwd
        self.workspace_id = workspace_id
        self.workspace_roots = workspace['paths']
        self.operation, self.model = operation, model
        self.model_rows = []
        self.cursors = set()
        self.model_page = 0
        if operation == 'models':
            self.catalogs.pop((workspace_id, cwd), None)
        self.request_id, self.prompt = request_id, prompt
        self.messages = {}
        self.thread_id = self.turn_id = self.agent_version = None
        self.settings = {}
        self.stop_requested = False
        self.interrupt_sent = False
        self.interrupt_acknowledged = False
        self.stop_fallback_used = False
        self.codex_turn_status = None
        self.pending_request = None
        self.items = {}
        self.started = time.monotonic()
        try:
            token = secrets.token_urlsafe(24)
            atomic_json(execution_path(), {'version': 1, 'token': token,
                        'bootId': boot_id(), 'operation': operation,
                        'workspaceRoots': self.workspace_roots, 'startedAt': time.time(),
                        'traceId': self.trace['id']})
            self.execution_token = token
            self.process = subprocess.Popen(
                codex_args(executable, self.workspace_roots), cwd=self.cwd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                start_new_session=True, bufsize=0)
            self.trace_event('codex_started')
            self.selector.register(self.process.stdout, selectors.EVENT_READ, 'codex')
            self.rpc('initialize', {'clientInfo': {'name': 'focodex_probe',
                     'title': 'Focodex connection probe', 'version': '0.1.1'},
                     'capabilities': {'experimentalApi': True}}, 1)
        except Exception:
            process_state = self.cleanup()
            self.trace_terminal('failed', 'launch', process_state)
            self.request_id = None
            raise
        send({'type': 'started', 'id': request_id, **self.trace_details()})

    def git_command(self, value):
        operation = value['type']
        fields = {'git_snapshot': set(), 'git_remote_preflight': set(), 'git_remote_sandbox_probe': set(), 'git_remote_relay_probe': set(), 'git_remote_ssh_probe': set(), 'git_fetch': set(), 'git_pull': set(), 'git_push': set(), 'git_stage': {'files', 'snapshotToken'},
                  'git_review': {'message'}, 'git_commit': {'reviewToken'}}
        if set(value) - ({'type', 'id', 'projectId', 'workspaceId'} | fields[operation]):
            raise ValueError('未対応のGit引数です。')
        if self.process or self.cleanup_uncertain:
            raise ValueError('実行中または終了未確認のためGit操作はできません。')
        if not isinstance(value.get('projectId'), str) or (value.get('workspaceId') is not None and not isinstance(value['workspaceId'], str)):
            raise ValueError('Git操作対象IDが不正です。')
        with self.locked_config():
            data = read_config()
            _, projects, workspaces = config(data)
            project_id = value.get('projectId')
            workspace_id, _, project = selected_workspace(data, projects, workspaces,
                                                        value.get('workspaceId'), project_id)
            if CONFIG.resolve().is_relative_to(project) or CONFIG.parent.resolve().is_relative_to(project):
                raise ValueError('Host設定と実行記録はGit対象の外に配置してください。')
            key = (workspace_id, project_id, project)
            token = secrets.token_urlsafe(24)
            atomic_json(execution_path(), {'version': 1, 'token': token, 'bootId': boot_id(),
                        'operation': operation, 'workspaceRoots': [project], 'startedAt': time.time()})
            repo = None
            operation_confirmed = False

            def confirm_operation():
                nonlocal operation_confirmed
                state = execution_state()
                if not state or state['token'] != token:
                    raise git_ops.UncertainGit('Git実行記録を照合できません。')
                remove_execution_state()
                repo.uncertain = False
                operation_confirmed = True

            try:
                repo = git_ops.Repository(project)
                if operation == 'git_snapshot':
                    result = git_snapshot(project)
                    try:
                        repo.writable()
                        fingerprint = repo.fingerprint()
                        snapshot_token = secrets.token_urlsafe(24)
                        self.git_snapshots[key] = (snapshot_token, fingerprint, time.monotonic())
                        result.update(snapshotToken=snapshot_token, canModify=True)
                    except git_ops.UncertainGit:
                        raise
                    except (ValueError, OSError) as error:
                        result.update(canModify=False, modificationError=str(error))
                    self.git_reviews.pop(key, None)
                elif operation == 'git_remote_preflight':
                    self.git_snapshots.pop(key, None)
                    self.git_reviews.pop(key, None)
                    result = git_remote_preflight(project)
                elif operation == 'git_remote_sandbox_probe':
                    self.git_snapshots.pop(key, None)
                    self.git_reviews.pop(key, None)
                    result = git_remote.sandbox_probe(project)
                elif operation == 'git_remote_relay_probe':
                    self.git_snapshots.pop(key, None)
                    self.git_reviews.pop(key, None)
                    result = git_remote.relay_probe(project)
                elif operation == 'git_remote_ssh_probe':
                    self.git_snapshots.pop(key, None)
                    self.git_reviews.pop(key, None)
                    result = git_remote.readonly_ssh_probe(project)
                elif operation == 'git_fetch':
                    self.git_snapshots.pop(key, None)
                    self.git_reviews.pop(key, None)
                    result = git_remote.fetch_upstream(project)
                elif operation == 'git_pull':
                    self.git_snapshots.pop(key, None); self.git_reviews.pop(key, None)
                    result = git_remote.pull_upstream(project)
                elif operation == 'git_push':
                    self.git_snapshots.pop(key, None); self.git_reviews.pop(key, None)
                    result = git_remote.push_upstream(project)
                elif operation == 'git_stage':
                    snapshot = self.git_snapshots.pop(key, None)
                    self.git_reviews.pop(key, None)
                    if not snapshot or value.get('snapshotToken') != snapshot[0] or time.monotonic() - snapshot[2] > 900:
                        raise ValueError('Git状態を再取得してからファイルを選択してください。')
                    repo.stage(value.get('files'), snapshot[1])
                    confirm_operation()
                    try:
                        result = git_snapshot(project)
                        snapshot_token = secrets.token_urlsafe(24)
                        self.git_snapshots[key] = (snapshot_token, git_ops.Repository(project).fingerprint(), time.monotonic())
                        result.update(canModify=True, snapshotToken=snapshot_token)
                    except (ValueError, OSError) as error:
                        result = {'refreshError': str(error)}
                elif operation == 'git_review':
                    self.git_reviews.pop(key, None)
                    review = repo.review(value.get('message'))
                    review_token = secrets.token_urlsafe(24)
                    self.git_reviews[key] = (review_token, review, time.monotonic())
                    result = {k: review[k] for k in ('tree', 'ref', 'parent', 'message', 'identity', 'diff')}
                    snapshot = git_snapshot(project)
                    result['stagedFiles'] = [
                        {key: row[key] for key in ('path', 'status', 'originalPath')}
                        for row in snapshot['files'] if row['status'] != '??' and row['status'][0] != ' ']
                    result['reviewToken'] = review_token
                else:
                    stored = self.git_reviews.pop(key, None)
                    self.git_snapshots.pop(key, None)
                    if not stored or value.get('reviewToken') != stored[0] or time.monotonic() - stored[2] > 900:
                        raise ValueError('Commit内容を最終確認してください。')
                    result = repo.commit(stored[1])
                    confirm_operation()
                    try:
                        result['files'] = git_snapshot(project)['files']
                    except (ValueError, OSError) as error:
                        result['refreshError'] = str(error)
                if not operation_confirmed:
                    confirm_operation()
            except BaseException as error:
                if isinstance(error, git_ops.UncertainGit) or repo is not None and repo.uncertain or not isinstance(error, (ValueError, OSError)):
                    self.cleanup_uncertain = True
                else:
                    state = execution_state()
                    if state and state['token'] == token:
                        remove_execution_state()
                    else:
                        self.cleanup_uncertain = True
                raise
        send({'type': operation, 'id': value['id'], **result})

    def respond(self, value):
        pending = self.pending_request
        if (not self.process or self.stop_requested or not pending or
                value.get('targetId') != self.request_id or
                value.get('requestId') != pending['requestId'] or
                value.get('itemId') != pending['itemId'] or
                self.thread_id != pending['threadId'] or
                self.turn_id != pending['turnId']):
            raise ValueError('回答対象の要求がありません。')
        if pending['kind'] == 'question':
            answers = value.get('answers')
            questions = pending['questions']
            if not isinstance(answers, dict) or set(answers) != {q['id'] for q in questions}:
                raise ValueError('全ての質問に回答してください。')
            result = {}
            for question in questions:
                answer = answers[question['id']]
                if not isinstance(answer, str) or not answer.strip() or len(answer) > 20000:
                    raise ValueError('回答は1〜20000文字で入力してください。')
                options = question.get('options') or []
                if options and not question.get('isOther') and answer not in [row['label'] for row in options]:
                    raise ValueError('選択肢から回答してください。')
                result[question['id']] = {'answers': [answer]}
            response = {'answers': result}
        else:
            decision = value.get('decision')
            if decision not in ('accept', 'decline'):
                raise ValueError('承認または拒否を選んでください。')
            if decision == 'accept':
                if value.get('approvalScope') != 'once':
                    raise ValueError('今回だけの承認を指定してください。')
                if not pending['approvable']:
                    raise ValueError('操作内容と理由を確認できないため承認できません。')
            response = {'decision': decision}
        self.rpc_response(pending['rpcId'], response)
        self.pending_request = None
        self.started = time.monotonic()
        send({'type': 'answered', 'id': self.request_id, 'requestId': pending['requestId']})

    def rpc_response(self, rpc_id, result):
        self.process.stdin.write((json.dumps({'id': rpc_id, 'result': result}) + '\n').encode())
        self.process.stdin.flush()

    def request_interaction(self, value):
        method = value['method']
        params = value.get('params')
        kinds = {'item/commandExecution/requestApproval': 'command',
                 'item/fileChange/requestApproval': 'file',
                 'item/tool/requestUserInput': 'question'}
        if (method not in kinds or self.operation != 'run' or self.pending_request or
                not isinstance(params, dict) or params.get('threadId') != self.thread_id or
                not isinstance(params.get('turnId'), str) or
                (self.turn_id and params.get('turnId') != self.turn_id)):
            self.finish('unsupported_request', f'対応できないCodex要求です: {method}')
            return
        if self.turn_id is None:
            self.turn_id = params['turnId']
        kind = kinds[method]
        item_id = params.get('itemId')
        if not isinstance(item_id, str) or not item_id:
            self.finish('unsupported_request', '承認対象の項目IDを確認できません。')
            return
        details = {}
        approvable = False
        if kind == 'command':
            item = self.items.get(item_id, {})
            if item and item.get('type') != 'commandExecution':
                self.finish('unsupported_request', '承認要求と実行項目の種類が一致しません。')
                return
            if params.get('networkApprovalContext') is not None or params.get('kind') not in (None, 'command'):
                self.finish('unsupported_request', 'この形式のコマンド承認には対応していません。')
                return
            request_command = params.get('command')
            item_command = item.get('command') if item.get('type') == 'commandExecution' else None
            if params.get('cwd') and item.get('cwd') and params['cwd'] != item['cwd']:
                self.finish('unsupported_request', '承認要求と実行項目の作業場所が一致しません。')
                return
            if request_command and item_command and request_command != item_command:
                self.finish('unsupported_request', '承認要求と実行項目のコマンドが一致しません。')
                return
            command = request_command or item_command
            reason = params.get('reason')
            cwd = params.get('cwd') or (item.get('cwd') if item.get('type') == 'commandExecution' else None)
            details = {'command': command, 'cwd': cwd, 'reason': reason}
            # A command may read, write, contact the network, or elevate privileges.
            # Displaying its text cannot establish that it is within Focodex policy.
            approvable = False
        elif kind == 'file':
            item = self.items.get(item_id, {})
            if item and item.get('type') != 'fileChange':
                self.finish('unsupported_request', '承認要求と変更項目の種類が一致しません。')
                return
            changes = item.get('changes') if item.get('type') == 'fileChange' else None
            if not isinstance(changes, list):
                changes = []
            changes = [{key: row.get(key) for key in ('path', 'kind', 'diff')}
                       for row in changes if isinstance(row, dict)]
            details = {'changes': changes, 'grantRoot': params.get('grantRoot'),
                       'reason': params.get('reason')}
            # Generic file-change approval could grant writes outside the project.
            approvable = False
        else:
            questions = params.get('questions')
            if (not isinstance(questions, list) or not 1 <= len(questions) <= 3 or
                    any(not isinstance(q, dict) or not isinstance(q.get('id'), str) or
                        not isinstance(q.get('question'), str) or q.get('isSecret') or
                        (q.get('options') is not None and
                         (not isinstance(q['options'], list) or
                          any(not isinstance(o, dict) or not isinstance(o.get('label'), str)
                              for o in q['options']))) for q in questions) or
                    len({q['id'] for q in questions}) != len(questions)):
                self.finish('unsupported_request', 'この形式の追加質問には対応していません。')
                return
            details = {'questions': questions}
        if len(json.dumps(details, ensure_ascii=False).encode('utf-8')) > 32000:
            if kind == 'question':
                self.finish('unsupported_request', '要求内容が表示上限を超えました。')
                return
            details = {'reason': '表示上限を超えたため内容を表示できません。'}
            approvable = False
        request_id = secrets.token_urlsafe(18)
        self.pending_request = {'rpcId': value['id'], 'requestId': request_id,
                                'itemId': item_id, 'threadId': self.thread_id,
                                'turnId': self.turn_id, 'kind': kind, 'details': details,
                                'approvable': approvable,
                                'questions': details.get('questions', [])}
        send({'type': 'interaction', 'id': self.request_id,
              'requestId': request_id, 'itemId': item_id,
              'kind': kind, 'details': details,
              'approvable': approvable, 'approvalScope': 'once' if kind != 'question' else None})

    def event(self, value):
        if self.trace and isinstance(value, dict) and isinstance(value.get('id'), int):
            self.trace_event('rpc_received', rpcId=value['id'])
        if 'method' in value and 'id' in value:
            self.request_interaction(value)
            return
        if 'error' in value and 'id' in value:
            self.trace_event('rpc_error', rpcId=value['id'])
            error = json.dumps(value['error'], ensure_ascii=False)[:4000]
            if 'bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted' in error:
                error = ('CodexのLinux隔離を開始できませんでした。bwrapのループバック設定が'
                         'AppArmor等で拒否されています。権限を緩めず、端末のbwrap設定を確認してください。')
            self.finish('failed', error)
            return
        if value.get('id') == 1:
            self.agent_version = value['result'].get('userAgent')
            self.rpc('initialized', {})
            if self.operation == 'models':
                self.model_page = 1
                self.rpc('model/list', {'limit': 100, 'includeHidden': False}, 4)
            else:
                params = {'cwd': self.cwd, 'ephemeral': True,
                          'permissions': PERMISSION_PROFILE, 'approvalPolicy': 'on-request'}
                if self.model is not None:
                    params['model'] = self.model
                self.rpc('thread/start', params, 2)
        elif value.get('id') == 4 and self.operation == 'models':
            result = value['result']
            raw_rows = result.get('data')
            self.trace_event('model_page_received', page=self.model_page,
                             itemCount=len(raw_rows) if isinstance(raw_rows, list) else -1,
                             hasNext=bool(result.get('nextCursor')))
            for row in result['data']:
                if row.get('hidden') or 'text' not in row.get('inputModalities', ['text']):
                    continue
                self.model_rows.append({key: row.get(key) for key in
                                        ('model', 'displayName', 'isDefault')})
            if len(self.model_rows) > 500:
                raise ValueError('モデル一覧の上限を超えました。')
            cursor = result.get('nextCursor')
            if cursor:
                if cursor in self.cursors or len(self.cursors) >= 20:
                    raise ValueError('モデル一覧のページ取得を継続できません。')
                self.cursors.add(cursor)
                self.model_page += 1
                self.rpc('model/list', {'limit': 100, 'includeHidden': False, 'cursor': cursor}, 4)
                return
            rows = list({row['model']: row for row in self.model_rows if isinstance(row['model'], str)}.values())
            self.catalogs[(self.workspace_id, self.cwd)] = {row['model'] for row in rows}
            message = {'type': 'models', 'id': self.request_id, 'models': rows}
            process_state = self.cleanup()
            self.trace_terminal('completed', process_state=process_state)
            message.update(self.trace_details())
            self.request_id = None
            send(message)
            self.clear_trace()
        elif value.get('id') == 2:
            result = value['result']
            self.thread_id = result['thread']['id']
            self.settings = {key: result.get(key) for key in
                             ('model', 'approvalPolicy', 'approvalsReviewer', 'sandbox',
                              'activePermissionProfile', 'runtimeWorkspaceRoots')}
            active = result.get('activePermissionProfile')
            sandbox = result.get('sandbox')
            runtime = result.get('runtimeWorkspaceRoots')
            roots = set(runtime) | set(sandbox.get('writableRoots', [])) if (
                isinstance(runtime, list) and isinstance(sandbox, dict) and
                isinstance(sandbox.get('writableRoots'), list)) else set()
            if (not isinstance(active, dict) or active.get('id') != PERMISSION_PROFILE or
                    result.get('approvalPolicy') != 'on-request' or
                    runtime != [self.cwd] or roots != set(self.workspace_roots) or
                    not isinstance(sandbox, dict) or sandbox.get('type') != 'workspaceWrite' or
                    sandbox.get('networkAccess') is not False or
                    sandbox.get('excludeTmpdirEnvVar') is not True or
                    sandbox.get('excludeSlashTmp') is not True):
                self.finish('failed', 'Codexの有効な権限設定を確認できません。実行を開始しません。')
                return
            self.rpc('turn/start', {'threadId': self.thread_id,
                     'input': [{'type': 'text', 'text': self.prompt}]}, 3)
        elif value.get('id') == 3:
            turn_id = value['result']['turn']['id']
            if self.turn_id and self.turn_id != turn_id:
                self.finish('failed', '開始したターンと承認要求のターンが一致しません。')
                return
            self.turn_id = turn_id
            self.interrupt()
        elif value.get('id') == 5:
            self.interrupt_acknowledged = True
        elif value.get('method') == 'item/started':
            params = value.get('params', {})
            item = params.get('item', {})
            if (params.get('threadId') == self.thread_id and
                    (not self.turn_id or params.get('turnId') == self.turn_id) and
                    isinstance(item, dict) and isinstance(item.get('id'), str) and
                    item.get('type') in ('commandExecution', 'fileChange')):
                self.items[item['id']] = item
        elif value.get('method') == 'serverRequest/resolved':
            params = value.get('params', {})
            pending = self.pending_request
            if (pending and params.get('threadId') == pending['threadId'] and
                    params.get('requestId') == pending['rpcId']):
                self.pending_request = None
                send({'type': 'interaction_resolved', 'id': self.request_id,
                      'requestId': pending['requestId'], 'itemId': pending['itemId']})
        elif value.get('method') == 'item/completed':
            params = value['params']
            item = params['item']
            if params.get('threadId') == self.thread_id and item.get('type') == 'agentMessage':
                self.messages[item['id']] = item['text']
                self.trace_event('agent_message', length=len(item['text']))
                if sum(len(text) for text in self.messages.values()) > MAX_TEXT:
                    self.messages = {}
                    self.finish('failed', '応答が検証版の文字数上限を超えました。')
        elif value.get('method') == 'turn/completed':
            params = value['params']
            if params.get('threadId') != self.thread_id:
                return
            turn = params['turn']
            self.turn_id = turn['id']
            status = turn['status']
            if status not in ('completed', 'failed', 'interrupted'):
                raise ValueError('未知の終了状態です。')
            self.codex_turn_status = status
            self.finish(status, turn.get('error'))

    def loop(self):
        try:
            while True:
                if self.process and self.stop_requested and time.monotonic() - self.stop_started > STOP_TIMEOUT:
                    self.stop_fallback_used = True
                    self.finish('interrupted', '中断応答が5秒以内に届かず、専用プロセス群を終了しました。')
                if self.process and not self.pending_request and time.monotonic() - self.started > TIMEOUT:
                    self.finish('timeout', '180秒以内に終了しませんでした。実行を終了しました。')
                for key, _ in self.selector.select(0.2):
                    if key.data == 'codex' and not self.process:
                        continue
                    chunk = os.read(key.fd, 65536)
                    if key.data == 'native':
                        if not chunk:
                            return
                        self.native_buffer.extend(chunk)
                        while len(self.native_buffer) >= 4:
                            size = struct.unpack('=I', self.native_buffer[:4])[0]
                            if size > MAX_NATIVE:
                                raise ValueError('Native Messagingの入力上限を超えました。')
                            if len(self.native_buffer) < size + 4:
                                break
                            body = bytes(self.native_buffer[4:4 + size])
                            del self.native_buffer[:4 + size]
                            value = json.loads(body)
                            try:
                                self.command(value)
                            except (ValueError, KeyError, OSError) as error:
                                active = self.trace and self.trace['requestId'] == value.get('id')
                                if active:
                                    self.trace_terminal('rejected', self.error_code(error))
                                    details = self.trace_details()
                                else:
                                    details = getattr(error, 'trace_details', {})
                                send({'type': 'error', 'id': value.get('id') if isinstance(value, dict) else None,
                                      'error': str(error), **details})
                                if active:
                                    self.clear_trace()
                    elif self.process:  # An earlier event may already have ended it.
                        if not chunk:
                            self.finish('failed', 'Codexとの接続が終了結果を返す前に切れました。')
                            continue
                        self.rpc_buffer.extend(chunk)
                        if len(self.rpc_buffer) > MAX_RPC:
                            raise ValueError('Codexイベントの上限を超えました。')
                        while self.process and b'\n' in self.rpc_buffer:
                            line, _, rest = self.rpc_buffer.partition(b'\n')
                            self.rpc_buffer = bytearray(rest)
                            if line.strip():
                                self.event(json.loads(line))
        finally:
            self.cleanup()
            self.selector.close()


def main():
    host = Host()
    def terminate(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        host.loop()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        host.trace_terminal('failed', host.error_code(error))
        send({'type': 'error', 'id': host.request_id, 'error': str(error), **host.trace_details()})


if __name__ == '__main__':
    main()
