import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('probe', ROOT / 'scripts/probe_host.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

FAKE = '''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
Path(os.environ['FAKE_PID']).write_text(str(os.getpid()))
assert sys.argv[1:3] == ['-c', 'default_permissions="focodex-project"'], sys.argv
assert sys.argv[3] == '-c' and sys.argv[4].startswith('permissions.focodex-project='), sys.argv
assert sys.argv[5:] == ['app-server', '--listen', 'stdio://'], sys.argv
assert '\":root\" = \"deny\"' in sys.argv[4], sys.argv[4]
assert '\":workspace_roots\" = {\".\" = \"write\"}' in sys.argv[4], sys.argv[4]
assert '\"enabled\" = false' in sys.argv[4], sys.argv[4]
def emit(value):
    print(json.dumps(value), flush=True)
pending = None
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    params = request.get('params')
    if method == 'initialize':
        assert params['capabilities'] == {'experimentalApi': True}
        emit({'id': request['id'], 'result': {'userAgent': 'fake/0.145.0'}})
    elif method == 'model/list':
        assert params['includeHidden'] is False
        if not params.get('cursor'):
            emit({'id': request['id'], 'result': {'data': [
                {'model': 'test-first', 'displayName': 'First', 'isDefault': True},
                {'model': 'hidden-model', 'hidden': True}], 'nextCursor': 'page-2'}})
        else:
            assert params['cursor'] == 'page-2'
            emit({'id': request['id'], 'result': {'data': [
                {'model': 'test-second', 'displayName': 'Second', 'isDefault': False}], 'nextCursor': None}})
    elif method == 'thread/start':
        assert set(params) in ({'cwd', 'ephemeral', 'permissions', 'approvalPolicy'}, {'cwd', 'ephemeral', 'model', 'permissions', 'approvalPolicy'}), params
        assert params['permissions'] == 'focodex-project'
        assert params['approvalPolicy'] == 'on-request'
        assert params['cwd'] == os.getcwd()
        emit({'id': request['id'], 'result': {'thread': {'id': 'thread-1'},
             'model': params.get('model', 'inherited'), 'approvalPolicy': 'on-request',
             'activePermissionProfile': {'id': os.environ.get('FAKE_PROFILE', 'focodex-project')},
             'runtimeWorkspaceRoots': [os.environ.get('FAKE_ROOT', os.getcwd())]}})
    elif method == 'turn/start':
        assert set(params) == {'threadId', 'input'}
        text = params['input'][0]['text']
        emit({'id': request['id'], 'result': {'turn': {'id': 'turn-1'}}})
        if text == 'hang':
            time.sleep(60)
        elif text == 'wait-interrupt':
            pass
        elif text == 'approval':
            emit({'id': 99, 'method': 'item/commandExecution/requestApproval', 'params': {}})
        elif text in ('approval-request', 'approval-decline'):
            pending = text
            emit({'id': 99, 'method': 'item/commandExecution/requestApproval', 'params': {
                'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'item-1',
                'command': 'echo test', 'cwd': os.getcwd(), 'reason': 'test reason',
                'startedAtMs': 1}})
        elif text == 'approval-resolved':
            emit({'id': 94, 'method': 'item/commandExecution/requestApproval', 'params': {
                'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'item-1',
                'command': 'echo test', 'cwd': os.getcwd(), 'reason': 'test reason',
                'startedAtMs': 1}})
            emit({'method': 'serverRequest/resolved', 'params': {
                'threadId': 'thread-1', 'requestId': 94}})
        elif text == 'approval-no-reason':
            pending = text
            emit({'id': 93, 'method': 'item/commandExecution/requestApproval', 'params': {
                'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'item-1',
                'command': 'echo test', 'cwd': os.getcwd(), 'startedAtMs': 1}})
        elif text == 'question':
            pending = text
            emit({'id': 98, 'method': 'item/tool/requestUserInput', 'params': {
                'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'item-1',
                'isBlocking': True, 'questions': [
                    {'id': 'choice', 'header': '種類', 'question': 'どちらですか？',
                     'options': [{'label': 'A', 'description': '最初'}, {'label': 'B', 'description': '次'}]},
                    {'id': 'detail', 'header': '詳細', 'question': '説明してください', 'options': None}]}})
        elif text == 'file-approval':
            pending = text
            emit({'id': 97, 'method': 'item/fileChange/requestApproval', 'params': {
                'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'item-1',
                'grantRoot': '/tmp/other', 'reason': 'test reason', 'startedAtMs': 1}})
        elif text == 'file-approval-diff':
            pending = text
            emit({'method': 'item/started', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1',
                'item': {'id': 'item-1', 'type': 'fileChange', 'changes': [
                    {'path': '/tmp/test.txt', 'kind': 'add', 'diff': '+test\\n'}]}}})
            emit({'id': 96, 'method': 'item/fileChange/requestApproval', 'params': {
                'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'item-1',
                'reason': 'test reason', 'startedAtMs': 1}})
        elif text == 'approval-mismatch':
            emit({'method': 'item/started', 'params': {'threadId': 'thread-1', 'turnId': 'turn-1',
                'item': {'id': 'item-1', 'type': 'commandExecution',
                         'command': 'echo different', 'cwd': os.getcwd()}}})
            emit({'id': 95, 'method': 'item/commandExecution/requestApproval', 'params': {
                'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'item-1',
                'command': 'echo test', 'cwd': os.getcwd(), 'reason': 'test reason',
                'startedAtMs': 1}})
        elif text == 'crash':
            sys.exit(2)
        elif text == 'rpc-error':
            emit({'id': 55, 'error': {'code': 1, 'message': 'test failure'}})
        elif text == 'bwrap-error':
            emit({'id': 55, 'error': {'code': -32603,
                'message': 'bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted'}})
        else:
            for index, message in enumerate(['調査結果です。', 'Focodex接続成功']):
                emit({'method': 'item/completed', 'params': {'threadId': 'thread-1',
                    'item': {'id': str(index), 'type': 'agentMessage', 'text': message}}})
            emit({'method': 'turn/completed', 'params': {'threadId': 'thread-1',
                'turn': {'id': 'turn-1', 'status': 'failed' if text == 'fail' else 'completed',
                         'error': {'message': 'test failure'} if text == 'fail' else None}}})
    elif method == 'turn/interrupt':
        assert params == {'threadId': 'thread-1', 'turnId': 'turn-1'}
        emit({'id': request['id'], 'result': {}})
        emit({'method': 'turn/completed', 'params': {'threadId': 'thread-1',
            'turn': {'id': 'turn-1', 'status': 'interrupted'}}})
    elif method is None and pending:
        if pending == 'question':
            assert request['result'] == {'answers': {
                'choice': {'answers': ['B']}, 'detail': {'answers': ['自由回答']}}}
        else:
            assert request['result'] == {'decision': 'decline'}
        pending = None
        emit({'method': 'item/completed', 'params': {'threadId': 'thread-1',
            'item': {'id': 'reply', 'type': 'agentMessage', 'text': '回答を受け取りました'}}})
        emit({'method': 'turn/completed', 'params': {'threadId': 'thread-1',
            'turn': {'id': 'turn-1', 'status': 'completed'}}})
'''


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        fake = root / 'codex'
        fake.write_text(FAKE)
        fake.chmod(0o700)
        self.config = root / 'config.json'
        self.config.write_text(json.dumps({'codex': str(fake), 'projects': {
            'test': {'name': 'テスト', 'path': str(root)}}}))
        self.pid = root / 'pid'
        self.env = {**os.environ, 'FOCODEX_CONFIG': str(self.config), 'FAKE_PID': str(self.pid)}
        self.host = self.new_host()

    def new_host(self, env=None):
        process = probe.launch(env or self.env)
        self.addCleanup(probe.close, process)
        return process

    def run_prompt(self, text='hello', host=None):
        process = host or self.host
        probe.write_message(process, {'type': 'run', 'id': 'run-1', 'projectId': 'test', 'text': text})
        return probe.read_message(process, 5)

    def result(self):
        message = probe.read_message(self.host, 8)
        self.assertEqual(message['type'], 'result')
        return message

    def test_round_trip_and_repeated_run(self):
        probe.write_message(self.host, {'type': 'projects', 'id': 'p'})
        self.assertEqual(probe.read_message(self.host)['projects'][0]['id'], 'test')
        for _ in range(2):
            self.assertEqual(self.run_prompt()['type'], 'started')
            result = self.result()
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['text'], '調査結果です。\n\nFocodex接続成功')
            self.assertEqual(result['settings']['model'], 'inherited')
            self.assertEqual(result['threadId'], 'thread-1')

    def test_model_listing_pagination_and_explicit_selection(self):
        probe.write_message(self.host, {'type': 'models', 'id': 'm', 'projectId': 'test'})
        self.assertEqual(probe.read_message(self.host)['type'], 'started')
        catalog = probe.read_message(self.host)
        self.assertEqual(catalog['type'], 'models')
        self.assertEqual([row['model'] for row in catalog['models']], ['test-first', 'test-second'])
        probe.write_message(self.host, {'type': 'run', 'id': 'run-1', 'projectId': 'test',
                                       'model': 'test-second', 'text': 'hello'})
        self.assertEqual(probe.read_message(self.host)['type'], 'started')
        self.assertEqual(self.result()['settings']['model'], 'test-second')
        for model in ['hidden-model', 'invented-model']:
            probe.write_message(self.host, {'type': 'run', 'id': 'invalid', 'projectId': 'test',
                                           'model': model, 'text': 'hello'})
            self.assertEqual(probe.read_message(self.host)['type'], 'error')

    def test_model_requires_catalog_from_same_connection(self):
        probe.write_message(self.host, {'type': 'run', 'id': 'invalid', 'projectId': 'test',
                                       'model': 'test-first', 'text': 'hello'})
        self.assertEqual(probe.read_message(self.host)['type'], 'error')
        self.assertFalse(self.pid.exists())

    def test_failed_turn_not_success(self):
        self.run_prompt('fail')
        self.assertEqual(self.result()['status'], 'failed')

    def test_unexpected_effective_permissions_stop_before_turn(self):
        for override in ({'FAKE_PROFILE': 'other'}, {'FAKE_ROOT': '/tmp/other'}):
            other = self.new_host({**self.env, **override})
            self.assertEqual(self.run_prompt(host=other)['type'], 'started')
            result = probe.read_message(other, 5)
            self.assertEqual(result['status'], 'failed')
            self.assertIn('有効な権限設定', result['error'])

    def test_approval_never_automatically_allowed(self):
        self.run_prompt('approval')
        self.assertEqual(self.result()['status'], 'unsupported_request')

    def test_command_approval_requires_explicit_answer(self):
        for prompt, decision in [('approval-request', 'decline'), ('approval-decline', 'decline')]:
            self.assertEqual(self.run_prompt(prompt)['type'], 'started')
            interaction = probe.read_message(self.host, 5)
            self.assertEqual(interaction['type'], 'interaction')
            self.assertEqual(interaction['details']['command'], 'echo test')
            self.assertFalse(interaction['approvable'])
            self.assertEqual(interaction['approvalScope'], 'once')
            if decision == 'decline':
                probe.write_message(self.host, {'type': 'respond', 'id': 'wrong-item',
                    'targetId': 'run-1', 'requestId': interaction['requestId'], 'itemId': 'other',
                    'decision': 'accept', 'approvalScope': 'once'})
                self.assertEqual(probe.read_message(self.host)['type'], 'error')
                probe.write_message(self.host, {'type': 'respond', 'id': 'wrong-scope',
                    'targetId': 'run-1', 'requestId': interaction['requestId'],
                    'itemId': interaction['itemId'], 'decision': 'accept',
                    'approvalScope': 'session'})
                self.assertEqual(probe.read_message(self.host)['type'], 'error')
            probe.write_message(self.host, {'type': 'respond', 'id': 'answer',
                'targetId': 'run-1', 'requestId': interaction['requestId'], 'itemId': interaction['itemId'],
                'decision': decision, 'approvalScope': 'once' if decision == 'accept' else None})
            self.assertEqual(probe.read_message(self.host)['type'], 'answered')
            self.assertEqual(self.result()['status'], 'completed')

    def test_question_choice_and_free_text(self):
        self.assertEqual(self.run_prompt('question')['type'], 'started')
        interaction = probe.read_message(self.host, 5)
        self.assertEqual(interaction['kind'], 'question')
        probe.write_message(self.host, {'type': 'respond', 'id': 'bad', 'targetId': 'run-1',
            'requestId': interaction['requestId'], 'itemId': interaction['itemId'],
            'answers': {'choice': 'invalid', 'detail': '自由回答'}})
        self.assertEqual(probe.read_message(self.host)['type'], 'error')
        probe.write_message(self.host, {'type': 'respond', 'id': 'answer', 'targetId': 'run-1',
            'requestId': interaction['requestId'], 'itemId': interaction['itemId'],
            'answers': {'choice': 'B', 'detail': '自由回答'}})
        self.assertEqual(probe.read_message(self.host)['type'], 'answered')
        self.assertEqual(self.result()['text'], '回答を受け取りました')

    def test_file_change_cannot_be_approved_without_change_details(self):
        self.assertEqual(self.run_prompt('file-approval')['type'], 'started')
        interaction = probe.read_message(self.host, 5)
        self.assertEqual(interaction['kind'], 'file')
        self.assertFalse(interaction['approvable'])
        probe.write_message(self.host, {'type': 'respond', 'id': 'bad', 'targetId': 'run-1',
            'requestId': interaction['requestId'], 'itemId': interaction['itemId'],
            'decision': 'accept', 'approvalScope': 'once'})
        self.assertEqual(probe.read_message(self.host)['type'], 'error')
        probe.write_message(self.host, {'type': 'respond', 'id': 'answer', 'targetId': 'run-1',
            'requestId': interaction['requestId'], 'itemId': interaction['itemId'], 'decision': 'decline'})
        self.assertEqual(probe.read_message(self.host)['type'], 'answered')
        self.assertEqual(self.result()['status'], 'completed')

    def test_file_change_diff_can_be_approved_once(self):
        self.assertEqual(self.run_prompt('file-approval-diff')['type'], 'started')
        interaction = probe.read_message(self.host, 5)
        self.assertFalse(interaction['approvable'])
        self.assertEqual(interaction['details']['changes'][0]['path'], '/tmp/test.txt')
        probe.write_message(self.host, {'type': 'respond', 'id': 'answer', 'targetId': 'run-1',
            'requestId': interaction['requestId'], 'itemId': interaction['itemId'],
            'decision': 'accept', 'approvalScope': 'once'})
        self.assertEqual(probe.read_message(self.host)['type'], 'error')
        probe.write_message(self.host, {'type': 'respond', 'id': 'decline',
            'targetId': 'run-1', 'requestId': interaction['requestId'],
            'itemId': interaction['itemId'], 'decision': 'decline'})
        self.assertEqual(probe.read_message(self.host)['type'], 'answered')
        self.assertEqual(self.result()['status'], 'completed')

    def test_mismatched_command_is_never_approvable(self):
        self.assertEqual(self.run_prompt('approval-mismatch')['type'], 'started')
        self.assertEqual(self.result()['status'], 'unsupported_request')

    def test_missing_reason_disables_approval(self):
        self.assertEqual(self.run_prompt('approval-no-reason')['type'], 'started')
        interaction = probe.read_message(self.host, 5)
        self.assertFalse(interaction['approvable'])
        probe.write_message(self.host, {'type': 'respond', 'id': 'bad', 'targetId': 'run-1',
            'requestId': interaction['requestId'], 'itemId': interaction['itemId'],
            'decision': 'accept', 'approvalScope': 'once'})
        self.assertEqual(probe.read_message(self.host)['type'], 'error')
        probe.write_message(self.host, {'type': 'respond', 'id': 'answer', 'targetId': 'run-1',
            'requestId': interaction['requestId'], 'itemId': interaction['itemId'],
            'decision': 'decline'})
        self.assertEqual(probe.read_message(self.host)['type'], 'answered')
        self.assertEqual(self.result()['status'], 'completed')

    def test_stop_invalidates_pending_approval(self):
        self.assertEqual(self.run_prompt('approval-request')['type'], 'started')
        interaction = probe.read_message(self.host, 5)
        self.assertEqual(interaction['kind'], 'command')
        probe.write_message(self.host, {'type': 'stop', 'id': 'stop-1', 'targetId': 'run-1'})
        self.assertEqual(probe.read_message(self.host)['type'], 'stopping')
        self.assertEqual(self.result()['status'], 'interrupted')
        probe.write_message(self.host, {'type': 'respond', 'id': 'late', 'targetId': 'run-1',
            'requestId': interaction['requestId'], 'itemId': interaction['itemId'],
            'decision': 'accept', 'approvalScope': 'once'})
        self.assertEqual(probe.read_message(self.host)['type'], 'error')

    def test_resolved_request_cannot_be_approved(self):
        self.assertEqual(self.run_prompt('approval-resolved')['type'], 'started')
        interaction = probe.read_message(self.host, 5)
        resolved = probe.read_message(self.host, 5)
        self.assertEqual(resolved['type'], 'interaction_resolved')
        self.assertEqual(resolved['requestId'], interaction['requestId'])
        probe.write_message(self.host, {'type': 'respond', 'id': 'late', 'targetId': 'run-1',
            'requestId': interaction['requestId'], 'itemId': interaction['itemId'],
            'decision': 'accept', 'approvalScope': 'once'})
        self.assertEqual(probe.read_message(self.host)['type'], 'error')
        probe.write_message(self.host, {'type': 'stop', 'id': 'stop-1', 'targetId': 'run-1'})
        self.assertEqual(probe.read_message(self.host)['type'], 'stopping')
        self.assertEqual(self.result()['status'], 'interrupted')

    def test_server_disconnect_and_rpc_error(self):
        for text in ['crash', 'rpc-error']:
            self.run_prompt(text)
            self.assertEqual(self.result()['status'], 'failed')

    def test_bwrap_failure_reports_sandbox_problem(self):
        self.run_prompt('bwrap-error')
        result = self.result()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Linux隔離を開始できません', result['error'])

    def test_unregistered_project_rejected(self):
        probe.write_message(self.host, {'type': 'run', 'id': 'x', 'projectId': '../other', 'text': 'hello'})
        self.assertEqual(probe.read_message(self.host)['type'], 'error')
        self.assertFalse(self.pid.exists())

    def test_git_snapshot_reads_status_and_both_diffs(self):
        root = Path(self.temp.name)
        def git(*args):
            subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
        git('init', '-q')
        note = root / 'note.txt'
        note.write_text('base\n')
        git('add', 'note.txt')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
        note.write_text('base\nstaged\n')
        git('add', 'note.txt')
        note.write_text('base\nstaged\nunstaged\n')
        (root / '日本語.txt').write_text('untracked\n')
        probe.write_message(self.host, {'type': 'git_snapshot', 'id': 'git-1', 'projectId': 'test'})
        reply = probe.read_message(self.host)
        self.assertEqual(reply['type'], 'git_snapshot')
        self.assertIn('MM note.txt', reply['status'])
        self.assertIn('?? 日本語.txt', reply['status'])
        self.assertIn('+staged', reply['stagedDiff'])
        self.assertIn('+unstaged', reply['unstagedDiff'])
        marker = root / 'unsafe-helper-ran'
        git('config', 'filter.unsafe.process', f'touch {marker}')
        probe.write_message(self.host, {'type': 'git_snapshot', 'id': 'git-2', 'projectId': 'test'})
        self.assertEqual(probe.read_message(self.host)['type'], 'error')
        self.assertFalse(marker.exists())

    def test_git_snapshot_accepts_registered_worktree(self):
        root = Path(self.temp.name)
        source, worktree = root / 'source', root / 'worktree'
        source.mkdir()
        def git(*args):
            subprocess.run(['git', '-C', str(source), *args], check=True, capture_output=True)
        git('init', '-q')
        (source / 'note.txt').write_text('base\n')
        git('add', 'note.txt')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
        git('worktree', 'add', '-qb', 'test-worktree', str(worktree))
        self.config.write_text(json.dumps({'codex': str(root / 'codex'), 'projects': {
            'test': {'name': 'テスト', 'path': str(worktree)}}}))
        (worktree / 'note.txt').write_text('base\nchanged\n')
        probe.write_message(self.host, {'type': 'git_snapshot', 'id': 'git-wt', 'projectId': 'test'})
        reply = probe.read_message(self.host)
        self.assertEqual(reply['type'], 'git_snapshot')
        self.assertIn('+changed', reply['unstagedDiff'])

    def test_duplicate_host_lock_and_disconnect_cleanup(self):
        self.run_prompt('hang')
        deadline = time.monotonic() + 3
        while not self.pid.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child = int(self.pid.read_text())
        second = self.new_host()
        self.assertEqual(self.run_prompt(host=second)['type'], 'error')
        probe.close(self.host)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)
        self.assertEqual(self.run_prompt(host=second)['type'], 'started')
        self.assertEqual(probe.read_message(second, 5)['status'], 'completed')

    def test_sigterm_cleanup(self):
        self.run_prompt('hang')
        deadline = time.monotonic() + 3
        while not self.pid.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child = int(self.pid.read_text())
        self.host.send_signal(signal.SIGTERM)
        self.host.wait(timeout=5)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_stop_waits_for_confirmed_exit(self):
        self.assertEqual(self.run_prompt('hang')['type'], 'started')
        deadline = time.monotonic() + 3
        while not self.pid.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child = int(self.pid.read_text())
        probe.write_message(self.host, {'type': 'stop', 'id': 'stop-1', 'targetId': 'run-1'})
        self.assertEqual(probe.read_message(self.host)['type'], 'stopping')
        result = self.result()
        self.assertEqual(result['status'], 'interrupted')
        self.assertTrue(result['stopDiagnostics']['fallbackUsed'])
        self.assertIsNone(result['stopDiagnostics']['turnStatus'])
        self.assertTrue(result['stopDiagnostics']['process']['processGroupGone'])
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)
        self.assertEqual(self.run_prompt()['type'], 'started')
        self.assertEqual(self.result()['status'], 'completed')

    def test_stop_uses_turn_interrupt_when_codex_responds(self):
        self.assertEqual(self.run_prompt('wait-interrupt')['type'], 'started')
        probe.write_message(self.host, {'type': 'stop', 'id': 'stop-1', 'targetId': 'run-1'})
        self.assertEqual(probe.read_message(self.host)['type'], 'stopping')
        result = self.result()
        self.assertEqual(result['status'], 'interrupted')
        self.assertIsNone(result['error'])
        self.assertFalse(result['stopDiagnostics']['fallbackUsed'])
        self.assertTrue(result['stopDiagnostics']['interruptSent'])
        self.assertTrue(result['stopDiagnostics']['interruptAcknowledged'])
        self.assertEqual(result['stopDiagnostics']['turnStatus'], 'interrupted')
        self.assertTrue(result['stopDiagnostics']['process']['processGroupGone'])

    def test_install_preserves_existing_config(self):
        target = Path(self.temp.name) / 'home'
        cmd = ['python3', str(ROOT / 'scripts/install_host.py'), '--home', str(target),
               '--codex', str(Path(self.temp.name) / 'codex')]
        subprocess.run(cmd, check=True, capture_output=True)
        config = target / '.config/focodex/config.json'
        config.write_text('{"keep": true}')
        subprocess.run(cmd, check=True, capture_output=True)
        self.assertEqual(json.loads(config.read_text()), {'keep': True})
        manifest = json.loads((target / '.mozilla/native-messaging-hosts/focodex.json').read_text())
        self.assertTrue(os.access(manifest['path'], os.X_OK))
        self.assertEqual(manifest['allowed_extensions'], ['focodex@foxbook.example'])


if __name__ == '__main__':
    unittest.main()
