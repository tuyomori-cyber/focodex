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
from dataclasses import dataclass
from urllib.parse import urlsplit

import git_ops

BWRAP = '/usr/bin/bwrap'
TIMEOUT = 10
# A relay may be waiting in accept()/select() for up to TIMEOUT after its
# sandbox client has exited.  Do not classify that bounded cleanup wait as an
# unconfirmed operation before the relay itself has had time to observe it.
RELAY_SHUTDOWN_TIMEOUT = TIMEOUT + 1
RELAY_BUFFER = 64 * 1024
SSH_USER = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,31}$')
SSH_HOST = re.compile(
    r'^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$')


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
                '  ProxyCommand /usr/bin/python3 /run/focodex/ssh_proxy.py /run/focodex/transport.sock\n')
    return (identity +
            '  UserKnownHostsFile /run/focodex/known_hosts\n'
            '  GlobalKnownHostsFile /dev/null\n'
            '  StrictHostKeyChecking yes\n'
            '  UpdateHostKeys no\n'
            '  BatchMode yes\n'
            '  PasswordAuthentication no\n'
            '  KbdInteractiveAuthentication no\n'
            # IdentityFile none prevents private-key file discovery.  The
            # dedicated Agent is the only remaining source of identities, so
            # it must not be excluded by IdentitiesOnly=yes.
            '  IdentitiesOnly no\n'
            '  IdentityFile none\n'
            '  IdentityAgent /run/focodex/agent.sock\n'
            '  ForwardAgent no\n'
            '  ControlMaster no\n'
            '  PermitLocalCommand no\n').encode()


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
        self.accepted = False
        self.connected = False

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
        if self._closed:
            raise ValueError('中継はすでに終了しています。')
        self._listener.settimeout(timeout)
        try:
            client, _ = self._listener.accept()
            self.accepted = True
        except TimeoutError as error:
            raise TimeoutError('Sandboxからの中継接続が時間切れです。') from error
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout) as remote:
                self.connected = True
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


def minimal_passwd():
    """Supply only the invoking uid's synthetic passwd entry to SSH."""
    return f'focodex:x:{os.geteuid()}:{os.getegid()}:Focodex:/nonexistent:/sbin/nologin\n'.encode()


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
        echo_thread.join(RELAY_SHUTDOWN_TIMEOUT); relay_thread.join(RELAY_SHUTDOWN_TIMEOUT)
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
        known_file, config_file, proxy_file, passwd_file = (base / 'known_hosts', base / 'ssh_config',
                                                              base / 'ssh_proxy.py', base / 'passwd')
        known_file.write_bytes(known); config_file.write_bytes(fixed_ssh_config(target))
        proxy_file.write_text('import os,select,socket,sys\ns=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1])\nwhile True:\n r,_,_=select.select([0,s],[],[])\n if 0 in r:\n  d=os.read(0,65536)\n  if not d:s.shutdown(socket.SHUT_WR)\n  else:s.sendall(d)\n if s in r:\n  d=s.recv(65536)\n  if not d:break\n  os.write(1,d)\n')
        passwd_file.write_bytes(minimal_passwd())
        for path in (known_file, config_file, proxy_file, passwd_file): os.chmod(path, 0o600)
        with FixedTargetProxy(target.host, target.port) as proxy:
            errors = []
            thread = threading.Thread(target=lambda: _relay_once(proxy, errors), daemon=True); thread.start()
            command = relay_arguments(project) + ['--bind', str(proxy.socket_path), '/run/focodex/transport.sock',
                '--bind', str(agent), '/run/focodex/agent.sock', '--ro-bind', str(known_file), '/run/focodex/known_hosts',
                '--ro-bind', str(config_file), '/run/focodex/ssh_config', '--ro-bind', str(proxy_file), '/run/focodex/ssh_proxy.py',
                '--dir', '/etc', '--ro-bind', str(passwd_file), '/etc/passwd',
                '--setenv', 'SSH_AUTH_SOCK', '/run/focodex/agent.sock', '--setenv', 'GIT_SSH_COMMAND', '/usr/bin/ssh -F /run/focodex/ssh_config',
                '--setenv', 'GIT_CONFIG_NOSYSTEM', '1', '--setenv', 'GIT_CONFIG_GLOBAL', '/dev/null', '--setenv', 'GIT_TERMINAL_PROMPT', '0',
                '/usr/bin/git', '-C', '/work', 'ls-remote', '--exit-code', remote, merge]
            # Keep diagnostics private, then expose only a fixed failure class.
            # In particular, never return an SSH URL, host key, user name, or
            # command output to the extension.
            with tempfile.TemporaryFile() as stderr:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                    start_new_session=True, env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C.UTF-8'})
                try: process.wait(timeout=TIMEOUT)
                except subprocess.TimeoutExpired as error:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=3)
                    raise git_ops.UncertainGit('読み取り専用SSH接続試験が時間切れです。') from error
                stderr.seek(0)
                diagnostic = stderr.read(4096)
            # The relay's accept/select timeout is TIMEOUT.  A shorter join
            # here created a false "unconfirmed" result on a real SSH exit,
            # even though the bounded relay cleanup completed moments later.
            thread.join(RELAY_SHUTDOWN_TIMEOUT)
            if thread.is_alive(): raise git_ops.UncertainGit('SSH中継の終了確認に失敗しました。')
            if process.returncode or errors:
                raise ValueError(ssh_probe_failure_reason(proxy, errors, diagnostic, process.returncode))
    return {'ssh': 'passed', 'readOnly': True, 'networkShared': False}


def _relay_once(proxy, errors):
    try: proxy.serve_once()
    except Exception as error: errors.append(error)


def ssh_probe_failure_reason(proxy, errors, diagnostic, returncode=None):
    """Return a fixed, non-secret category for an SSH probe failure."""
    text = diagnostic.lower()
    if not proxy.accepted:
        return '読み取り専用SSH接続試験に失敗しました: Sandbox内SSHが中継へ接続できませんでした。'
    if not proxy.connected:
        return '読み取り専用SSH接続試験に失敗しました: 固定宛先への接続に失敗しました。'
    if b'host key verification failed' in text:
        return '読み取り専用SSH接続試験に失敗しました: 接続先のホスト鍵を確認できませんでした。'
    if any(marker in text for marker in (b'permission denied', b'no identities', b'agent refused operation')):
        return '読み取り専用SSH接続試験に失敗しました: 専用Agentによる認証に失敗しました。'
    if errors:
        return '読み取り専用SSH接続試験に失敗しました: SSH中継通信に失敗しました。'
    # git ls-remote --exit-code returns 2, with no diagnostic, if the pinned
    # ref does not exist.  Authentication and transport have already worked.
    if returncode == 2:
        return '読み取り専用SSH接続試験に失敗しました: upstreamブランチをリモートに確認できませんでした。'
    return '読み取り専用SSH接続試験に失敗しました: Sandbox内のGitまたはSSHが失敗しました。'
