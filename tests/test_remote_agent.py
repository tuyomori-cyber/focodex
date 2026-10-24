import importlib.util
import os
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('remote_agent', ROOT / 'native_host/remote_agent.py')
remote_agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote_agent)


class RemoteAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name)
        self.runtime.chmod(0o700)

    def test_prepare_creates_private_directory_without_socket(self):
        socket = remote_agent.prepare(self.runtime)
        self.assertEqual(socket, self.runtime / 'focodex/remote-agent.sock')
        self.assertEqual(stat.S_IMODE(socket.parent.stat().st_mode), 0o700)
        self.assertFalse(socket.exists())

    def test_prepare_refuses_existing_socket_name(self):
        socket = remote_agent.prepare(self.runtime)
        socket.write_text('not-a-socket')
        with self.assertRaises(ValueError):
            remote_agent.prepare(self.runtime)

    def test_keyring_status_returns_only_lock_state(self):
        complete = mock.Mock(returncode=0, stdout=b'b false\n')
        with mock.patch.object(remote_agent.Path, 'is_file', return_value=True), \
             mock.patch.object(remote_agent.subprocess, 'run', return_value=complete):
            self.assertTrue(remote_agent.keyring_unlocked())
        complete = mock.Mock(returncode=0, stdout=b'b true\n')
        with mock.patch.object(remote_agent.Path, 'is_file', return_value=True), \
             mock.patch.object(remote_agent.subprocess, 'run', return_value=complete):
            self.assertFalse(remote_agent.keyring_unlocked())

    def test_agent_status_refuses_non_socket(self):
        socket = remote_agent.prepare(self.runtime)
        socket.write_text('not-a-socket')
        self.assertEqual(remote_agent.agent_status(self.runtime), 'invalid')

    def test_settings_rejects_public_key_or_config(self):
        config = self.runtime / 'settings.json'
        key = self.runtime / 'id_ed25519'
        key.write_text('private')
        key.chmod(0o600)
        config.write_text(json.dumps({'version': 1, 'privateKey': str(key),
                                      'secretAttributes': {'service': 'focodex'}}))
        config.chmod(0o600)
        self.assertEqual(remote_agent.settings(config)[0], key)
        config.chmod(0o644)
        with self.assertRaises(ValueError): remote_agent.settings(config)

    def test_register_uses_fixed_askpass_and_never_exposes_secret(self):
        key = self.runtime / 'id_ed25519'; key.write_text('private'); key.chmod(0o600)
        config = self.runtime / 'settings.json'
        config.write_text(json.dumps({'version': 1, 'privateKey': str(key),
                                      'secretAttributes': {'service': 'focodex'}})); config.chmod(0o600)
        with mock.patch.object(remote_agent, 'CONFIG', config), \
             mock.patch.object(remote_agent, 'keyring_unlocked', return_value=True), \
             mock.patch.object(remote_agent, 'secret_available', return_value=True), \
             mock.patch.object(remote_agent, 'agent_status', side_effect=['empty', 'ready']), \
             mock.patch.object(remote_agent.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run:
            remote_agent.register(self.runtime)
        command, kwargs = run.call_args
        self.assertEqual(command[0][0], remote_agent.SSH_ADD)
        self.assertEqual(kwargs['env']['SSH_ASKPASS'], str(Path(remote_agent.__file__).resolve()))
        self.assertEqual(kwargs['env']['XDG_RUNTIME_DIR'], str(self.runtime))
        self.assertEqual(kwargs['env']['DBUS_SESSION_BUS_ADDRESS'], f'unix:path={self.runtime}/bus')
        self.assertNotIn('focodex', ''.join(command[0]))

    def test_register_reports_wrong_passphrase_without_exposing_it(self):
        key = self.runtime / 'id_ed25519'; key.write_text('private'); key.chmod(0o600)
        config = self.runtime / 'settings.json'
        config.write_text(json.dumps({'version': 1, 'privateKey': str(key),
                                      'secretAttributes': {'service': 'focodex'}})); config.chmod(0o600)
        with mock.patch.object(remote_agent, 'CONFIG', config), \
             mock.patch.object(remote_agent, 'keyring_unlocked', return_value=True), \
             mock.patch.object(remote_agent, 'secret_available', return_value=True), \
             mock.patch.object(remote_agent, 'agent_status', return_value='empty'), \
             mock.patch.object(remote_agent.subprocess, 'run', return_value=mock.Mock(returncode=1, stderr=b'incorrect passphrase')):
            with self.assertRaisesRegex(ValueError, 'パスフレーズとSSH秘密鍵') as error:
                remote_agent.register(self.runtime)
        self.assertNotIn('private', str(error.exception))

    def test_register_reports_askpass_failure_without_raw_stderr(self):
        key = self.runtime / 'id_ed25519'; key.write_text('private'); key.chmod(0o600)
        config = self.runtime / 'settings.json'
        config.write_text(json.dumps({'version': 1, 'privateKey': str(key),
                                      'secretAttributes': {'service': 'focodex'}})); config.chmod(0o600)
        with mock.patch.object(remote_agent, 'CONFIG', config), \
             mock.patch.object(remote_agent, 'keyring_unlocked', return_value=True), \
             mock.patch.object(remote_agent, 'secret_available', return_value=True), \
             mock.patch.object(remote_agent, 'agent_status', return_value='empty'), \
             mock.patch.object(remote_agent.subprocess, 'run', return_value=mock.Mock(returncode=1, stderr=b'ssh_askpass: failed')):
            with self.assertRaisesRegex(ValueError, 'Secret ServiceからSSH鍵パスフレーズ') as error:
                remote_agent.register(self.runtime)
        self.assertNotIn('ssh_askpass:', str(error.exception))

    def test_secret_available_discards_secret_value(self):
        key = self.runtime / 'id_ed25519'; key.write_text('private'); key.chmod(0o600)
        config = self.runtime / 'settings.json'
        config.write_text(json.dumps({'version': 1, 'privateKey': str(key),
                                      'secretAttributes': {'service': 'focodex'}})); config.chmod(0o600)
        with mock.patch.object(remote_agent, 'CONFIG', config), \
             mock.patch.object(remote_agent, 'keyring_unlocked', return_value=True), \
             mock.patch.object(remote_agent.Path, 'is_file', return_value=True), \
             mock.patch.object(remote_agent.subprocess, 'run', return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(remote_agent.secret_available())
        self.assertIs(run.call_args.kwargs['stdout'], remote_agent.subprocess.DEVNULL)
