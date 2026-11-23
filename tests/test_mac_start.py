import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import launch
from app import mac_codex, model


class MacStartupTests(unittest.TestCase):
    def test_desktop_needs_no_download_and_keeps_login(self):
        with tempfile.TemporaryDirectory() as home:
            executable = Path(home) / 'Applications/Codex.app/Contents/Resources/codex'
            executable.parent.mkdir(parents=True)
            executable.touch(mode=0o700)
            with patch('pathlib.Path.home', return_value=Path(home)), patch('shutil.which', return_value=None), patch('sys.platform','darwin'), patch.dict(os.environ, {'AUDIT_CODEX_PATH':''}), patch.object(model,'diagnostic',return_value={'login':'已登录'}), patch.object(mac_codex,'provision') as provision, patch('launch.subprocess.run') as login:
                launch.prepare_mac_model(model)
                self.assertEqual(model.executable(), str(executable))
                provision.assert_not_called()
                login.assert_not_called()

    def test_first_launch_download_and_interactive_login(self):
        with patch.dict(os.environ, {}), patch.object(mac_codex,'provision',return_value='/new/codex') as provision, patch('launch.subprocess.run') as login:
            adapter = Mock()
            adapter.executable.return_value = ''
            adapter.diagnostic.return_value = {'login':'未登录'}
            launch.prepare_mac_model(adapter)
            provision.assert_called_once()
            self.assertEqual(login.call_args.args[0], ['/new/codex','login'])

    def test_corrupted_archive_never_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'broken'
            target = Path(tmp) / 'codex'
            archive.write_bytes(b'incomplete')
            with self.assertRaisesRegex(ValueError, '校验失败'):
                mac_codex.unpack(archive, target, 'arm64')
            self.assertFalse(target.exists())

    def test_inference_probe_requires_fresh_correct_reply(self):
        with patch.object(model,'diagnostic',side_effect=lambda **kw:{'offline':False,'login':'已登录'}), patch.object(model,'uid',return_value='nonce'), patch.object(model,'infer',side_effect=[({'code':'old'},{}),({'code':'nonce'}, {})]) as infer:
            self.assertEqual(model.probe()['inference'],'模型回答与测试码不符')
            self.assertEqual(model.probe()['inference'],'真实推理通过')
            self.assertTrue(infer.call_args.kwargs['force'])
