#!/usr/bin/env python3
"""Verify multiple Focodex workspace roots with the real Codex app-server.

This creates only a temporary directory and removes it on completion.  It does
not use the Native Host configuration or modify any persistent Codex settings.
"""
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time

PROFILE = 'focodex-workspace-verify'


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, dict):
        return '{' + ', '.join(f'{json.dumps(key)} = {toml_value(item)}'
                               for key, item in value.items()) + '}'
    raise TypeError(f'Unsupported value: {value!r}')


class AppServer:
    def __init__(self, command):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, bufsize=1)
        self.next_id = 1

    def request(self, method, params, timeout=30):
        request_id = self.next_id
        self.next_id += 1
        self.process.stdin.write(json.dumps({'id': request_id, 'method': method,
                                              'params': params}) + '\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise TimeoutError(f'{method} の応答待ちが時間切れです。')
            response = json.loads(self.process.stdout.readline())
            if response.get('id') == request_id:
                if 'error' in response:
                    raise RuntimeError(f'{method}: {response["error"]}')
                return response['result']

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=8)
        stderr = self.process.stderr.read().strip()
        if stderr:
            print(f'app-server stderr:\n{stderr}', file=sys.stderr)


def shell_quote(path):
    import shlex
    return shlex.quote(str(path))


def command_result(server, command, cwd):
    return server.request('command/exec', {'command': ['/bin/sh', '-lc', command], 'cwd': str(cwd),
                                           'permissionProfile': PROFILE})


def expect(label, result, predicate):
    if not predicate(result):
        raise AssertionError(f'{label} が期待と異なります: {json.dumps(result, ensure_ascii=False)}')
    print(f'PASS {label}: {json.dumps(result, ensure_ascii=False)}')


def main():
    executable = shutil.which('codex')
    if not executable:
        raise RuntimeError('codex がPATH上にありません。')
    executable = str(Path(executable).resolve(strict=True))
    with tempfile.TemporaryDirectory(prefix='focodex-workspace-verify-') as temporary:
        root = Path(temporary)
        repo_a, repo_b, repo_c = (root / name for name in ('repo-a', 'repo-b', 'repo-c'))
        for directory in (repo_a, repo_b, repo_c):
            directory.mkdir()
        (repo_a / 'seed-a.txt').write_text('A-READ\n')
        (repo_b / 'seed-b.txt').write_text('B-READ\n')
        (repo_c / 'secret-c.txt').write_text('C-MUST-NOT-READ\n')
        (repo_a / 'link-to-c').symlink_to(repo_c, target_is_directory=True)

        profile = {
            'workspace_roots': {str(repo_a): True, str(repo_b): True},
            'filesystem': {':root': 'deny', ':minimal': 'read',
                           ':workspace_roots': {'.': 'write'},
                           ':tmpdir': 'deny', ':slash_tmp': 'deny', executable: 'read'},
            'network': {'enabled': False},
        }
        command = [executable, '-c', f'default_permissions={json.dumps(PROFILE)}',
                   '-c', f'permissions.{PROFILE}={toml_value(profile)}',
                   'app-server', '--listen', 'stdio://']
        server = AppServer(command)
        try:
            initialized = server.request('initialize', {
                'clientInfo': {'name': 'focodex-workspace-verify',
                               'title': 'Focodex workspace permission verification', 'version': '0.1.0'},
                'capabilities': {'experimentalApi': True}})
            print('initialize:', json.dumps(initialized, ensure_ascii=False))
            profiles = server.request('permissionProfile/list', {'cwd': str(repo_a)})
            print('permissionProfile/list:', json.dumps(profiles, ensure_ascii=False))

            thread = server.request('thread/start', {
                'cwd': str(repo_a), 'ephemeral': True, 'permissions': PROFILE,
                'approvalPolicy': 'on-request'})
            print('thread/start:', json.dumps(thread, ensure_ascii=False))
            active = thread.get('activePermissionProfile', {})
            if active.get('id') != PROFILE:
                raise AssertionError('選択したpermission profileが有効ではありません。')
            if thread.get('runtimeWorkspaceRoots') != [str(repo_a)]:
                raise AssertionError('開始プロジェクトのruntimeWorkspaceRootsが想定外です。')

            expect('Aへの書き込み', command_result(server,
                   f'printf A-WRITE > {shell_quote(repo_a / "write-a.txt")}', repo_a),
                   lambda value: value.get('exitCode') == 0 and (repo_a / 'write-a.txt').read_text() == 'A-WRITE')
            expect('Bへの書き込み', command_result(server,
                   f'printf B-WRITE > {shell_quote(repo_b / "write-b.txt")}', repo_a),
                   lambda value: value.get('exitCode') == 0 and (repo_b / 'write-b.txt').read_text() == 'B-WRITE')
            expect('Bの読み取り', command_result(server,
                   f'cat {shell_quote(repo_b / "seed-b.txt")}', repo_a),
                   lambda value: value.get('exitCode') == 0 and 'B-READ' in value.get('stdout', ''))
            expect('未登録Cの直接読み取り拒否', command_result(server,
                   f'cat {shell_quote(repo_c / "secret-c.txt")}', repo_a),
                   lambda value: 'C-MUST-NOT-READ' not in value.get('stdout', ''))
            expect('未登録Cのリンク経由読み取り拒否', command_result(server,
                   f'cat {shell_quote(repo_a / "link-to-c" / "secret-c.txt")}', repo_a),
                   lambda value: 'C-MUST-NOT-READ' not in value.get('stdout', ''))
            expect('未登録Cがホスト側で不変', command_result(server,
                   f'printf FORBIDDEN > {shell_quote(repo_c / "write-c.txt")}', repo_a),
                   lambda value: not (repo_c / 'write-c.txt').exists())
        finally:
            server.close()


if __name__ == '__main__':
    main()
