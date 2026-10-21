#!/usr/bin/env python3
"""Linux-only, one-turn Codex Native Messaging proof of concept."""
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import secrets
import signal
import struct
import subprocess
import sys
import time

CONFIG = Path(os.environ.get('FOCODEX_CONFIG', Path.home() / '.config/focodex/config.json'))
MAX_NATIVE = 128 * 1024
MAX_RPC = 8 * 1024 * 1024
MAX_TEXT = 160_000  # Keeps the UTF-8 native result below Firefox's 1 MiB limit.
TIMEOUT = 180
STOP_TIMEOUT = 5
PERMISSION_PROFILE = 'focodex-project'
GIT = '/usr/bin/git'
MAX_GIT_OUTPUT = 250_000


def git_read(project, *arguments):
    env = {key: item for key, item in os.environ.items() if not key.startswith('GIT_')}
    env.update({'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1',
                'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
                'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat'})
    command = [GIT, '--no-pager', '--no-optional-locks', '-c', 'core.fsmonitor=false',
               '-c', 'core.hooksPath=/dev/null', '-c', 'diff.external=',
               '-c', 'color.ui=false', '-C', str(project), *arguments]
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    output_bytes = bytearray()
    try:
        with selectors.DefaultSelector() as reader:
            reader.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 10
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('Gitの読み取りが時間切れになりました。')
                if not reader.select(remaining):
                    raise ValueError('Gitの読み取りが時間切れになりました。')
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                output_bytes.extend(chunk)
                if len(output_bytes) > MAX_GIT_OUTPUT:
                    raise ValueError('Gitの出力が表示上限を超えました。')
        process.wait(timeout=1)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
    output = output_bytes.decode('utf-8', 'replace')
    if process.returncode:
        raise ValueError(f'Gitの読み取りに失敗しました: {output[:2000]}')
    return output


def git_snapshot(project):
    project = Path(project).resolve(strict=True)
    dotgit = project / '.git'
    if not dotgit.exists() or dotgit.is_symlink():
        raise ValueError('プロジェクト直下のGit管理情報を確認できません。')
    top = Path(git_read(project, 'rev-parse', '--show-toplevel').strip()).resolve()
    gitdir = Path(git_read(project, 'rev-parse', '--absolute-git-dir').strip()).resolve()
    common = Path(git_read(project, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()).resolve()
    if top != project:
        raise ValueError('登録済みプロジェクトとGitリポジトリのルートが一致しません。')
    if dotgit.is_dir():
        valid = gitdir == dotgit.resolve() and common == gitdir
    elif dotgit.is_file():
        pointer = dotgit.read_text(encoding='utf-8', errors='replace').strip()
        target = (project / pointer[8:]).resolve() if pointer.startswith('gitdir: ') else None
        backlink = gitdir / 'gitdir'
        valid = (target == gitdir and gitdir.parent.name == 'worktrees' and
                 gitdir.parent.parent == common and backlink.is_file() and
                 Path(backlink.read_text().strip()).resolve() == dotgit)
    else:
        valid = False
    if not valid:
        raise ValueError('Git管理ディレクトリの実体が一致しません。')
    # Git may execute project-controlled filters, fsmonitors or diff helpers.
    # Refuse these repositories until a dedicated helper policy is designed.
    settings = git_read(project, 'config', '--list', '--null')
    for entry in settings.split('\0'):
        key = entry.split('\n', 1)[0].lower()
        if (key.startswith(('filter.', 'include.', 'includeif.')) or
                key == 'core.fsmonitor' and '\nfalse' not in entry or
                key == 'diff.external' and '\n' in entry and entry.split('\n', 1)[1] or
                re.match(r'^diff\.[^.]+\.(command|textconv)$', key)):
            raise ValueError('外部ヘルパーを含むGit設定は現在の表示機能で扱えません。')
    return {'status': git_read(project, 'status', '--short', '--untracked-files=all'),
            'unstagedDiff': git_read(project, 'diff', '--no-ext-diff', '--no-textconv', '--no-color', '--'),
            'stagedDiff': git_read(project, 'diff', '--cached', '--no-ext-diff', '--no-textconv', '--no-color', '--')}


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, dict):
        return '{' + ', '.join(f'{json.dumps(key)} = {toml_value(item)}'
                               for key, item in value.items()) + '}'
    raise TypeError('Unsupported permission profile value')


def codex_args(executable):
    binary = str(Path(executable).resolve(strict=True))
    profile = {'filesystem': {':root': 'deny', ':minimal': 'read',
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


def config():
    data = json.loads(CONFIG.read_text())
    executable = data['codex']
    if not isinstance(executable, str) or not Path(executable).is_absolute() or not os.access(executable, os.X_OK):
        raise ValueError('設定のcodexには実行可能ファイルの絶対パスが必要です。')
    projects = data['projects']
    if not isinstance(projects, dict) or not projects:
        raise ValueError('projectsにプロジェクトを登録してください。')
    for key, value in projects.items():
        if not key or not isinstance(value, dict) or not isinstance(value.get('path'), str):
            raise ValueError('プロジェクト設定が不正です。')
        path = Path(value['path'])
        if not path.is_absolute() or not path.is_dir():
            raise ValueError(f'プロジェクトのディレクトリが見つかりません: {key}')
    return executable, projects


class Host:
    def __init__(self):
        self.selector = selectors.DefaultSelector()
        self.selector.register(sys.stdin.buffer, selectors.EVENT_READ, 'native')
        self.native_buffer = bytearray()
        self.rpc_buffer = bytearray()
        self.process = None
        self.lock = None
        self.request_id = None
        self.thread_id = None
        self.turn_id = None
        self.messages = {}
        self.started = 0
        self.prompt = ''
        self.cwd = ''
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

    def rpc(self, method, params, rpc_id=None):
        value = {'method': method, 'params': params}
        if rpc_id is not None:
            value['id'] = rpc_id
        self.process.stdin.write((json.dumps(value) + '\n').encode())
        self.process.stdin.flush()

    def cleanup(self):
        process_state = None
        if self.process:
            process = self.process
            process_state = {'leaderExitCode': None, 'processGroupGone': False,
                             'terminationSignal': None}
            self.selector.unregister(process.stdout)
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
        self.request_id = None
        self.stop_requested = False
        self.interrupt_sent = False
        self.interrupt_acknowledged = False
        self.stop_fallback_used = False
        self.codex_turn_status = None
        self.pending_request = None
        self.items = {}
        send(result)

    def interrupt(self):
        if self.stop_requested and not self.interrupt_sent and self.thread_id and self.turn_id:
            self.rpc('turn/interrupt', {'threadId': self.thread_id, 'turnId': self.turn_id}, 5)
            self.interrupt_sent = True

    def command(self, value):
        if not isinstance(value, dict) or not isinstance(value.get('id'), str):
            raise ValueError('id付きのメッセージが必要です。')
        request_id = value['id']
        if value.get('type') == 'projects':
            _, projects = config()
            send({'type': 'projects', 'id': request_id, 'projects': [
                {'id': key, 'name': item.get('name', key), 'path': item['path']}
                for key, item in projects.items()]})
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
        if value.get('type') == 'git_snapshot':
            if self.process or self.cleanup_uncertain:
                raise ValueError('Codex実行中または終了未確認のためGit状態を取得できません。')
            _, projects = config()
            project_id = value.get('projectId')
            if not isinstance(project_id, str) or project_id not in projects:
                raise ValueError('未登録のプロジェクトです。')
            with CONFIG.with_suffix('.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError('別のFocodexタブで実行中です。')
                snapshot = git_snapshot(projects[project_id]['path'])
            send({'type': 'git_snapshot', 'id': request_id, **snapshot})
            return
        if value.get('type') not in ('run', 'models'):
            raise ValueError('未対応の操作です。')
        if self.cleanup_uncertain:
            raise ValueError('前回の専用プロセス群の終了を確認できません。再実行できません。')
        if self.process:
            raise ValueError('実行中です。同時実行は1件です。')
        executable, projects = config()
        project_id = value.get('projectId')
        if not isinstance(project_id, str) or project_id not in projects:
            raise ValueError('未登録のプロジェクトです。')
        operation = value['type']
        prompt = value.get('text', '')
        cwd = str(Path(projects[project_id]['path']).resolve())
        model = value.get('model')
        if operation == 'run':
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
                raise ValueError('指示は1〜20000文字で入力してください。')
            if model is not None and (not isinstance(model, str) or model not in self.catalogs.get(cwd, set())):
                raise ValueError('モデル一覧を取得し、候補からモデルを選択してください。')
        self.lock = CONFIG.with_suffix('.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            self.lock = None
            raise ValueError('別のFocodexタブで実行中です。')
        self.cwd = cwd
        self.operation, self.model = operation, model
        self.model_rows = []
        self.cursors = set()
        if operation == 'models':
            self.catalogs.pop(cwd, None)
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
            self.process = subprocess.Popen(
                codex_args(executable), cwd=self.cwd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                start_new_session=True, bufsize=0)
            self.selector.register(self.process.stdout, selectors.EVENT_READ, 'codex')
            self.rpc('initialize', {'clientInfo': {'name': 'focodex_probe',
                     'title': 'Focodex connection probe', 'version': '0.1.1'},
                     'capabilities': {'experimentalApi': True}}, 1)
        except Exception:
            self.cleanup()
            self.request_id = None
            raise
        send({'type': 'started', 'id': request_id})

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
        if 'method' in value and 'id' in value:
            self.request_interaction(value)
            return
        if 'error' in value and 'id' in value:
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
                self.rpc('model/list', {'limit': 100, 'includeHidden': False}, 4)
            else:
                params = {'cwd': self.cwd, 'ephemeral': True,
                          'permissions': PERMISSION_PROFILE, 'approvalPolicy': 'on-request'}
                if self.model is not None:
                    params['model'] = self.model
                self.rpc('thread/start', params, 2)
        elif value.get('id') == 4 and self.operation == 'models':
            result = value['result']
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
                self.rpc('model/list', {'limit': 100, 'includeHidden': False, 'cursor': cursor}, 4)
                return
            rows = list({row['model']: row for row in self.model_rows if isinstance(row['model'], str)}.values())
            self.catalogs[self.cwd] = {row['model'] for row in rows}
            message = {'type': 'models', 'id': self.request_id, 'models': rows}
            self.cleanup()
            self.request_id = None
            send(message)
        elif value.get('id') == 2:
            result = value['result']
            self.thread_id = result['thread']['id']
            self.settings = {key: result.get(key) for key in
                             ('model', 'approvalPolicy', 'approvalsReviewer', 'sandbox',
                              'activePermissionProfile', 'runtimeWorkspaceRoots')}
            active = result.get('activePermissionProfile')
            if (not isinstance(active, dict) or active.get('id') != PERMISSION_PROFILE or
                    result.get('approvalPolicy') != 'on-request' or
                    result.get('runtimeWorkspaceRoots') != [self.cwd]):
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
                                send({'type': 'error', 'id': value.get('id') if isinstance(value, dict) else None,
                                      'error': str(error)})
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
        send({'type': 'error', 'id': host.request_id, 'error': str(error)})


if __name__ == '__main__':
    main()
