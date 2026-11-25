"""Portable install/startup contracts; no real login or Salesforce requests."""
import errno
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import install_local_service as installer
import serve_local


class InstallationChecks(unittest.TestCase):
    def test_installer_uses_invoking_python_and_teammates_paths(self):
        with tempfile.TemporaryDirectory(prefix='teammate-install-') as folder:
            home = Path(folder) / 'a teammate'
            app = home / 'Library/Application Support/com.zendesk.pse-split-automation'
            agents = home / 'Library/LaunchAgents'
            plist = agents / (installer.LABEL + '.plist')
            sf = str(home / 'tools/bin/sf')
            python = str(home / 'python/bin/python3')
            auth = SimpleNamespace(returncode=0, stdout=json.dumps({'result': {
                'accessToken': 'SYNTHETIC_TEST_TOKEN', 'instanceUrl': 'https://example.invalid'}}))
            with patch.multiple(installer, APP_ROOT=app, AGENT_DIR=agents, PLIST_PATH=plist, LOG_DIR=app/'logs'), \
                 patch.object(installer.sys, 'executable', python), \
                 patch.object(installer.shutil, 'which', return_value=sf), \
                 patch.object(installer.subprocess, 'run', return_value=auth) as command:
                installer.write_agent()
            definition = plistlib.loads(plist.read_bytes())
            self.assertEqual(definition['ProgramArguments'][0], python)
            self.assertEqual(definition['WorkingDirectory'], str(app))
            self.assertIn(str(app/'serve_local.py'), definition['ProgramArguments'])
            self.assertEqual(definition['RunAtLoad'], True)
            self.assertEqual(definition['KeepAlive'], True)
            self.assertNotIn('SYNTHETIC_TEST_TOKEN', plist.read_text())
            self.assertEqual(command.call_count, 1)
            self.assertIn('--target-org', command.call_args.args[0])
            self.assertNotIn('refresh.py', str(definition))

    def test_invalid_login_does_not_replace_app_or_stop_service(self):
        with patch.object(installer.sys, 'platform', 'darwin'), \
             patch.object(installer, 'write_agent', side_effect=SystemExit('Login needed')), \
             patch.object(installer, 'sync_app_copy') as sync, \
             patch.object(installer, 'launchctl') as launch:
            with self.assertRaises(SystemExit):
                installer.main()
            sync.assert_not_called()
            launch.assert_not_called()

    def test_session_timeout_explains_login_without_raw_output(self):
        with patch.object(installer.shutil, 'which', return_value='/example/sf'), \
             patch.object(installer.subprocess, 'run', side_effect=subprocess.TimeoutExpired('sf', 60)):
            with self.assertRaisesRegex(SystemExit, 'sf org login web'):
                installer.write_agent()

    def test_ipv4_only_machine_can_start(self):
        ipv4 = Mock()
        with patch.object(serve_local, 'ThreadingHTTPServer', return_value=ipv4), \
             patch.object(serve_local, 'IPv6ThreadingHTTPServer', side_effect=OSError(errno.EAFNOSUPPORT, 'No IPv6')):
            server, ipv6 = serve_local.create_servers(8773)
        self.assertIs(server, ipv4)
        self.assertIsNone(ipv6)
        ipv4.server_close.assert_not_called()

    def test_conflicting_ipv6_port_does_not_leave_half_started_app(self):
        ipv4 = Mock()
        with patch.object(serve_local, 'ThreadingHTTPServer', return_value=ipv4), \
             patch.object(serve_local, 'IPv6ThreadingHTTPServer', side_effect=OSError(errno.EADDRINUSE, 'Port occupied')):
            with self.assertRaises(OSError):
                serve_local.create_servers(8773)
        ipv4.server_close.assert_called_once()
