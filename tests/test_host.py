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
def emit(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    params = request['params']
    if method == 'initialize':
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
        assert set(params) in ({'cwd', 'ephemeral'}, {'cwd', 'ephemeral', 'model'}), params
        assert params['cwd'] == os.getcwd()
        emit({'id': request['id'], 'result': {'thread': {'id': 'thread-1'},
             'model': params.get('model', 'inherited'), 'approvalPolicy': 'on-request', 'sandbox': {'type': 'workspaceWrite'}}})
    elif method == 'turn/start':
        assert set(params) == {'threadId', 'input'}
        text = params['input'][0]['text']
        emit({'id': request['id'], 'result': {'turn': {'id': 'turn-1'}}})
        if text == 'hang':
            time.sleep(60)
        elif text == 'approval':
            emit({'id': 99, 'method': 'item/commandExecution/requestApproval', 'params': {}})
        elif text == 'crash':
            sys.exit(2)
        elif text == 'rpc-error':
            emit({'id': 55, 'error': {'code': 1, 'message': 'test failure'}})
        else:
            for index, message in enumerate(['調査結果です。', 'Focodex接続成功']):
                emit({'method': 'item/completed', 'params': {'threadId': 'thread-1',
                    'item': {'id': str(index), 'type': 'agentMessage', 'text': message}}})
            emit({'method': 'turn/completed', 'params': {'threadId': 'thread-1',
                'turn': {'id': 'turn-1', 'status': 'failed' if text == 'fail' else 'completed',
                         'error': {'message': 'test failure'} if text == 'fail' else None}}})
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

    def new_host(self):
        process = probe.launch(self.env)
        self.addCleanup(probe.close, process)
        return process

    def run_prompt(self, text='hello', host=None):
        process = host or self.host
        probe.write_message(process, {'type': 'run', 'id': 'run-1', 'projectId': 'test', 'text': text})
        return probe.read_message(process, 5)

    def result(self):
        message = probe.read_message(self.host, 5)
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

    def test_approval_never_automatically_allowed(self):
        self.run_prompt('approval')
        self.assertEqual(self.result()['status'], 'unsupported_request')

    def test_server_disconnect_and_rpc_error(self):
        for text in ['crash', 'rpc-error']:
            self.run_prompt(text)
            self.assertEqual(self.result()['status'], 'failed')

    def test_unregistered_project_rejected(self):
        probe.write_message(self.host, {'type': 'run', 'id': 'x', 'projectId': '../other', 'text': 'hello'})
        self.assertEqual(probe.read_message(self.host)['type'], 'error')
        self.assertFalse(self.pid.exists())

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
