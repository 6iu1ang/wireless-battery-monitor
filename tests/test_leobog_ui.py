"""Real Tk, mocked Alice hardware. No physical device configuration is sent."""
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock,patch
from providers.leobog_settings import decode_settings
from ui.leobog_settings import Alice75SettingsWindow,function_name

def sample():
    raw=bytearray(128);raw[10]=1;raw[24]=4;raw[58]=3;raw[59]=7;raw[-2:]=b'\x5a\xa5'
    palette=bytearray(512);palette[21:24]=b'\xee\0\xf1';palette[-2:]=b'\x5a\xa5'
    return {'raw':bytes(raw),'palette':bytes(palette),'values':decode_settings(raw,palette),'receiver':'fixture-only'}

class AliceEditorTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=Path('runtime/temp'))
        self.root=tk.Tk();self.root.withdraw();self.api=Mock();self.api.read_settings.return_value=sample()
        with patch.object(Alice75SettingsWindow,'API_FACTORY',Mock(return_value=self.api)),patch('ui.s99_settings.ROOT',Path(self.folder.name)):
            self.panel=Alice75SettingsWindow(self.root)
        self.panel.window.withdraw();self.finish()

    def finish(self):
        end=time.monotonic()+2
        while self.panel.busy and time.monotonic()<end:self.root.update();time.sleep(.01)
        self.assertFalse(self.panel.busy)

    def tearDown(self):self.panel.close();self.root.destroy();self.folder.cleanup()

    def test_noop_does_not_apply_or_normalize_values(self):
        self.panel.apply_settings();self.api.apply_settings.assert_not_called()

    def test_profile_roundtrip_is_editor_only(self):
        path=Path(self.folder.name)/'alice.json'
        self.panel.variables['sleep'].set('10 分钟')
        with patch('ui.s99_settings.filedialog.asksaveasfilename',return_value=str(path)):self.panel.export_profile()
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['model'],'LEOBOG Alice 75')
        self.panel.variables['sleep'].set('2 分钟')
        with patch('ui.s99_settings.filedialog.askopenfilename',return_value=str(path)):self.panel.import_profile()
        self.assertEqual(self.panel.variables['sleep'].get(),'10 分钟');self.api.apply_settings.assert_not_called()

    def test_s99_profile_is_rejected(self):
        path=Path(self.folder.name)/'wrong.json';path.write_text(json.dumps({'schema':1,'model':'VGN S99 2.4G'}),encoding='utf-8')
        with patch('ui.s99_settings.filedialog.askopenfilename',return_value=str(path)):self.panel.import_profile()
        self.assertIn('导入失败',self.panel.status.cget('text'));self.api.apply_settings.assert_not_called()

    def test_key_layer_change_requires_new_read_and_modifiers_encode(self):
        raw=bytearray(512);raw[-2:]=b'\x5a\xa5'
        snapshot={'raw':bytes(raw),'layer':0,'receiver':'fixture-only'}
        self.panel.show_keys(snapshot);self.panel.key_tree.selection_set('9');self.panel.target.set('C');self.panel.modifiers['Left Ctrl'].set(True)
        self.panel.layer.set('FN1 层');self.panel.apply_key();self.api.apply_key.assert_not_called()
        self.panel.layer.set('默认层');self.api.apply_key.return_value=snapshot
        self.panel.apply_key();self.finish()
        self.assertEqual(self.api.apply_key.call_args.args[:2],(9,bytes.fromhex('00 01 00 06')))

    def test_light_capabilities_and_per_mode_settings(self):
        self.assertEqual(str(self.panel.controls['speed'].cget('state')),'disabled')
        self.panel.variables['mode'].set('呼吸模式');self.panel.mode_changed()
        self.assertEqual(str(self.panel.controls['speed'].cget('state')),'readonly')
        self.assertEqual(function_name(bytes.fromhex('00 01 00 06')),'Left Ctrl + C')

    def test_macro_local_file_uses_alice_limits_and_does_not_write_device(self):
        self.panel.new_macro();self.panel.event_action.set('按下');self.panel.add_event()
        self.panel.event_action.set('释放');self.panel.add_event();self.panel.save_macros()
        self.assertEqual(self.panel.local_path.name,'alice75-macros.json')
        self.assertTrue(self.panel.local_path.exists());self.api.install_macros.assert_not_called()

if __name__=='__main__':unittest.main()
