import importlib.util
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'native_host'))
import git_ops
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

    def test_post_commit_verification_failure_is_durable_and_not_success(self):
        # Exercise the actual Host boundary, after the ref was already updated.
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
        with mock.patch.object(module, 'CONFIG', self.config), mock.patch.object(module, 'git_snapshot', side_effect=ValueError('postflight failed')):
            with self.assertRaisesRegex(ValueError, 'postflight failed'):
                host.git_command({'type': 'git_commit', 'id': 'commit', 'projectId': 'test', 'reviewToken': 'token'})
        self.assertTrue(host.cleanup_uncertain)
        self.assertTrue(self.config.with_suffix('.execution.json').exists())
        self.assertEqual(self.git('log', '-1', '--format=%s').strip(), 'message')
        self.assertEqual(self.request('git_commit', reviewToken='token')['type'], 'error')

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
