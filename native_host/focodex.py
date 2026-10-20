#!/usr/bin/env python3
"""Linux-only, one-turn Codex Native Messaging proof of concept."""
import fcntl
import json
import os
from pathlib import Path
import selectors
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

    def rpc(self, method, params, rpc_id=None):
        value = {'method': method, 'params': params}
        if rpc_id is not None:
            value['id'] = rpc_id
        self.process.stdin.write((json.dumps(value) + '\n').encode())
        self.process.stdin.flush()

    def cleanup(self):
        if self.process:
            process = self.process
            self.selector.unregister(process.stdout)
            # Kill only the dedicated process group created for this invocation.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
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
            process.wait(timeout=3)
            process.stdin.close()
            process.stdout.close()
            self.process = None
        if self.lock:
            self.lock.close()
            self.lock = None
        self.rpc_buffer.clear()

    def finish(self, status, error=None):
        result = {'type': 'result', 'id': self.request_id, 'status': status,
                  'text': '\n\n'.join(self.messages.values()), 'error': error,
                  'threadId': self.thread_id, 'turnId': self.turn_id,
                  'agentVersion': self.agent_version, 'settings': self.settings}
        self.cleanup()
        self.request_id = None
        send(result)

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
        if value.get('type') not in ('run', 'models'):
            raise ValueError('未対応の操作です。')
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
        self.started = time.monotonic()
        try:
            self.process = subprocess.Popen(
                [executable, 'app-server', '--listen', 'stdio://'], cwd=self.cwd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                start_new_session=True, bufsize=0)
            self.selector.register(self.process.stdout, selectors.EVENT_READ, 'codex')
            self.rpc('initialize', {'clientInfo': {'name': 'focodex_probe',
                     'title': 'Focodex connection probe', 'version': '0.1.1'}}, 1)
        except Exception:
            self.cleanup()
            self.request_id = None
            raise
        send({'type': 'started', 'id': request_id})

    def event(self, value):
        # Step 1 deliberately has no approval/question UI. Never auto-approve.
        if 'method' in value and 'id' in value:
            self.finish('unsupported_request', f"この検証版は承認・質問に未対応です: {value['method']}")
            return
        if 'error' in value and 'id' in value:
            self.finish('failed', json.dumps(value['error'], ensure_ascii=False)[:4000])
            return
        if value.get('id') == 1:
            self.agent_version = value['result'].get('userAgent')
            self.rpc('initialized', {})
            if self.operation == 'models':
                self.rpc('model/list', {'limit': 100, 'includeHidden': False}, 4)
            else:
                # Only the user's explicit model selection overrides a setting.
                # approvalPolicy, sandbox and config remain inherited.
                params = {'cwd': self.cwd, 'ephemeral': True}
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
                             ('model', 'approvalPolicy', 'approvalsReviewer', 'sandbox')}
            self.rpc('turn/start', {'threadId': self.thread_id,
                     'input': [{'type': 'text', 'text': self.prompt}]}, 3)
        elif value.get('id') == 3:
            self.turn_id = value['result']['turn']['id']
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
            self.finish(status, turn.get('error'))

    def loop(self):
        try:
            while True:
                if self.process and time.monotonic() - self.started > TIMEOUT:
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
