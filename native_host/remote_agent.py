#!/usr/bin/env python3
"""Prepare and inspect the Focodex-only SSH Agent without handling secrets."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

AGENT_NAME = 'remote-agent.sock'
DIRECTORY_NAME = 'focodex'
SSH_AGENT = '/usr/bin/ssh-agent'
SSH_ADD = '/usr/bin/ssh-add'
BUSCTL = '/usr/bin/busctl'
SECRET_TOOL = '/usr/bin/secret-tool'
TIMEOUT = 5
CONFIG = Path(os.environ.get('FOCODEX_REMOTE_AGENT_CONFIG',
                             Path.home() / '.config/focodex/remote-agent.json'))


def runtime_directory(runtime=None):
    path = Path(runtime if runtime is not None else os.environ.get('XDG_RUNTIME_DIR', ''))
    if not path.is_absolute():
        raise ValueError('XDG_RUNTIME_DIRを安全に確認できません。')
    try:
        status = path.stat()
    except OSError as error:
        raise ValueError('XDG_RUNTIME_DIRを確認できません。') from error
    if (not stat.S_ISDIR(status.st_mode) or status.st_uid != os.geteuid() or
            status.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
        raise ValueError('XDG_RUNTIME_DIRの所有者または権限が不正です。')
    return path


def agent_paths(runtime=None):
    base = runtime_directory(runtime) / DIRECTORY_NAME
    return base, base / AGENT_NAME


def prepare(runtime=None):
    directory, socket = agent_paths(runtime)
    try:
        status = directory.lstat()
    except FileNotFoundError:
        directory.mkdir(mode=0o700)
    else:
        if (not stat.S_ISDIR(status.st_mode) or stat.S_ISLNK(status.st_mode) or
                status.st_uid != os.geteuid() or status.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
            raise ValueError('専用Agentディレクトリの所有者または権限が不正です。')
    try:
        socket.lstat()
    except FileNotFoundError:
        return socket
    raise ValueError('既存の専用Agentソケットを自動削除しません。')


def keyring_unlocked():
    if not Path(BUSCTL).is_file():
        raise ValueError('Secret Service診断に必要なbusctlがありません。')
    result = subprocess.run([BUSCTL, '--user', 'get-property', 'org.freedesktop.secrets',
                             '/org/freedesktop/secrets/collection/login',
                             'org.freedesktop.Secret.Collection', 'Locked'], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=False)
    if result.returncode or result.stdout.strip() not in (b'b true', b'b false'):
        raise ValueError('GNOME Keyringの状態を確認できません。')
    return result.stdout.strip() == b'b false'


def settings(path=None):
    path = CONFIG if path is None else Path(path)
    try:
        status = path.lstat()
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError('専用Agent設定を安全に読み取れません。') from error
    if (not stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode) or
            status.st_uid != os.geteuid() or status.st_mode & (stat.S_IRWXG | stat.S_IRWXO) or
            not isinstance(data, dict) or data.get('version') != 1):
        raise ValueError('専用Agent設定の所有者、権限、形式が不正です。')
    key, attributes = data.get('privateKey'), data.get('secretAttributes')
    if (not isinstance(key, str) or not Path(key).is_absolute() or
            not isinstance(attributes, dict) or not attributes or
            any(not isinstance(name, str) or not name or not isinstance(value, str) or not value
                for name, value in attributes.items())):
        raise ValueError('専用Agent設定の鍵またはSecret Service属性が不正です。')
    private_key = Path(key)
    try:
        key_status = private_key.lstat()
    except OSError as error:
        raise ValueError('SSH秘密鍵を安全に確認できません。') from error
    if (not stat.S_ISREG(key_status.st_mode) or stat.S_ISLNK(key_status.st_mode) or
            key_status.st_uid != os.geteuid() or key_status.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
        raise ValueError('SSH秘密鍵の所有者または権限が不正です。')
    return private_key, tuple(sorted(attributes.items()))


def askpass():
    """Emit only the configured Secret Service value, for ssh-add's stdin pipe."""
    _key, attributes = settings()
    if not keyring_unlocked() or not Path(SECRET_TOOL).is_file():
        return 1
    command = [SECRET_TOOL, 'lookup'] + [value for pair in attributes for value in pair]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=False)
    if result.returncode or not result.stdout.rstrip(b'\n'):
        return 1
    os.write(1, result.stdout.rstrip(b'\n') + b'\n')
    return 0


def secret_available():
    _key, attributes = settings()
    if not keyring_unlocked() or not Path(SECRET_TOOL).is_file():
        return False
    command = [SECRET_TOOL, 'lookup'] + [value for pair in attributes for value in pair]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=False)
    return result.returncode == 0


def agent_status(runtime=None):
    _directory, socket = agent_paths(runtime)
    try:
        status = socket.lstat()
    except OSError:
        return 'missing'
    if (not stat.S_ISSOCK(status.st_mode) or status.st_uid != os.geteuid() or
            status.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
        return 'invalid'
    if not Path(SSH_ADD).is_file():
        return 'unavailable'
    result = subprocess.run([SSH_ADD, '-l'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin',
                            'SSH_AUTH_SOCK': str(socket)}, timeout=TIMEOUT, check=False)
    return 'ready' if result.returncode == 0 else 'empty' if result.returncode == 1 else 'invalid'


def serve(runtime=None):
    socket = prepare(runtime)
    if not Path(SSH_AGENT).is_file():
        raise ValueError('専用Agentに必要なssh-agentがありません。')
    os.execve(SSH_AGENT, [SSH_AGENT, '-D', '-a', str(socket)], {'PATH': '/usr/bin:/bin'})


def register(runtime=None):
    private_key, _attributes = settings()
    runtime_path = runtime_directory(runtime)
    if not keyring_unlocked():
        raise ValueError('GNOME Keyringが解除されていません。')
    if not secret_available():
        raise ValueError('固定属性に一致するSecret Service項目を確認できません。')
    if agent_status(runtime) != 'empty':
        raise ValueError('空の専用Agentだけに鍵を登録できます。')
    helper = Path(__file__).resolve()
    result = subprocess.run([SSH_ADD, str(private_key)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            env={'PATH': '/usr/bin:/bin', 'XDG_RUNTIME_DIR': str(runtime_path),
                                 'DBUS_SESSION_BUS_ADDRESS': f'unix:path={runtime_path}/bus',
                                 'SSH_AUTH_SOCK': str(agent_paths(runtime)[1]),
                                 'SSH_ASKPASS': str(helper), 'SSH_ASKPASS_REQUIRE': 'force'},
                            timeout=TIMEOUT, check=False)
    if result.returncode:
        detail = result.stderr.lower()
        if b'passphrase' in detail or b'bad decrypt' in detail:
            raise ValueError('保存したパスフレーズとSSH秘密鍵の組合せを確認してください。')
        if b'askpass' in detail:
            raise ValueError('Secret ServiceからSSH鍵パスフレーズを取得できません。')
        raise ValueError('専用Agentへの鍵登録に失敗しました。SSH鍵または認証補助を確認してください。')
    if agent_status(runtime) != 'ready':
        raise ValueError('専用Agentへの鍵登録に失敗しました。Secret Serviceに保存したパスフレーズとSSH秘密鍵の組合せを確認してください。')


def main():
    commands = ('prepare', 'status', 'keyring-status', 'config-status', 'secret-status', 'askpass', 'register', 'serve')
    # ssh-add invokes SSH_ASKPASS with its human-readable prompt as argv[1],
    # not with a subcommand.  Never print that prompt or any secret value.
    if len(sys.argv) > 1 and sys.argv[1] not in commands:
        try:
            return askpass()
        except (OSError, subprocess.SubprocessError, ValueError):
            return 1
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=commands)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            prepare(); payload = {'status': 'prepared'}
        elif args.command == 'status':
            payload = {'status': agent_status()}
        elif args.command == 'keyring-status':
            payload = {'status': 'unlocked' if keyring_unlocked() else 'locked'}
        elif args.command == 'config-status':
            settings(); payload = {'status': 'configured'}
        elif args.command == 'secret-status':
            payload = {'status': 'present' if secret_available() else 'missing'}
        elif args.command == 'askpass':
            return askpass()
        elif args.command == 'register':
            register(); payload = {'status': 'ready'}
        else:
            serve(); return
        print(json.dumps(payload))
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(json.dumps({'status': 'error'}))
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
