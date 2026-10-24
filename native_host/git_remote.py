"""Narrow, experimental remote-Git sandbox checks. No remote operation is exposed."""
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import git_ops

BWRAP = '/usr/bin/bwrap'
TIMEOUT = 10
RELAY_BUFFER = 64 * 1024
CLOSE_POLL_SECONDS = 0.1
SSH_USER = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,31}$')
SSH_HOST = re.compile(
    r'^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$')


def remote_diagnostic_path():
    """Private, local-only summary log; it intentionally contains no inputs."""
    state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    directory = state / 'focodex'
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        status = directory.lstat()
        if (not stat.S_ISDIR(status.st_mode) or stat.S_ISLNK(status.st_mode) or
                status.st_uid != os.geteuid() or status.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
            raise OSError('unsafe diagnostic directory')
        path = directory / 'remote-ssh-diagnostics.jsonl'
        if path.exists():
            file_status = path.lstat()
            if (not stat.S_ISREG(file_status.st_mode) or stat.S_ISLNK(file_status.st_mode) or
                    file_status.st_uid != os.geteuid() or file_status.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
                raise OSError('unsafe diagnostic log')
        return path
    except OSError:
        return None


def write_remote_diagnostic(event):
    """Best-effort, bounded diagnostic telemetry that cannot block Git safety."""
    path = remote_diagnostic_path()
    if path is None:
        return
    payload = json.dumps(event, sort_keys=True, separators=(',', ':')).encode() + b'\n'
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            os.write(descriptor, payload[:1024])
        finally:
            os.close(descriptor)
    except OSError:
        pass


def proxy_status_value(path):
    try:
        value = Path(path).read_bytes().decode('ascii', 'ignore')
    except OSError:
        return 'unavailable'
    return value if value in ('', 'wrapper-config-probe', 'wrapper-command', 'started', 'connected', 'connect-failed') else 'invalid'


@dataclass(frozen=True)
class SshRemote:
    """A deliberately small SSH remote form that is safe to pin to a relay."""
    user: str
    host: str
    port: int
    path: str


def parse_ssh_remote(url):
    """Accept only an explicit ssh://user@DNS-name[:port]/repository URL.

    The caller must never use an accepted URL as a shell command.  Scp-like
    syntax, implicit local usernames, IP literals, query strings and fragments
    are deliberately unsupported until their host-key policy is designed.
    """
    if not isinstance(url, str) or not url or len(url) > 2048 or any(ord(char) < 0x20 for char in url):
        raise ValueError('許可するSSHリモートURL形式ではありません。')
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError('許可するSSHリモートURL形式ではありません。') from error
    if (parsed.scheme != 'ssh' or parsed.query or parsed.fragment or parsed.password is not None or
            not parsed.username or not parsed.hostname or not parsed.path.startswith('/') or
            parsed.path == '/' or parsed.path.startswith('//')):
        raise ValueError('許可するSSHリモートURL形式ではありません。')
    user, host = parsed.username, parsed.hostname.lower()
    if (not SSH_USER.fullmatch(user) or not SSH_HOST.fullmatch(host) or
            any(char in parsed.path for char in ('\\', '\x00', '@')) or
            any(char.isspace() for char in parsed.path)):
        raise ValueError('許可するSSHリモートURL形式ではありません。')
    return SshRemote(user=user, host=host, port=22 if port is None else port, path=parsed.path)


def upstream_ssh_remote(repo):
    """Read one configured upstream and return its pinned SSH endpoint.

    This is data validation only.  It does not invoke SSH, resolve DNS, or
    expose a remote URL to the extension.
    """
    ref, _ = repo.branch()
    branch = ref.removeprefix('refs/heads/')
    branch_key = f'branch.{branch}'.lower()
    remotes = [value for key, value in repo.settings if key == branch_key + '.remote']
    merges = [value for key, value in repo.settings if key == branch_key + '.merge']
    if (len(remotes) != 1 or len(merges) != 1 or not re.fullmatch(r'[A-Za-z0-9._-]+', remotes[0]) or
            not merges[0].startswith('refs/heads/') or len(merges[0]) == len('refs/heads/')):
        raise ValueError('現在のブランチのupstreamを一意に確認できません。')
    remote = remotes[0]
    urls = [value for key, value in repo.settings if key == f'remote.{remote}.url'.lower()]
    push_urls = [value for key, value in repo.settings if key == f'remote.{remote}.pushurl'.lower()]
    if len(urls) != 1 or push_urls:
        raise ValueError('リモートURLを一意に確認できません。')
    return remote, merges[0], parse_ssh_remote(urls[0])


def known_host_entries(host, known_hosts):
    """Return only exact ssh-keygen matches; never expose the source file."""
    if not isinstance(host, str) or not host or '\x00' in host:
        raise ValueError('接続先ホスト名を安全に扱えません。')
    path = Path(known_hosts)
    if not path.is_file() or path.is_symlink():
        raise ValueError('known_hostsを安全に読み取れません。')
    result = subprocess.run(['/usr/bin/ssh-keygen', '-F', host, '-f', str(path)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=False)
    entries = [line for line in result.stdout.splitlines() if line and not line.startswith(b'#')]
    if result.returncode or not entries:
        raise ValueError('対象ホストの既知鍵を確認できません。')
    return b'\n'.join(entries) + b'\n'


def known_host_name(target):
    return target.host if target.port == 22 else f'[{target.host}]:{target.port}'


def dedicated_agent_socket():
    """Accept only an explicitly configured, user-private remote Agent.

    Ambient SSH_AUTH_SOCK remains usable for the earlier non-network probe but
    is never silently promoted to remote Git credentials.
    """
    raw = os.environ.get('FOCODEX_REMOTE_AGENT')
    if not raw:
        raise ValueError('専用SSH Agentが設定されていません。')
    path = Path(raw)
    try:
        status = path.lstat()
    except OSError as error:
        raise ValueError('専用SSH Agentソケットを確認できません。') from error
    if (not stat.S_ISSOCK(status.st_mode) or status.st_uid != os.geteuid() or
            status.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
        raise ValueError('専用SSH Agentソケットの所有者または権限が不正です。')
    return path


def fixed_ssh_config(target=None):
    """Fixed SSH options with no inherited config or client-selected relay."""
    host = 'focodex-remote' if target is None else target.host
    identity = ('Host focodex-remote\n' if target is None else
                f'Host {target.host}\n  HostName {target.host}\n  Port {target.port}\n  User {target.user}\n'
                '  ProxyCommand /usr/bin/python3 /run/focodex/ssh_proxy.py /run/focodex/transport.sock /run/focodex/proxy-status\n')
    return (identity +
            '  UserKnownHostsFile /run/focodex/known_hosts\n'
            '  GlobalKnownHostsFile /dev/null\n'
            '  StrictHostKeyChecking yes\n'
            '  UpdateHostKeys no\n'
            '  BatchMode yes\n'
            '  PasswordAuthentication no\n'
            '  KbdInteractiveAuthentication no\n'
            '  IdentitiesOnly yes\n'
            '  IdentityFile none\n'
            '  IdentityAgent /run/focodex/agent.sock\n'
            '  ForwardAgent no\n'
            '  ControlMaster no\n'
            '  PermitLocalCommand no\n').encode()


def fixed_ssh_wrapper(target):
    """An executable fixed SSH policy, used ahead of Git-supplied arguments."""
    if not isinstance(target, SshRemote):
        raise ValueError('固定SSH設定を生成できません。')
    return ('#!/bin/sh\n'
            'case " $* " in *" -G "*) printf wrapper-config-probe > /run/focodex/proxy-status ;; '
            '*) printf wrapper-command > /run/focodex/proxy-status ;; esac\n'
            'exec /usr/bin/ssh -vv \\\n'
            f'  -o HostName={target.host} \\\n'
            f'  -o Port={target.port} \\\n'
            f'  -o User={target.user} \\\n'
            '  -o UserKnownHostsFile=/run/focodex/known_hosts \\\n'
            '  -o GlobalKnownHostsFile=/dev/null \\\n'
            '  -o StrictHostKeyChecking=yes \\\n'
            '  -o UpdateHostKeys=no \\\n'
            '  -o BatchMode=yes \\\n'
            '  -o PasswordAuthentication=no \\\n'
            '  -o KbdInteractiveAuthentication=no \\\n'
            '  -o IdentitiesOnly=yes \\\n'
            '  -o IdentityFile=none \\\n'
            '  -o IdentityAgent=/run/focodex/agent.sock \\\n'
            '  -o ForwardAgent=no \\\n'
            '  -o ControlMaster=no \\\n'
            '  -o PermitLocalCommand=no \\\n'
            "  -o 'ProxyCommand=/usr/bin/python3 /run/focodex/ssh_proxy.py /run/focodex/transport.sock /run/focodex/proxy-status' \\\n"
            '  "$@" 2>/run/focodex/ssh-stderr\n'
            'exit $?\n')


class FixedTargetProxy:
    """One Unix-socket client to one caller-fixed TCP endpoint.

    This is intentionally not connected to a Native Messaging request yet.
    Future SSH can use the Unix socket without receiving a network namespace;
    only the Host creates the TCP connection and it has no client-selected
    destination parameter.
    """

    def __init__(self, host, port):
        if not isinstance(host, str) or not host or '\x00' in host:
            raise ValueError('中継先ホスト名を安全に扱えません。')
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('中継先ポートを安全に扱えません。')
        self.host = host
        self.port = port
        self._directory = tempfile.TemporaryDirectory(prefix='focodex-remote-')
        os.chmod(self._directory.name, 0o700)
        self.socket_path = Path(self._directory.name) / 'transport.sock'
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self._listener.listen(1)
        self._closed = False
        self.client_connected = False
        self.target_connected = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._listener.close()
        self._directory.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()

    def serve_once(self, timeout=TIMEOUT):
        """Relay exactly one client; the destination cannot be supplied by it."""
        # Closing a listener in a different thread does not reliably interrupt
        # accept(2) on every supported Linux/Python combination.  Polling with
        # a short bounded timeout lets close() finish an SSH failure path
        # promptly, without treating that intentional shutdown as uncertain.
        deadline = time.monotonic() + timeout
        while not self._closed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Sandboxからの中継接続が時間切れです。')
            self._listener.settimeout(min(remaining, CLOSE_POLL_SECONDS))
            try:
                client, _ = self._listener.accept()
                self.client_connected = True
                break
            except TimeoutError:
                continue
            except OSError:
                if self._closed:
                    return
                raise
        else:
            return
        try:
            if self._closed:
                return
            with socket.create_connection((self.host, self.port), timeout=timeout) as remote:
                self.target_connected = True
                client.settimeout(timeout)
                remote.settimeout(timeout)
                self._relay(client, remote)
        finally:
            client.close()

    @staticmethod
    def _relay(left, right):
        """Bounded byte relay for one SSH transport stream."""
        while True:
            readable, _, _ = select.select((left, right), (), (), TIMEOUT)
            if not readable:
                raise TimeoutError('中継通信が時間切れです。')
            for source in readable:
                destination = right if source is left else left
                data = source.recv(RELAY_BUFFER)
                if not data:
                    try:
                        destination.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    return
                destination.sendall(data)


def sandbox_arguments(project, agent):
    root = Path(project).resolve(strict=True)
    socket = Path(agent)
    if not stat.S_ISSOCK(socket.stat().st_mode):
        raise ValueError('SSH Agentソケットを確認できません。')
    if not Path(BWRAP).is_file() or not Path('/usr/bin/ssh-add').is_file():
        raise ValueError('Sandbox試験に必要な実行ファイルがありません。')
    # Do not bind HOME, /etc, /run, or any host directory.  The agent socket
    # is the sole host runtime object visible to the child.
    return [BWRAP, '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
            '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
            '--symlink', 'usr/sbin', '/sbin', '--symlink', 'usr/lib', '/lib',
            '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev',
            '--dir', '/work', '--ro-bind', str(root), '/work', '--dir', '/run',
            '--dir', '/run/focodex', '--bind', str(socket), '/run/focodex/agent.sock',
            '--setenv', 'HOME', '/nonexistent', '--setenv', 'PATH', '/usr/bin:/bin',
            '--setenv', 'SSH_AUTH_SOCK', '/run/focodex/agent.sock', '--chdir', '/work',
            '/bin/sh', '-c',
            'test -r /work/.git/HEAD && test ! -e /home && ssh-add -l >/dev/null && ! touch /work/.focodex-sandbox-probe']


def relay_arguments(project):
    """Sandbox for the local relay probe; deliberately exposes no SSH Agent."""
    root = Path(project).resolve(strict=True)
    if not Path(BWRAP).is_file() or not Path('/usr/bin/python3').is_file():
        raise ValueError('中継試験に必要な実行ファイルがありません。')
    return [BWRAP, '--unshare-all', '--die-with-parent', '--new-session', '--clearenv',
            '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
            '--symlink', 'usr/sbin', '/sbin', '--symlink', 'usr/lib', '/lib',
            '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev',
            '--dir', '/work', '--ro-bind', str(root), '/work', '--dir', '/run',
            '--dir', '/run/focodex', '--setenv', 'HOME', '/nonexistent',
            '--setenv', 'PATH', '/usr/bin:/bin', '--chdir', '/work']


def sandbox_probe(project):
    agent = os.environ.get('SSH_AUTH_SOCK')
    if not agent:
        raise ValueError('SSH Agentが利用できません。')
    command = sandbox_arguments(project, agent)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True,
                               env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C.UTF-8'})
    try:
        output, _ = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired as error:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        except (OSError, subprocess.SubprocessError) as cleanup:
            raise git_ops.UncertainGit('Sandbox試験の終了確認に失敗しました。') from cleanup
        raise git_ops.UncertainGit('Sandbox試験が時間切れです。') from error
    if process.returncode:
        raise ValueError('Sandbox試験に失敗しました: ' + output.decode('utf-8', 'replace')[:1000])
    return {'sandbox': 'passed', 'networkShared': False, 'projectReadOnly': True,
            'hostHomeHidden': True, 'sshAgentAvailable': True}


def relay_probe(project):
    """Use a host-loopback echo only; the sandbox remains network-isolated."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(TIMEOUT)
    token, errors = os.urandom(32), []
    def echo():
        try:
            peer, _ = listener.accept()
            with peer:
                data = peer.recv(64)
                if data != token: raise ValueError('中継データが一致しません。')
                peer.sendall(data)
        except Exception as error: errors.append(error)
    with FixedTargetProxy('127.0.0.1', listener.getsockname()[1]) as proxy:
        def relay():
            try: proxy.serve_once()
            except Exception as error: errors.append(error)
        echo_thread = threading.Thread(target=echo, daemon=True)
        relay_thread = threading.Thread(target=relay, daemon=True)
        echo_thread.start(); relay_thread.start()
        command = relay_arguments(project)
        command += ['--bind', str(proxy.socket_path), '/run/focodex/transport.sock']
        script = ('import socket; s=socket.socket(socket.AF_UNIX); '
                  's.connect("/run/focodex/transport.sock"); '
                  f's.sendall({token!r}); assert s.recv(64) == {token!r}')
        command += ['/usr/bin/python3', '-c', script]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C.UTF-8'})
        try: output, _ = process.communicate(timeout=TIMEOUT)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=3)
            raise git_ops.UncertainGit('中継試験が時間切れです。') from error
        echo_thread.join(3); relay_thread.join(3)
        if echo_thread.is_alive() or relay_thread.is_alive():
            raise git_ops.UncertainGit('中継試験の終了確認に失敗しました。')
        if process.returncode or errors:
            detail = str(errors[0]) if errors else output.decode('utf-8', 'replace')[:1000]
            raise ValueError('中継試験に失敗しました: ' + detail)
    listener.close()
    return {'relay': 'passed', 'networkShared': False, 'externalNetworkUsed': False}


def readonly_ssh_probe(project):
    """One explicit, read-only ls-remote through the pinned relay."""
    repo = git_ops.Repository(project)
    remote, merge, target = upstream_ssh_remote(repo)
    agent = dedicated_agent_socket()
    known = known_host_entries(known_host_name(target), Path.home() / '.ssh/known_hosts')
    with tempfile.TemporaryDirectory(prefix='focodex-ssh-') as directory:
        base = Path(directory); os.chmod(base, 0o700)
        known_file, config_file, proxy_file, wrapper_file, status_file, stderr_file = (base / 'known_hosts', base / 'ssh_config',
                                                                                         base / 'ssh_proxy.py', base / 'ssh-wrapper',
                                                                                         base / 'proxy-status', base / 'ssh-stderr')
        known_file.write_bytes(known); config_file.write_bytes(fixed_ssh_config(target))
        wrapper_file.write_text(fixed_ssh_wrapper(target))
        status_file.write_bytes(b''); stderr_file.write_bytes(b'')
        proxy_file.write_text('import os,select,socket,sys\n'
                              'def status(v):\n'
                              ' try:open(sys.argv[2],"wb").write(v)\n'
                              ' except OSError:pass\n'
                              'status(b"started")\n'
                              's=socket.socket(socket.AF_UNIX)\n'
                              'try:s.connect(sys.argv[1])\n'
                              'except OSError:\n'
                              ' status(b"connect-failed")\n'
                              ' os.write(2,b"FOCODEX_PROXY_CONNECT_FAILED\\n")\n'
                              ' raise SystemExit(111)\n'
                              'status(b"connected")\n'
                              'def write_all(d):\n'
                              ' while d:\n'
                              '  n=os.write(1,d)\n'
                              '  d=d[n:]\n'
                              'stdin_open=True\n'
                              'while True:\n'
                              ' r,_,_=select.select(([0] if stdin_open else [])+[s],[],[])\n'
                              ' if stdin_open and 0 in r:\n'
                              '  d=os.read(0,65536)\n'
                              '  if not d:\n'
                              '   s.shutdown(socket.SHUT_WR)\n'
                              '   stdin_open=False\n'
                              '  else:s.sendall(d)\n'
                              ' if s in r:\n'
                              '  d=s.recv(65536)\n'
                              '  if not d:break\n'
                              '  write_all(d)\n')
        for path in (known_file, config_file, proxy_file, status_file, stderr_file): os.chmod(path, 0o600)
        os.chmod(wrapper_file, 0o700)
        with FixedTargetProxy(target.host, target.port) as proxy:
            errors = []
            thread = threading.Thread(target=lambda: _relay_once(proxy, errors), daemon=True); thread.start()
            command = relay_arguments(project) + ['--bind', str(proxy.socket_path), '/run/focodex/transport.sock',
                '--bind', str(agent), '/run/focodex/agent.sock', '--ro-bind', str(known_file), '/run/focodex/known_hosts',
                '--ro-bind', str(config_file), '/run/focodex/ssh_config', '--ro-bind', str(proxy_file), '/run/focodex/ssh_proxy.py',
                '--ro-bind', str(wrapper_file), '/run/focodex/ssh-wrapper',
                '--bind', str(status_file), '/run/focodex/proxy-status',
                '--bind', str(stderr_file), '/run/focodex/ssh-stderr',
                # GIT_SSH_COMMAND outranks a repository's core.sshCommand;
                # GIT_SSH does not.  The wrapper is a fixed single pathname.
                '--setenv', 'SSH_AUTH_SOCK', '/run/focodex/agent.sock', '--setenv', 'GIT_SSH_COMMAND', '/run/focodex/ssh-wrapper',
                # The command is the pinned OpenSSH binary, but Git's
                # heuristic can otherwise classify a command-with-options as
                # a generic SSH client.  Pin its documented OpenSSH variant.
                '--setenv', 'GIT_SSH_VARIANT', 'ssh',
                '--setenv', 'GIT_CONFIG_NOSYSTEM', '1', '--setenv', 'GIT_CONFIG_GLOBAL', '/dev/null', '--setenv', 'GIT_TERMINAL_PROMPT', '0',
                '/usr/bin/git', '-c', 'core.sshCommand=/run/focodex/ssh-wrapper',
                '-C', '/work', 'ls-remote', '--exit-code', remote, merge]
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                start_new_session=True, env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C.UTF-8'})
            try: process.wait(timeout=TIMEOUT)
            except subprocess.TimeoutExpired as error:
                os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=3)
                write_remote_diagnostic({'event': 'ssh-probe', 'outcome': 'timeout',
                                         'relayAccepted': proxy.client_connected,
                                         'targetConnected': proxy.target_connected,
                                         'proxyStatus': proxy_status_value(status_file)})
                raise git_ops.UncertainGit('読み取り専用SSH接続試験が時間切れです。') from error
            git_stderr = process.stderr.read(4096) if process.stderr else b''
            try:
                ssh_stderr = stderr_file.read_bytes()[:4096]
            except OSError:
                ssh_stderr = b''
            stderr = ssh_stderr + b'\n' + git_stderr
            proxy_status = proxy_status_value(status_file)
            # If SSH failed before its ProxyCommand connected, do not leave the
            # one-shot relay waiting until its independent accept timeout.
            proxy.close()
            thread.join(3)
            if thread.is_alive():
                write_remote_diagnostic({'event': 'ssh-probe', 'outcome': 'relay-exit-unconfirmed',
                                         'returnCode': process.returncode,
                                         'relayAccepted': proxy.client_connected,
                                         'targetConnected': proxy.target_connected, 'proxyStatus': proxy_status})
                raise git_ops.UncertainGit('SSH中継の終了確認に失敗しました。')
            if errors:
                # The fixed relay reached the only place that can open a TCP
                # connection.  Do not include its exception: it may contain
                # host or socket details, but distinguish it from SSH auth.
                write_remote_diagnostic({'event': 'ssh-probe', 'outcome': 'relay-error',
                                         'returnCode': process.returncode,
                                         'relayAccepted': proxy.client_connected,
                                         'targetConnected': proxy.target_connected, 'proxyStatus': proxy_status})
                raise ValueError('読み取り専用SSH接続試験に失敗しました。固定宛先への中継接続を確認してください。')
            if process.returncode:
                write_remote_diagnostic({'event': 'ssh-probe', 'outcome': 'ssh-error',
                                         'returnCode': process.returncode,
                                         'relayAccepted': proxy.client_connected,
                                         'targetConnected': proxy.target_connected, 'proxyStatus': proxy_status,
                                         'classification': ssh_probe_classification(stderr, process.returncode)})
                raise ValueError(ssh_probe_error(stderr, process.returncode))
            write_remote_diagnostic({'event': 'ssh-probe', 'outcome': 'passed', 'returnCode': 0,
                                     'relayAccepted': proxy.client_connected,
                                     'targetConnected': proxy.target_connected, 'proxyStatus': proxy_status})
    return {'ssh': 'passed', 'readOnly': True, 'networkShared': False}


def _relay_once(proxy, errors):
    try: proxy.serve_once()
    except Exception as error: errors.append(error)


def ssh_probe_classification(stderr, returncode=None):
    """Stable categories for the private summary log; no SSH text is retained."""
    detail = stderr.lower()
    if b'focodex_proxy_connect_failed' in detail:
        return 'proxy-connect'
    if b'executing proxy command' in detail:
        return 'proxy-command'
    if b'connecting to ' in detail:
        return 'proxy-bypassed'
    if b'bad configuration option' in detail or b'unknown option' in detail or b'illegal option' in detail:
        return 'ssh-config'
    if returncode == 2:
        return 'upstream-ref'
    if b'host key verification' in detail or b'no matching host key type' in detail:
        return 'host-key'
    if b'permission denied' in detail or b'agent refused operation' in detail:
        return 'agent-auth'
    if (b'repository not found' in detail or b'could not read from remote repository' in detail or
            b'permission to ' in detail):
        return 'repository-access'
    if (b'connection timed out' in detail or b'connection refused' in detail or b'could not resolve' in detail or
            b'connection reset' in detail or b'connection closed' in detail or b'kex_exchange' in detail):
        return 'target-connection'
    if b'askpass' in detail or b'agent' in detail:
        return 'agent-setup'
    return 'unclassified'


def ssh_probe_error(stderr, returncode=None):
    """Classify SSH's bounded stderr without exposing host, URL, or key data."""
    classification = ssh_probe_classification(stderr, returncode)
    # git ls-remote --exit-code reserves 2 for a reachable remote that has no
    # matching ref.  This is not an SSH transport failure.
    if classification == 'upstream-ref':
        return '読み取り専用SSH接続試験に失敗しました。upstreamの追跡ブランチを確認してください。'
    if classification == 'proxy-connect':
        return '読み取り専用SSH接続試験に失敗しました。Sandbox内の固定中継ソケットを確認してください。'
    if classification == 'proxy-command':
        return '読み取り専用SSH接続試験に失敗しました。固定中継でのSSH接続を確認してください。'
    if classification == 'proxy-bypassed':
        return '読み取り専用SSH接続試験に失敗しました。固定中継設定が適用されていません。'
    if classification == 'ssh-config':
        return '読み取り専用SSH接続試験に失敗しました。固定SSH設定を確認してください。'
    if classification == 'host-key':
        return '読み取り専用SSH接続試験に失敗しました。接続先の既知鍵を確認してください。'
    if classification == 'agent-auth':
        return '読み取り専用SSH接続試験に失敗しました。専用Agentの鍵または接続先制約を確認してください。'
    if classification == 'repository-access':
        return '読み取り専用SSH接続試験に失敗しました。GitHub上のリポジトリアクセスを確認してください。'
    if classification == 'target-connection':
        return '読み取り専用SSH接続試験に失敗しました。固定宛先への接続を確認してください。'
    if classification == 'agent-setup':
        return '読み取り専用SSH接続試験に失敗しました。専用Agentの認証準備を確認してください。'
    return '読み取り専用SSH接続試験に失敗しました。SSHまたは固定中継を確認してください。'
