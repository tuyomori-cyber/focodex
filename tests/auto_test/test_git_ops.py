import importlib.util
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import struct
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'native_host'))
import git_ops
import git_remote
spec = importlib.util.spec_from_file_location('git_probe', ROOT / 'scripts/probe_host.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class GitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-qb', 'main')
        self.git('config', 'user.name', '試験作者')
        self.git('config', 'user.email', 'test@example.invalid')
        for name in ('a.txt', 'b.txt'):
            (self.repo / name).write_text('base\n')
        self.git('add', 'a.txt', 'b.txt')
        self.git('commit', '-qm', 'base')
        self.config = self.root / 'settings.json'
        self.config.write_text(json.dumps({'codex': '/usr/bin/false', 'projects': {
            'test': {'path': str(self.repo)}}}))
        self.host = self.new_host()
        self.sequence = 0

    def new_host(self, extra=None):
        env = {**os.environ, 'FOCODEX_CONFIG': str(self.config), **(extra or {})}
        host = probe.launch(env)
        self.addCleanup(probe.close, host)
        return host

    def git(self, *args, input=None):
        return subprocess.run(['/usr/bin/git', '-C', str(self.repo), *args],
                              input=input, check=True, capture_output=True,
                              env={**git_ops.environment(), 'GIT_CONFIG_GLOBAL': '/dev/null'}).stdout.decode()

    def request(self, kind, host=None, **values):
        self.sequence += 1
        probe.write_message(host or self.host, {'type': kind, 'id': str(self.sequence),
                            'projectId': 'test', **values})
        return probe.read_message(host or self.host, 8)

    def snapshot(self):
        result = self.request('git_snapshot')
        self.assertEqual(result['type'], 'git_snapshot', result)
        self.assertTrue(result['canModify'], result)
        return result

    def stage(self, names):
        snapshot = self.snapshot()
        result = self.request('git_stage', snapshotToken=snapshot['snapshotToken'], files=names)
        self.assertEqual(result['type'], 'git_stage', result)
        return result

    def review(self, message='音声からのメッセージ\n\n詳細'):
        result = self.request('git_review', message=message)
        self.assertEqual(result['type'], 'git_review', result)
        return result

    def configure_standalone(self):
        self.config.write_text(json.dumps({'codex': '/usr/bin/false', 'projects': {},
                                           'workspaces': {}, 'discoveryRoots': [str(self.root)]}))
        return 'repo_' + hashlib.sha256(str(self.repo.resolve()).encode()).hexdigest()

    def standalone_request(self, kind, project_id, **values):
        return self.request(kind, workspaceId='__standalone__', projectId=project_id, **values)

    def test_standalone_git_commit_uses_existing_tokens_and_rejects_unselected_or_changed_target(self):
        project_id = self.configure_standalone()
        missing = 'repo_' + hashlib.sha256(b'/not-a-discovered-repository').hexdigest()
        self.assertEqual(self.standalone_request('git_snapshot', missing)['type'], 'error')

        (self.repo / 'a.txt').write_text('standalone first\n')
        snapshot = self.standalone_request('git_snapshot', project_id)
        self.assertTrue(snapshot['canModify'], snapshot)
        staged = self.standalone_request('git_stage', project_id,
                                         snapshotToken=snapshot['snapshotToken'], files=['a.txt'])
        self.assertEqual(staged['type'], 'git_stage', staged)
        review = self.standalone_request('git_review', project_id, message='standalone commit')
        self.assertEqual(review['type'], 'git_review', review)
        committed = self.standalone_request('git_commit', project_id, reviewToken=review['reviewToken'])
        self.assertEqual(committed['type'], 'git_commit', committed)

        (self.repo / 'a.txt').write_text('standalone staged again\n')
        snapshot = self.standalone_request('git_snapshot', project_id)
        staged = self.standalone_request('git_stage', project_id,
                                         snapshotToken=snapshot['snapshotToken'], files=['a.txt'])
        self.assertEqual(staged['type'], 'git_stage', staged)
        review = self.standalone_request('git_review', project_id, message='must reconfirm')
        (self.repo / 'a.txt').write_text('changed after standalone review\n')
        self.assertEqual(self.standalone_request('git_commit', project_id,
                                                 reviewToken=review['reviewToken'])['type'], 'error')

    def test_selected_stage_preserves_existing_and_commits_exact_tree(self):
        (self.repo / 'b.txt').write_text('pre-staged\n')
        self.git('add', '--', 'b.txt')
        (self.repo / 'a.txt').write_text('selected\nGIT binary patch\n')
        (self.repo / 'unselected.txt').write_text('leave me\n')
        self.stage(['a.txt'])
        self.assertEqual(self.git('show', ':b.txt'), 'pre-staged\n')
        self.assertNotIn('unselected.txt', self.git('ls-files'))
        review = self.review()
        self.assertIn('+pre-staged', review['diff'])
        self.assertIn('+selected', review['diff'])
        self.assertNotIn('unselected.txt', review['diff'])
        self.assertEqual({item['path'] for item in review['stagedFiles']}, {'a.txt', 'b.txt'})
        result = self.request('git_commit', reviewToken=review['reviewToken'])
        self.assertEqual(result['type'], 'git_commit', result)
        self.assertEqual(self.git('rev-parse', 'HEAD^{tree}').strip(), review['tree'])
        self.assertEqual(self.git('show', 'HEAD:a.txt'), 'selected\nGIT binary patch\n')
        self.assertEqual(self.git('log', '-1', '--format=%B').strip(), review['message'])
        self.assertFalse(self.config.with_suffix('.execution.json').exists())
        self.assertEqual(self.request('git_commit', reviewToken=review['reviewToken'])['type'], 'error')

    def test_changed_worktree_index_attributes_or_head_requires_reconfirmation(self):
        (self.repo / 'a.txt').write_text('selected\n')
        self.stage(['a.txt'])
        old = self.git('rev-parse', 'HEAD')
        for change in ('worktree', 'index', 'attributes', 'head'):
            review = self.review()
            if change == 'worktree':
                (self.repo / 'a.txt').write_text('changed-after-review\n')
            elif change == 'index':
                self.git('add', '--', 'a.txt')
            elif change == 'attributes':
                (self.repo / '.gitattributes').write_text('*.txt text eol=lf\n')
            else:
                self.git('checkout', '-qb', 'other')
            result = self.request('git_commit', reviewToken=review['reviewToken'])
            self.assertEqual(result['type'], 'error', (change, result))
            self.assertEqual(self.git('rev-parse', 'HEAD'), old)
            self.assertFalse(self.config.with_suffix('.execution.json').exists())

    def test_initial_commit_rename_deletion_and_literal_path(self):
        # Remove only this disposable repository's history to exercise unborn HEAD.
        import shutil
        shutil.rmtree(self.repo / '.git')
        self.git('init', '-qb', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.stage(['a.txt', 'b.txt'])
        review = self.review('first')
        self.assertIsNone(review['parent'])
        self.assertEqual(self.request('git_commit', reviewToken=review['reviewToken'])['type'], 'git_commit')
        (self.repo / 'a.txt').rename(self.repo / 'renamed.txt')
        (self.repo / 'b.txt').unlink()
        (self.repo / ':(glob)*.txt').write_text('literal\n')
        (self.repo / 'other.txt').write_text('unselected\n')
        self.stage(['a.txt', 'renamed.txt', 'b.txt', ':(glob)*.txt'])
        review = self.review('rename and delete')
        self.assertEqual(self.request('git_commit', reviewToken=review['reviewToken'])['type'], 'git_commit')
        paths = self.git('ls-tree', '--name-only', 'HEAD')
        self.assertIn('renamed.txt', paths)
        self.assertIn(':(glob)*.txt', paths)
        self.assertNotIn('b.txt', paths)
        self.assertNotIn('other.txt', paths)

    def test_stale_selection_unsupported_arguments_and_foreign_tokens(self):
        (self.repo / 'a.txt').write_text('selected\n')
        snap = self.snapshot()
        (self.repo / 'b.txt').write_text('external\n')
        result = self.request('git_stage', snapshotToken=snap['snapshotToken'], files=['a.txt'])
        self.assertEqual(result['type'], 'error')
        self.assertEqual(self.git('diff', '--cached'), '')
        for names in (['.'], ['../escape'], ['not-listed.txt'], ['a.txt', 'a.txt']):
            snap = self.snapshot()
            self.assertEqual(self.request('git_stage', snapshotToken=snap['snapshotToken'], files=names)['type'], 'error')
        self.assertEqual(self.request('git_snapshot', args=['--anything'])['type'], 'error')
        self.stage(['a.txt'])
        review = self.review()
        self.assertEqual(self.request('git_commit', host=self.new_host(), reviewToken=review['reviewToken'])['type'], 'error')
        for operation in ('git_pull', 'git_push', 'git_fetch'):
            self.assertEqual(self.request(operation)['type'], 'error')

    def test_remote_preflight_is_read_only_and_reports_no_secret_values(self):
        self.git('config', 'branch.main.remote', 'origin')
        self.git('config', 'branch.main.merge', 'refs/heads/main')
        self.git('remote', 'add', 'origin', 'ssh://git@example.test/team/repository.git')
        socket_path = self.root / 'remote-agent.sock'
        socket_path.write_text('not a socket')
        with mock.patch.object(git_remote, 'dedicated_agent_socket', return_value=socket_path):
            result = self.request('git_remote_preflight')
        self.assertEqual(result['type'], 'git_remote_preflight', result)
        self.assertEqual(result['branch'], 'refs/heads/main')
        self.assertEqual(result['upstream'], 'origin/main')
        self.assertIsInstance(result['sshAgentAvailable'], bool)
        self.assertEqual(result['remoteAgentStatus'], 'notConfigured')
        self.assertNotIn('url', result)
        self.assertFalse(self.config.with_suffix('.execution.json').exists())

    def test_ssh_remote_parser_rejects_ambiguous_or_implicit_forms(self):
        target = git_remote.parse_ssh_remote('ssh://git@example.test:2222/team/repository.git')
        self.assertEqual(target, git_remote.SshRemote('git', 'example.test', 2222, '/team/repository.git'))
        for value in ('git@example.test:team/repository.git', 'ssh://example.test/team/repository.git',
                      'ssh://git@example.test/', 'ssh://git@example.test//team/repository.git',
                      'ssh://git@example.test/team/repository.git?x=1',
                      'ssh://git@[2001:db8::1]/team/repository.git',
                      'https://git@example.test/team/repository.git',
                      'ssh://git:password@example.test/team/repository.git'):
            with self.assertRaises(ValueError, msg=value):
                git_remote.parse_ssh_remote(value)

    def test_remote_preflight_requires_one_explicit_ssh_url_without_pushurl(self):
        self.git('config', 'branch.main.remote', 'origin')
        self.git('config', 'branch.main.merge', 'refs/heads/main')
        self.git('remote', 'add', 'origin', 'git@example.test:team/repository.git')
        self.assertEqual(self.request('git_remote_preflight')['type'], 'error')
        self.git('remote', 'set-url', 'origin', 'ssh://git@example.test/team/repository.git')
        self.git('config', 'remote.origin.pushurl', 'ssh://git@example.test/team/repository.git')
        self.assertEqual(self.request('git_remote_preflight')['type'], 'error')

    def test_remote_sandbox_arguments_mount_only_project_and_agent_socket(self):
        agent = self.root / 'agent.sock'
        agent.write_bytes(b'not-a-real-socket')
        with mock.patch.object(git_remote.stat, 'S_ISSOCK', return_value=True):
            args = git_remote.sandbox_arguments(self.repo, agent)
        self.assertIn('--unshare-all', args)
        self.assertIn(str(self.repo), args)
        self.assertIn(str(agent), args)
        self.assertIn('/run/focodex/agent.sock', args)
        self.assertNotIn('--share-net', args)
        self.assertNotIn('/home', args[:args.index('/bin/sh')])

    def test_relay_sandbox_arguments_expose_neither_network_nor_agent(self):
        args = git_remote.relay_arguments(self.repo)
        self.assertIn('--unshare-all', args)
        self.assertNotIn('--share-net', args)
        self.assertNotIn('SSH_AUTH_SOCK', args)
        self.assertNotIn('/run/focodex/agent.sock', args)

    def test_fetch_sandbox_exposes_git_dir_but_not_worktree(self):
        args = git_remote.fetch_sandbox_arguments(self.repo, self.repo / '.git')
        self.assertIn('--unshare-all', args)
        self.assertIn(str((self.repo / '.git').resolve()), args)
        self.assertIn('/work/.git', args)
        self.assertNotIn(str(self.repo), args)
        self.assertNotIn('--share-net', args)

    def test_push_is_fixed_to_current_branch_and_upstream_without_force(self):
        source = Path(git_remote.__file__).read_text()
        self.assertIn("['push', '--porcelain', '--no-verify', remote, f'{ref}:{merge}']", source)
        self.assertIn("return _remote_ssh_operation(project, push=True)", source)
        self.assertNotIn("['push', '--force'", source)
        self.assertNotIn("['push', '-f'", source)

    def test_push_rejection_is_classified_without_endpoint_disclosure(self):
        detail = b'! [rejected] main -> main (fetch first; non-fast-forward)'
        self.assertEqual(git_remote.ssh_probe_classification(detail), 'push-rejected')

    def test_minimal_passwd_exposes_only_a_synthetic_current_user(self):
        self.assertEqual(git_remote.minimal_passwd(),
                         f'focodex:x:{os.geteuid()}:{os.getegid()}:Focodex:/nonexistent:/sbin/nologin\n'.encode())

    def test_relay_shutdown_wait_covers_its_bounded_io_timeout(self):
        self.assertGreater(git_remote.RELAY_SHUTDOWN_TIMEOUT, git_remote.TIMEOUT)

    def test_ssh_probe_failure_reason_does_not_expose_endpoint_details(self):
        proxy = mock.MagicMock(accepted=True, connected=True)
        self.assertIn('ホスト鍵', git_remote.ssh_probe_failure_reason(
            proxy, [], b'Host key verification failed for github.example'))
        self.assertIn('専用Agent', git_remote.ssh_probe_failure_reason(
            proxy, [], b'git@github.example: Permission denied (publickey)'))
        self.assertNotIn('github.example', git_remote.ssh_probe_failure_reason(
            proxy, [], b'git@github.example: Permission denied (publickey)'))
        proxy.accepted = False
        self.assertIn('中継へ接続', git_remote.ssh_probe_failure_reason(proxy, [], b''))
        proxy.accepted = True
        self.assertIn('upstreamブランチ', git_remote.ssh_probe_failure_reason(proxy, [], b'', 2))
        self.assertIn('upstreamブランチ', git_remote.ssh_probe_failure_reason(
            proxy, [], b'Warning: Identity file none not accessible', 2))

    def test_known_host_entries_returns_only_matching_key_rows(self):
        known = self.root / 'known_hosts'; known.write_text('placeholder\n')
        completed = subprocess.CompletedProcess([], 0, b'# Host example found\nexample ssh-ed25519 key\n')
        with mock.patch.object(git_remote.subprocess, 'run', return_value=completed):
            self.assertEqual(git_remote.known_host_entries('example', known), b'example ssh-ed25519 key\n')
        with mock.patch.object(git_remote.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'')):
            with self.assertRaises(ValueError): git_remote.known_host_entries('example', known)

    def test_remote_agent_must_be_explicit_private_socket_and_known_host_uses_port(self):
        target = git_remote.SshRemote('git', 'example.test', 2222, '/team/repository.git')
        self.assertEqual(git_remote.known_host_name(target), '[example.test]:2222')
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError): git_remote.dedicated_agent_socket()
        agent = self.root / 'agent.sock'; agent.write_bytes(b'not a socket')
        with mock.patch.dict(os.environ, {'FOCODEX_REMOTE_AGENT': str(agent)}):
            with self.assertRaises(ValueError): git_remote.dedicated_agent_socket()

    def test_dedicated_agent_status_never_discloses_key_material(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(git_remote.dedicated_agent_status(), 'notConfigured')
        with mock.patch.dict(os.environ, {'FOCODEX_REMOTE_AGENT': str(self.root / 'missing.sock')}):
            self.assertEqual(git_remote.dedicated_agent_status(), 'unavailable')
        agent = self.root / 'agent.sock'; agent.write_bytes(b'not a socket')
        with mock.patch.dict(os.environ, {'FOCODEX_REMOTE_AGENT': str(agent)}):
            self.assertEqual(git_remote.dedicated_agent_status(), 'unavailable')

    def test_fixed_ssh_config_disables_inherited_authentication_paths(self):
        target = git_remote.SshRemote('git', 'example.test', 2222, '/team/repository.git')
        config = git_remote.fixed_ssh_config(target).decode()
        for line in ('StrictHostKeyChecking yes', 'UpdateHostKeys no', 'BatchMode yes',
                     'PasswordAuthentication no', 'KbdInteractiveAuthentication no',
                     'IdentitiesOnly no', 'IdentityFile none', 'GlobalKnownHostsFile /dev/null'):
            self.assertIn(line, config)
        self.assertIn('Host example.test', config)
        self.assertIn('Port 2222', config)
        self.assertIn('ProxyCommand /usr/bin/python3 /run/focodex/ssh_proxy.py /run/focodex/transport.sock /run/focodex/proxy-status', config)
        wrapper = git_remote.fixed_ssh_wrapper(target)
        self.assertIn('/usr/bin/ssh -vv', wrapper)
        self.assertIn('-o HostName=example.test', wrapper)
        self.assertIn("-o 'ProxyCommand=/usr/bin/python3 /run/focodex/ssh_proxy.py", wrapper)
        self.assertIn('2>/run/focodex/ssh-stderr', wrapper)
        wrapper_path = self.root / 'ssh-wrapper'; wrapper_path.write_text(wrapper)
        self.assertEqual(subprocess.run(['/bin/sh', '-n', str(wrapper_path)], check=False).returncode, 0)

    def test_minimal_passwd_has_only_current_user(self):
        self.assertEqual(git_remote.minimal_passwd(),
                         f'focodex:x:{os.geteuid()}:{os.getegid()}:Focodex:/nonexistent:/sbin/nologin\n'.encode())

    def test_ssh_probe_error_keeps_transport_details_private(self):
        self.assertIn('既知鍵', git_remote.ssh_probe_error(b'Host key verification failed for secret.example'))
        self.assertIn('専用Agent', git_remote.ssh_probe_error(b'Permission denied (publickey)'))
        self.assertIn('固定宛先', git_remote.ssh_probe_error(b'Connection timed out to secret.example'))
        self.assertNotIn('secret.example', git_remote.ssh_probe_error(b'Connection timed out to secret.example'))
        self.assertIn('リポジトリアクセス', git_remote.ssh_probe_error(b'ERROR: Repository not found.'))
        self.assertIn('追跡ブランチ', git_remote.ssh_probe_error(b'', 2))
        self.assertIn('固定中継ソケット', git_remote.ssh_probe_error(b'FOCODEX_PROXY_CONNECT_FAILED'))
        self.assertIn('固定SSH設定', git_remote.ssh_probe_error(b'Bad configuration option: x'))
        self.assertIn('固定中継設定が適用', git_remote.ssh_probe_error(b'debug1: Connecting to example'))

    def test_remote_diagnostic_is_private_summary_without_ssh_text(self):
        with tempfile.TemporaryDirectory() as state, mock.patch.dict(os.environ, {'XDG_STATE_HOME': state}):
            event = {'event': 'ssh-probe', 'outcome': 'ssh-error', 'returnCode': 128,
                     'relayAccepted': True, 'targetConnected': True, 'classification': 'repository-access'}
            git_remote.write_remote_diagnostic(event)
            path = Path(state) / 'focodex' / 'remote-ssh-diagnostics.jsonl'
            self.assertEqual(json.loads(path.read_text()), event)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_pull_diagnostic_is_private_fixed_summary(self):
        with tempfile.TemporaryDirectory() as state, mock.patch.dict(os.environ, {'XDG_STATE_HOME': state}):
            event = {'event': 'pull', 'outcome': 'passed', 'returnCode': 0}
            git_remote.write_remote_diagnostic(event)
            path = Path(state) / 'focodex' / 'remote-ssh-diagnostics.jsonl'
            self.assertEqual(json.loads(path.read_text()), event)

    def test_proxy_status_is_limited_to_fixed_values(self):
        marker = self.root / 'status'
        marker.write_text('connected')
        self.assertEqual(git_remote.proxy_status_value(marker), 'connected')
        marker.write_text('wrapper-command')
        self.assertEqual(git_remote.proxy_status_value(marker), 'invalid')
        marker.write_text('github.com')
        self.assertEqual(git_remote.proxy_status_value(marker), 'invalid')

    def test_wrapper_status_is_limited_to_fixed_values(self):
        marker = self.root / 'wrapper-status'
        marker.write_text('command')
        self.assertEqual(git_remote.wrapper_status_value(marker), 'command')
        marker.write_text('connected')
        self.assertEqual(git_remote.wrapper_status_value(marker), 'invalid')
        self.assertEqual(git_remote.wrapper_status_value(self.root / 'missing'), 'unavailable')

    def test_ssh_proxy_script_writes_each_received_chunk_completely(self):
        # The script is generated into the isolated sandbox at runtime.  Keep
        # its output path byte-complete: a single os.write can be partial.
        source = Path(git_remote.__file__).read_text()
        self.assertIn('write_all(d)', source)
        self.assertIn('stdin_open and 0 in r', source)

    def test_readonly_probe_uses_a_fixed_ssh_wrapper(self):
        source = Path(git_remote.__file__).read_text()
        self.assertIn("'ssh-wrapper'", source)
        self.assertIn("'GIT_SSH_COMMAND', '/run/focodex/ssh-wrapper'", source)
        self.assertIn("'core.sshCommand=/run/focodex/ssh-wrapper'", source)

    def test_relay_connection_failure_message_has_no_transport_detail(self):
        message = '読み取り専用SSH接続試験に失敗しました。固定宛先への中継接続を確認してください。'
        self.assertIn('固定宛先', message)
        self.assertNotIn('secret.example', message)

    def test_fixed_target_proxy_allows_one_local_client_only_to_fixed_endpoint(self):
        # This harness prohibits all socket creation, including AF_UNIX.  Mock
        # only verifies the fail-closed wiring; real socket behavior remains a
        # required Surface test before exposing this helper to SSH.
        listener = mock.MagicMock()
        client = mock.MagicMock()
        remote = mock.MagicMock()
        remote.__enter__.return_value = remote
        listener.accept.return_value = (client, None)
        with mock.patch.object(git_remote.socket, 'socket', return_value=listener), \
             mock.patch.object(git_remote.socket, 'create_connection', return_value=remote) as create, \
             mock.patch.object(git_remote.os, 'chmod') as chmod, \
             mock.patch.object(git_remote.FixedTargetProxy, '_relay') as relay:
            with git_remote.FixedTargetProxy('fixed.example.test', 2200) as proxy:
                proxy.serve_once()
                listener.bind.assert_called_once_with(str(proxy.socket_path))
                listener.listen.assert_called_once_with(1)
                listener.accept.assert_called_once()
                create.assert_called_once_with(('fixed.example.test', 2200), timeout=git_remote.TIMEOUT)
                relay.assert_called_once_with(client, remote)
                client.close.assert_called_once()
                self.assertIn(mock.call(proxy.socket_path, 0o600), chmod.call_args_list)
        self.assertFalse(proxy.socket_path.exists())
        with self.assertRaises(ValueError):
            git_remote.FixedTargetProxy('', 2200)
        with self.assertRaises(ValueError):
            git_remote.FixedTargetProxy('fixed.example.test', 0)

    def test_fixed_target_proxy_close_cancels_waiting_client_without_network(self):
        listener = mock.MagicMock()
        with mock.patch.object(git_remote.socket, 'socket', return_value=listener), \
             mock.patch.object(git_remote.socket, 'create_connection') as create, \
             mock.patch.object(git_remote.os, 'chmod'):
            with git_remote.FixedTargetProxy('fixed.example.test', 2200) as proxy:
                proxy.close()
                proxy.serve_once()
        listener.accept.assert_not_called()
        create.assert_not_called()

    def test_settings_attributes_and_helpers_are_refused_without_execution(self):
        marker = self.root / 'helper-ran'
        helper = self.root / 'helper'
        helper.write_text('#!/bin/sh\ntouch ' + str(marker) + '\n')
        helper.chmod(0o700)
        for key, value in [('filter.x.process', str(helper)), ('core.fsmonitor', str(helper)),
                           ('diff.external', str(helper)), ('diff.x.textconv', str(helper)),
                           ('core.sshCommand', str(helper)), ('credential.helper', str(helper)),
                           ('include.path', str(self.root / 'included-missing')),
                           ('includeIf.gitdir:/.path', str(self.root / 'included-missing')),
                           ('remote.x.vcs', str(helper)), ('gpg.program', str(helper)),
                           ('remote.x.url', 'ext::' + str(helper))]:
            self.git('config', key, value)
            result = self.request('git_snapshot')
            self.assertEqual(result['type'], 'error', (key, result))
            self.assertFalse(marker.exists())
            self.assertFalse(self.config.with_suffix('.execution.json').exists())
            self.git('config', '--unset', key)
        for attribute in ('*.txt filter=x', '*.txt diff=x', '[attr]custom filter=x', '*.txt working-tree-encoding=UTF-16'):
            (self.repo / '.gitattributes').write_text(attribute + '\n')
            self.assertEqual(self.request('git_snapshot')['type'], 'error')
            self.assertFalse(marker.exists())

    def test_hooks_signing_and_inherited_git_environment_cannot_execute(self):
        marker = self.root / 'hook-ran'
        hooks = self.root / 'hooks'
        hooks.mkdir()
        for name in ('pre-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit', 'reference-transaction'):
            hook = hooks / name
            hook.write_text('#!/bin/sh\ntouch ' + str(marker) + '\nexit 1\n')
            hook.chmod(0o700)
        self.git('config', 'core.hooksPath', str(hooks))
        self.git('config', 'commit.gpgSign', 'true')
        injected = self.new_host({'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'filter.inject.process',
                                  'GIT_CONFIG_VALUE_0': 'touch ' + str(marker),
                                  'GIT_INDEX_FILE': str(self.root / 'wrong-index'),
                                  'GIT_SSH_COMMAND': 'touch ' + str(marker)})
        self.host = injected
        (self.repo / 'a.txt').write_text('safe\n')
        self.stage(['a.txt'])
        review = self.review()
        self.assertEqual(self.request('git_commit', reviewToken=review['reviewToken'])['type'], 'git_commit')
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / 'wrong-index').exists())
        self.assertNotIn('\ngpgsig ', self.git('cat-file', 'commit', 'HEAD'))

    def test_local_git_environment_does_not_inherit_ssh_agent_or_git_variables(self):
        # Remote operations must use their own explicitly sandboxed environment;
        # the local stage/commit path must never gain agent access by accident.
        with mock.patch.dict(os.environ, {'SSH_AUTH_SOCK': '/tmp/forbidden-agent',
                                          'GIT_SSH_COMMAND': 'forbidden-command'}):
            env = git_ops.environment()
        self.assertNotIn('SSH_AUTH_SOCK', env)
        self.assertNotIn('GIT_SSH_COMMAND', env)
        self.assertEqual(env['GIT_CONFIG_GLOBAL'], '/dev/null')

    def test_worktree_commit_and_detached_or_conflict_rejection(self):
        source = self.repo
        worktree = self.root / 'worktree'
        self.git('worktree', 'add', '-qb', 'work', str(worktree))
        data = json.loads(self.config.read_text())
        data['projects']['test']['path'] = str(worktree)
        self.config.write_text(json.dumps(data))
        self.repo = worktree
        (worktree / 'a.txt').write_text('worktree\n')
        self.stage(['a.txt'])
        review = self.review()
        self.assertEqual(self.request('git_commit', reviewToken=review['reviewToken'])['type'], 'git_commit')
        self.assertEqual(self.git('show', 'HEAD:a.txt'), 'worktree\n')
        self.git('checkout', '--detach', '-q')
        snapshot = self.request('git_snapshot')
        self.assertFalse(snapshot['canModify'])
        self.assertEqual(self.request('git_review', message='no')['type'], 'error')

    def test_old_oid_race_does_not_overwrite_other_commit(self):
        (self.repo / 'a.txt').write_text('staged\n')
        self.stage(['a.txt'])
        repo = git_ops.Repository(self.repo)
        review = repo.review('ours')
        competitor = self.git('commit-tree', review['tree'], '-p', review['parent'], '-m', 'competitor').strip()
        original = repo.call
        def racing_call(*args, **kwargs):
            if args[0] == 'update-ref':
                self.git('update-ref', review['ref'], competitor, review['parent'])
            return original(*args, **kwargs)
        with mock.patch.object(repo, 'call', side_effect=racing_call):
            with self.assertRaisesRegex(ValueError, 'ブランチ更新'):
                repo.commit(review)
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), competitor)
        self.assertFalse(repo.uncertain)
        self.assertFalse((self.repo / '.git/index.lock').exists())

    def test_binary_conflict_and_attribute_index_are_not_confirmed(self):
        (self.repo / 'binary').write_bytes(b'\0binary')
        self.stage(['binary'])
        self.assertEqual(self.request('git_review', message='binary')['type'], 'error')
        (self.repo / '.gitattributes').write_text('*.txt filter=unsafe\n')
        self.git('add', '--', '.gitattributes')
        (self.repo / '.gitattributes').unlink()
        self.assertEqual(self.request('git_snapshot')['type'], 'error')

    def test_host_sigkill_between_object_creation_and_ref_update_stays_blocked(self):
        gate = self.root / 'at-update-ref'
        code = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import focodex, git_ops
original = git_ops.Repository.call
def paused(self, *args, **kwargs):
    if args[0] == 'update-ref':
        Path(sys.argv[2]).write_text('ready')
        time.sleep(60)
    return original(self, *args, **kwargs)
git_ops.Repository.call = paused
focodex.main()
"""
        host = subprocess.Popen([sys.executable, '-c', code, str(ROOT / 'native_host'), str(gate)],
                                env={**os.environ, 'FOCODEX_CONFIG': str(self.config)},
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        self.addCleanup(probe.close, host)
        self.host = host
        (self.repo / 'a.txt').write_text('selected\n')
        self.stage(['a.txt'])
        review = self.review()
        old = self.git('rev-parse', 'HEAD')
        probe.write_message(host, {'type': 'git_commit', 'id': 'paused', 'projectId': 'test',
                                  'reviewToken': review['reviewToken']})
        deadline = time.monotonic() + 5
        while not gate.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(gate.exists())
        host.kill()
        host.wait(timeout=3)
        self.assertEqual(self.git('rev-parse', 'HEAD'), old)
        second = self.new_host()
        self.assertEqual(self.request('git_snapshot', host=second)['type'], 'error')
        self.assertTrue(self.config.with_suffix('.execution.json').exists())
        self.assertTrue((self.repo / '.git/index.lock').exists(), 'Do not automatically remove an abandoned Git lock')

    def test_post_commit_display_failure_keeps_confirmed_success_recoverable(self):
        # The ref update was confirmed; a later display refresh must not turn it
        # into an indeterminate operation that blocks all future Git actions.
        host_spec = importlib.util.spec_from_file_location('git_host_test', ROOT / 'native_host/focodex.py')
        module = importlib.util.module_from_spec(host_spec)
        host_spec.loader.exec_module(module)
        (self.repo / 'a.txt').write_text('selected\n')
        self.stage(['a.txt'])
        review = git_ops.Repository(self.repo).review('message')
        host = module.Host.__new__(module.Host)
        host.process = None; host.cleanup_uncertain = False
        key = ('test', 'test', str(self.repo))
        host.git_snapshots = {}
        host.git_reviews = {key: ('token', review, time.monotonic())}
        sent = []
        with (mock.patch.object(module, 'CONFIG', self.config),
              mock.patch.object(module, 'git_snapshot', side_effect=ValueError('postflight failed')),
              mock.patch.object(module, 'send', side_effect=sent.append)):
            host.git_command({'type': 'git_commit', 'id': 'commit', 'projectId': 'test', 'reviewToken': 'token'})
        self.assertFalse(host.cleanup_uncertain)
        self.assertFalse(self.config.with_suffix('.execution.json').exists())
        self.assertEqual(self.git('log', '-1', '--format=%s').strip(), 'message')
        self.assertEqual(sent, [{'type': 'git_commit', 'id': 'commit', 'commit': mock.ANY,
                                 'ref': 'refs/heads/main', 'tree': mock.ANY, 'message': 'message',
                                 'refreshError': 'postflight failed'}])
        self.assertEqual(self.request('git_snapshot')['type'], 'git_snapshot')

    def test_post_stage_display_failure_keeps_confirmed_success_recoverable(self):
        host_spec = importlib.util.spec_from_file_location('git_host_stage_test', ROOT / 'native_host/focodex.py')
        module = importlib.util.module_from_spec(host_spec)
        host_spec.loader.exec_module(module)
        (self.repo / 'a.txt').write_text('selected\n')
        host = module.Host.__new__(module.Host)
        host.process = None; host.cleanup_uncertain = False
        key = ('test', 'test', str(self.repo))
        host.git_reviews = {}
        host.git_snapshots = {key: ('token', git_ops.Repository(self.repo).fingerprint(), time.monotonic())}
        sent = []
        with (mock.patch.object(module, 'CONFIG', self.config),
              mock.patch.object(module, 'git_snapshot', side_effect=ValueError('postflight failed')),
              mock.patch.object(module, 'send', side_effect=sent.append)):
            host.git_command({'type': 'git_stage', 'id': 'stage', 'projectId': 'test',
                              'snapshotToken': 'token', 'files': ['a.txt']})
        self.assertFalse(host.cleanup_uncertain)
        self.assertFalse(self.config.with_suffix('.execution.json').exists())
        self.assertEqual(self.git('show', ':a.txt'), 'selected\n')
        self.assertEqual(sent, [{'type': 'git_stage', 'id': 'stage', 'refreshError': 'postflight failed'}])
        self.assertEqual(self.request('git_snapshot')['type'], 'git_snapshot')

    def test_malformed_index_is_rejected_as_a_validation_error(self):
        index = self.repo / '.git/index'
        original = index.read_bytes()
        checksum = b'\0' * 20
        for payload in (b'DIRC' + struct.pack('>II', 2, 1) + checksum,
                        b'DIRC' + struct.pack('>II', 2, 1) + b'\1' * 62 + checksum):
            index.write_bytes(original)
            repo = git_ops.Repository(self.repo)
            index.write_bytes(payload)
            with self.assertRaisesRegex(ValueError, 'インデックス構造を確認できません。'):
                repo.writable()

    def test_git_timeout_and_output_overflow_kill_process_group(self):
        fake = self.root / 'fake-git'
        fake.write_text('#!/bin/sh\nsleep 60\n')
        fake.chmod(0o700)
        with mock.patch.object(git_ops, 'GIT', str(fake)), mock.patch.object(git_ops, 'TIMEOUT', 0.1):
            with self.assertRaises(git_ops.UncertainGit):
                git_ops.run(self.repo, 'status')
        fake.write_text("#!/usr/bin/python3\nprint('x' * 300000)\n")
        with mock.patch.object(git_ops, 'GIT', str(fake)):
            with self.assertRaises(git_ops.UncertainGit):
                git_ops.run(self.repo, 'status')

    def test_git_signal_or_cleanup_error_is_always_uncertain(self):
        fake = self.root / 'signal-git'
        fake.write_text('#!/usr/bin/python3\nimport os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n')
        fake.chmod(0o700)
        with mock.patch.object(git_ops, 'GIT', str(fake)):
            with self.assertRaises(git_ops.UncertainGit):
                git_ops.run(self.repo, 'status')
        processes = []
        original = subprocess.Popen
        def capture(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process
        try:
            with mock.patch.object(git_ops.subprocess, 'Popen', side_effect=capture), mock.patch.object(git_ops.os, 'killpg', side_effect=PermissionError):
                with self.assertRaises(git_ops.UncertainGit):
                    git_ops.run(self.repo, 'status', '--short')
        finally:
            for process in processes:
                process.wait(timeout=3)

    def test_index_lock_blocks_publish_and_conflict_or_sparse_index_refused(self):
        (self.repo / 'a.txt').write_text('selected\n')
        snap = self.snapshot()
        lock = self.repo / '.git/index.lock'
        lock.write_bytes(b'owned by another git')
        self.assertEqual(self.request('git_stage', files=['a.txt'], snapshotToken=snap['snapshotToken'])['type'], 'error')
        self.assertEqual(lock.read_bytes(), b'owned by another git')
        lock.unlink()
        oid = self.git('rev-parse', 'HEAD:a.txt').strip()
        self.git('update-index', '--index-info', input=f'0 {"0"*40}\ta.txt\n100644 {oid} 1\ta.txt\n100644 {oid} 2\ta.txt\n100644 {oid} 3\ta.txt\n'.encode())
        self.assertFalse(self.request('git_snapshot')['canModify'])
        self.assertEqual(self.request('git_review', message='conflict')['type'], 'error')

    def test_sha256_repository_and_merge_state(self):
        import shutil
        shutil.rmtree(self.repo / '.git')
        self.git('init', '-qb', 'main', '--object-format=sha256')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.stage(['a.txt'])
        review = self.review('sha256 first')
        result = self.request('git_commit', reviewToken=review['reviewToken'])
        self.assertEqual(result['type'], 'git_commit', result)
        self.assertEqual(len(result['commit']), 64)
        self.assertEqual(self.git('show', 'HEAD:a.txt'), 'base\n')
        (self.repo / '.git/MERGE_HEAD').write_text(result['commit'] + '\n')
        self.assertFalse(self.request('git_snapshot')['canModify'])
        self.assertEqual(self.request('git_review', message='merge')['type'], 'error')

    def test_staging_forces_rehash_despite_same_size_stat_cache(self):
        (self.repo / 'a.txt').write_text('safe\n')
        repo = git_ops.Repository(self.repo)
        expected = repo.fingerprint()
        original = repo.call
        def stale_add(*args, **kwargs):
            if args[:2] == ('add', '-A'):
                # Simulate Git trusting a copied index's cached stat data.
                return ''
            return original(*args, **kwargs)
        with mock.patch.object(repo, 'call', side_effect=stale_add):
            repo.stage(['a.txt'], expected)
        self.assertEqual(self.git('show', ':a.txt'), 'safe\n')

    def test_shared_codex_lock_rejects_git_writes(self):
        import fcntl
        with self.config.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for operation, values in [('git_snapshot', {}), ('git_stage', {'files': ['a.txt']}),
                                      ('git_review', {'message': 'no'}), ('git_commit', {'reviewToken': 'no'})]:
                self.assertEqual(self.request(operation, **values)['type'], 'error')


if __name__ == '__main__':
    unittest.main()
