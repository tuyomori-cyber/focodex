import importlib.util
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import subprocess
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('probe', ROOT / 'scripts/probe_host.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
sys.path.insert(0, str(ROOT / 'native_host'))
host_spec = importlib.util.spec_from_file_location('focodex_host', ROOT / 'native_host/focodex.py')
host_module = importlib.util.module_from_spec(host_spec)
host_spec.loader.exec_module(host_module)

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
             'runtimeWorkspaceRoots': [os.environ.get('FAKE_ROOT', os.getcwd())],
             'sandbox': {'type': 'workspaceWrite', 'writableRoots':
                         [os.environ['FAKE_EXTRA_ROOT']] if os.environ.get('FAKE_EXTRA_ROOT') else [],
                         'networkAccess': False, 'excludeTmpdirEnvVar': True,
                         'excludeSlashTmp': True}}})
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
        settings_temp = tempfile.TemporaryDirectory()
        self.addCleanup(settings_temp.cleanup)
        state_temp = tempfile.TemporaryDirectory()
        self.addCleanup(state_temp.cleanup)
        self.config = Path(settings_temp.name) / 'config.json'
        self.config.write_text(json.dumps({'codex': str(fake), 'projects': {
            'test': {'name': 'テスト', 'path': str(root)}}}))
        self.pid = root / 'pid'
        self.state = Path(state_temp.name)
        self.env = {**os.environ, 'FOCODEX_CONFIG': str(self.config), 'FAKE_PID': str(self.pid),
                    'XDG_STATE_HOME': str(self.state)}
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

    def diagnostic_events(self):
        path = self.state / 'focodex' / 'operation-diagnostics.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_round_trip_and_repeated_run(self):
        probe.write_message(self.host, {'type': 'projects', 'id': 'p'})
        projects = probe.read_message(self.host)
        self.assertEqual(projects['workspaces'][0]['id'], 'test')
        self.assertEqual(projects['workspaces'][0]['projects'][0]['id'], 'test')
        for _ in range(2):
            self.assertEqual(self.run_prompt()['type'], 'started')
            result = self.result()
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['text'], '調査結果です。\n\nFocodex接続成功')
            self.assertEqual(result['settings']['model'], 'inherited')
            self.assertEqual(result['threadId'], 'thread-1')
            self.assertFalse(self.config.with_suffix('.execution.json').exists())

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

    def test_model_listing_writes_content_free_page_trace(self):
        probe.write_message(self.host, {'type': 'models', 'id': 'm', 'projectId': 'test'})
        self.assertEqual(probe.read_message(self.host)['type'], 'started')
        catalog = probe.read_message(self.host)
        self.assertEqual(catalog['type'], 'models')
        self.assertRegex(catalog['traceId'], r'^[0-9a-f-]{36}$')
        events = self.diagnostic_events()
        self.assertEqual({event['traceId'] for event in events}, {catalog['traceId']})
        self.assertEqual([event['page'] for event in events if event['stage'] == 'model_page_received'], [1, 2])
        self.assertEqual(events[-1]['stage'], 'terminal')
        self.assertEqual(events[-1]['status'], 'completed')
        contents = (self.state / 'focodex' / 'operation-diagnostics.jsonl').read_text()
        self.assertNotIn('test-first', contents)
        self.assertEqual((self.state / 'focodex' / 'operation-diagnostics.jsonl').stat().st_mode & 0o777, 0o600)

    def test_model_catalog_rejects_unconfirmed_cleanup(self):
        host = host_module.Host.__new__(host_module.Host)
        host.trace = {'id': 'test', 'operation': 'models', 'requestId': 'models-test',
                      'started': time.monotonic(), 'terminal': False, 'available': True}
        host.operation = 'models'
        host.model_page = 1
        host.model_rows = []
        host.cursors = set()
        host.catalogs = {}
        host.workspace_id = 'workspace'
        host.cwd = '/project'
        host.request_id = 'models-test'
        host.cleanup = mock.Mock(return_value={'processGroupGone': False})
        with mock.patch.object(host_module, 'send') as send, mock.patch.object(host_module.diagnostics, 'write_operation_diagnostic', return_value=True) as write:
            host.event({'id': 4, 'result': {'data': [{'model': 'example'}], 'nextCursor': None}})
        message = send.call_args.args[0]
        self.assertEqual(message['type'], 'error')
        self.assertIn('終了を確認できません', message['error'])
        self.assertFalse(host.catalogs)
        self.assertEqual(write.call_args.args[0]['errorCode'], 'process-unconfirmed')
        self.assertEqual(write.call_args.args[0]['status'], 'failed')

    def test_process_group_snapshot_counts_own_process_without_identifiers(self):
        result = host_module.process_group_summary(os.getpgrp())
        self.assertTrue(result['available'])
        self.assertGreaterEqual(sum(result['states'].values()), 1)
        self.assertEqual(set(result), {'available', 'states', 'unreadable', 'scanned', 'truncated'})

    def test_uncertain_cleanup_records_signal_and_state_summary(self):
        host = host_module.Host.__new__(host_module.Host)
        host.process = mock.Mock(pid=123456789)
        host.process.wait.return_value = -9
        host.selector = mock.Mock()
        host.lock = None
        host.execution_token = None
        host.cleanup_uncertain = False
        host.rpc_buffer = bytearray()
        host.trace = {'id': 'test', 'operation': 'models', 'started': time.monotonic(), 'available': True}
        snapshot = {'available': True, 'states': {'zombie': 1}, 'unreadable': 0, 'scanned': 1, 'truncated': False}
        with mock.patch.object(host_module.os, 'killpg', side_effect=[ProcessLookupError, None]), \
             mock.patch.object(host_module, 'process_group_summary', return_value=snapshot), \
             mock.patch.object(host_module.diagnostics, 'write_operation_diagnostic', return_value=True) as write:
            result = host.cleanup()
        self.assertFalse(result['processGroupGone'])
        events = [call.args[0] for call in write.call_args_list]
        self.assertEqual(next(e for e in events if e['stage'] == 'cleanup_signal')['outcome'], 'group-gone')
        self.assertEqual(next(e for e in events if e['stage'] == 'cleanup_group_check')['snapshot'], snapshot)
        self.assertNotIn('123456789', json.dumps(events))

    def test_failed_run_records_rpc_stage_without_prompt_or_response(self):
        self.assertEqual(self.run_prompt('rpc-error')['type'], 'started')
        result = self.result()
        self.assertEqual(result['status'], 'failed')
        events = self.diagnostic_events()
        self.assertTrue(any(event['stage'] == 'rpc_error' for event in events))
        self.assertEqual(events[-1]['status'], 'failed')
        contents = (self.state / 'focodex' / 'operation-diagnostics.jsonl').read_text()
        self.assertNotIn('rpc-error', contents)
        self.assertNotIn('test failure', contents)

    def test_validation_rejection_has_a_trace(self):
        probe.write_message(self.host, {'type': 'run', 'id': 'invalid', 'projectId': 'test',
                                       'model': 'not-listed', 'text': 'secret prompt'})
        reply = probe.read_message(self.host)
        self.assertEqual(reply['type'], 'error')
        self.assertRegex(reply['traceId'], r'^[0-9a-f-]{36}$')
        events = self.diagnostic_events()
        self.assertEqual(events[-1]['stage'], 'terminal')
        self.assertEqual(events[-1]['status'], 'rejected')
        self.assertNotIn('secret prompt', (self.state / 'focodex' / 'operation-diagnostics.jsonl').read_text())

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

    def test_standalone_git_is_rejected_while_codex_runs(self):
        root = Path(self.temp.name)
        repository = root / 'standalone'
        (repository / '.git').mkdir(parents=True)
        self.config.write_text(json.dumps({'codex': str(root / 'codex'), 'projects': {
            'test': {'name': 'テスト', 'path': str(root)}},
            'discoveryRoots': [str(root)]}))
        project_id = 'repo_' + hashlib.sha256(str(repository.resolve()).encode()).hexdigest()
        probe.write_message(self.host, {'type': 'run', 'id': 'run-standalone',
                                        'workspaceId': '__standalone__', 'projectId': project_id,
                                        'text': 'hang'})
        self.assertEqual(probe.read_message(self.host)['type'], 'started')
        probe.write_message(self.host, {'type': 'git_snapshot', 'id': 'git-while-run',
                                        'workspaceId': '__standalone__', 'projectId': project_id})
        reply = probe.read_message(self.host)
        self.assertEqual(reply['type'], 'error')
        self.assertIn('実行中', reply['error'])

    def test_workspace_allows_only_its_declared_projects(self):
        root = Path(self.temp.name)
        other_temp = tempfile.TemporaryDirectory()
        self.addCleanup(other_temp.cleanup)
        other = Path(other_temp.name)
        self.config.write_text(json.dumps({'codex': str(root / 'codex'), 'projects': {
            'test': {'name': 'テスト', 'path': str(root)},
            'other': {'name': '別', 'path': str(other)}}, 'workspaces': {
            'both': {'name': '両方', 'projectIds': ['test', 'other'], 'defaultProjectId': 'test'}}}))
        host = self.new_host({**self.env, 'FAKE_EXTRA_ROOT': str(other.resolve())})
        probe.write_message(host, {'type': 'projects', 'id': 'p'})
        reply = probe.read_message(host)
        self.assertEqual(reply['workspaces'][0]['id'], 'both')
        self.assertEqual([row['id'] for row in reply['workspaces'][0]['projects']], ['test', 'other'])
        probe.write_message(host, {'type': 'run', 'id': 'run-1', 'workspaceId': 'both',
                                   'projectId': 'test', 'text': 'hello'})
        self.assertEqual(probe.read_message(host)['type'], 'started')
        self.assertEqual(probe.read_message(host)['status'], 'completed')
        probe.write_message(host, {'type': 'run', 'id': 'bad', 'workspaceId': 'both',
                                   'projectId': 'missing', 'text': 'hello'})
        self.assertEqual(probe.read_message(host)['type'], 'error')

    def test_repository_discovery_and_workspace_save(self):
        root = Path(self.temp.name)
        discovery = root / 'repos'
        alpha = discovery / 'alpha'
        beta = discovery / 'group' / 'beta'
        (alpha / '.git').mkdir(parents=True)
        beta.mkdir(parents=True)
        (beta / '.git').write_text('gitdir: /outside/worktrees/beta\n')
        probe.write_message(self.host, {'type': 'discovery_root_add', 'id': 'add',
                                        'path': str(discovery)})
        added = probe.read_message(self.host)
        self.assertEqual(added['type'], 'workspace_config')
        self.assertEqual(added['discoveryRoots'], [str(discovery.resolve())])
        probe.write_message(self.host, {'type': 'repository_scan', 'id': 'scan',
                                        'root': str(discovery.resolve())})
        scanned = probe.read_message(self.host)
        self.assertEqual({row['path'] for row in scanned['repositories']},
                         {str(alpha.resolve()), str(beta.resolve())})
        self.assertEqual({row['kind'] for row in scanned['repositories']}, {'repository', 'worktree'})
        probe.write_message(self.host, {'type': 'workspace_save', 'id': 'save',
            'name': 'Found', 'projectPaths': [str(alpha.resolve()), str(beta.resolve())],
            'defaultProjectPath': str(beta.resolve())})
        saved = probe.read_message(self.host)
        workspace = next(row for row in saved['workspaces']
                         if [item['path'] for item in row['projects']] == [str(alpha.resolve()), str(beta.resolve())])
        self.assertRegex(workspace['id'], r'^ws_[a-f0-9]{32}$')
        self.assertEqual([row['path'] for row in workspace['projects']],
                         [str(alpha.resolve()), str(beta.resolve())])
        self.assertEqual(workspace['projects'][1]['id'], workspace['defaultProjectId'])
        persisted = json.loads(self.config.read_text())
        self.assertIn('discoveryRoots', persisted)
        self.assertIn('workspaces', persisted)

    def test_last_workspace_can_be_deleted(self):
        probe.write_message(self.host, {'type': 'workspace_delete', 'id': 'delete', 'workspaceId': 'test'})
        deleted = probe.read_message(self.host)
        self.assertEqual(deleted['workspaces'], [])
        persisted = json.loads(self.config.read_text())
        self.assertEqual(persisted['workspaces'], {})
        self.assertEqual(persisted['projects'], {})

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
        (root / 'no-newline.txt').write_text('test')
        (root / 'outside-link').symlink_to('/etc/hostname')
        probe.write_message(self.host, {'type': 'git_snapshot', 'id': 'git-1', 'projectId': 'test'})
        reply = probe.read_message(self.host)
        self.assertEqual(reply['type'], 'git_snapshot')
        files = {row['path']: row for row in reply['files']}
        self.assertEqual(files['note.txt']['status'], 'MM')
        self.assertIn('+staged', files['note.txt']['stagedDiff'])
        self.assertIn('+unstaged', files['note.txt']['unstagedDiff'])
        self.assertEqual(files['日本語.txt']['status'], '??')
        self.assertIn('+untracked', files['日本語.txt']['unstagedDiff'])
        self.assertIn('\\ No newline at end of file', files['no-newline.txt']['unstagedDiff'])
        self.assertIn('安全に読み取れない', files['outside-link']['unstagedDiff'])
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
        self.assertIn('+changed', reply['files'][0]['unstagedDiff'])

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

    def test_sigkill_leaves_durable_block_even_after_child_exits(self):
        self.assertEqual(self.run_prompt('hang')['type'], 'started')
        deadline = time.monotonic() + 3
        while not self.pid.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child = int(self.pid.read_text())
        self.addCleanup(self.kill_group, child)
        marker = self.config.with_suffix('.execution.json')
        self.assertTrue(marker.exists())
        self.host.kill()
        self.host.wait(timeout=5)
        # Kernel flock is now free; persistent execution state must still block.
        second = self.new_host()
        for message in (
                {'type': 'run', 'text': 'hello'}, {'type': 'models'},
                {'type': 'git_snapshot'},
                {'type': 'discovery_root_add', 'path': self.temp.name}):
            probe.write_message(second, {**message, 'id': 'blocked', 'projectId': 'test'})
            reply = probe.read_message(second, 5)
            self.assertEqual(reply['type'], 'error')
            self.assertIn('終了が未確認', reply['error'])
        self.kill_group(child)
        self.assertEqual(self.run_prompt(host=second)['type'], 'error')
        self.assertTrue(marker.exists())

    @staticmethod
    def kill_group(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def test_uncertain_cleanup_releases_lock_but_keeps_block(self):
        with mock.patch.object(host_module, 'CONFIG', self.config):
            token = 'owned-test-execution'
            host_module.atomic_json(host_module.execution_path(), {
                'version': 1, 'token': token, 'bootId': host_module.boot_id()})
            host = host_module.Host.__new__(host_module.Host)
            host.process = mock.Mock(pid=123456789)
            host.process.wait.return_value = 0
            host.selector = mock.Mock()
            host.lock = self.config.with_suffix('.lock').open('a')
            host_module.fcntl.flock(host.lock, host_module.fcntl.LOCK_EX)
            host.execution_token = token
            host.cleanup_uncertain = False
            host.rpc_buffer = bytearray()
            with mock.patch.object(host_module.os, 'killpg', side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    host.cleanup()
            self.assertIsNone(host.lock)
            self.assertTrue(host_module.execution_path().exists())
            # A later cleanup still cannot prove the process group disappeared.
            with mock.patch.object(host_module.os, 'killpg', side_effect=[ProcessLookupError, None]):
                result = host.cleanup()
            self.assertFalse(result['processGroupGone'])
            self.assertTrue(host.cleanup_uncertain)
            self.assertIsNone(host.lock)
            host.cleanup()  # Repeated cleanup must not erase the block.
            with self.assertRaisesRegex(ValueError, '終了が未確認'):
                host.locked_config()
            self.assertTrue(host_module.execution_path().exists())

    def test_execution_record_recovery_requires_different_boot(self):
        with mock.patch.object(host_module, 'CONFIG', self.config):
            path = host_module.execution_path()
            guard = host_module.Host.__new__(host_module.Host)
            for value in ('{', '{}', '{"version": 1, "token": "x", "bootId": "bad"}'):
                path.write_text(value)
                with self.assertRaises(ValueError):
                    with guard.locked_config():
                        pass
                self.assertTrue(path.exists())
            host_module.atomic_json(path, {'version': 1, 'token': 'x',
                                          'bootId': host_module.boot_id().upper()})
            with self.assertRaisesRegex(ValueError, '終了が未確認'):
                with guard.locked_config():
                    pass
            with mock.patch.object(host_module, 'boot_id', return_value='00000000-0000-0000-0000-000000000001'):
                with guard.locked_config():
                    pass
            self.assertFalse(path.exists())

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
        self.assertTrue((target / '.local/lib/focodex/diagnostics.py').is_file())
        self.assertTrue((target / '.local/lib/focodex/git_ops.py').is_file())
        self.assertTrue((target / '.local/lib/focodex/git_remote.py').is_file())
        self.assertTrue((target / '.local/lib/focodex/remote_agent.py').is_file())
        self.assertTrue((target / '.config/systemd/user/focodex-remote-agent.service').is_file())
        self.assertTrue((target / '.config/systemd/user/focodex-remote-key.service').is_file())


if __name__ == '__main__':
    unittest.main()
