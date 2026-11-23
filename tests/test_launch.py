import os
import unittest
from unittest.mock import Mock, patch
import launch


class LaunchTests(unittest.TestCase):
    def run_mode(self, args, diagnostic):
        service = Mock(server_port=8765)
        with patch.dict(os.environ, {'AUDIT_AI':'off'}, clear=False), patch('server.create_server', return_value=service) as create, patch('app.model.diagnostic', return_value=diagnostic) as check:
            launch.main(['--no-browser', *args])
            return os.environ['AUDIT_AI'], os.environ['AUDIT_VISUAL_OCR'], create.call_count, check.call_count

    def test_default_enables_codex_despite_old_off_setting(self):
        self.assertEqual(self.run_mode([], {'executable_found':True,'login':'已登录'}), ('codex','on',1,1))

    def test_explicit_offline_does_not_require_login(self):
        self.assertEqual(self.run_mode(['--offline'], {}), ('off','off',1,0))

    def test_missing_or_logged_out_cli_does_not_silently_launch(self):
        for diagnostic in ({'executable_found':False}, {'executable_found':True,'login':'未登录'}):
            with patch.dict(os.environ, {}, clear=False), patch('server.create_server') as create, patch('app.model.diagnostic', return_value=diagnostic), self.assertRaises(SystemExit):
                try: launch.main(['--no-browser'])
                finally: create.assert_not_called()
