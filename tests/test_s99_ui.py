"""Offline editor checks: mock the device, exercise real Tk and profile files."""
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
from providers.s99_settings import decode_settings
from ui.s99_settings import S99SettingsWindow

class S99EditorTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=Path('runtime/temp'))
        self.root=tk.Tk();self.root.withdraw()
        raw=bytearray(48);raw[1]=6;raw[13]=60;raw[20]=255
        self.snapshot={'raw':bytes(raw),'values':decode_settings(raw),'receiver':'test'}
        self.api=Mock();self.api.read_settings.return_value=self.snapshot
        with patch('ui.s99_settings.S99Settings',return_value=self.api),patch('ui.s99_settings.ROOT',Path(self.folder.name)):
            self.panel=S99SettingsWindow(self.root)
        self.panel.window.withdraw()
        end=time.monotonic()+2
        while self.panel.busy and time.monotonic()<end:self.root.update();time.sleep(.01)
        self.assertFalse(self.panel.busy)

    def tearDown(self):
        self.panel.close();self.root.destroy();self.folder.cleanup()

    def test_unchanged_six_key_sentinel_is_not_normalized_or_written(self):
        self.panel.apply_settings()
        self.api.apply_settings.assert_not_called()

    def test_profile_roundtrip_only_changes_editor(self):
        path=Path(self.folder.name)/'profile.json'
        self.panel.variables['brightness'].set('4')
        with patch('ui.s99_settings.filedialog.asksaveasfilename',return_value=str(path)):
            self.panel.export_profile()
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['settings']['brightness'],4)
        self.panel.variables['brightness'].set('0')
        with patch('ui.s99_settings.filedialog.askopenfilename',return_value=str(path)):
            self.panel.import_profile()
        self.assertEqual(self.panel.variables['brightness'].get(),'4')
        self.api.apply_settings.assert_not_called();self.api.install_macros.assert_not_called()

    def test_invalid_profile_does_not_partially_change_editor(self):
        path=Path(self.folder.name)/'bad.json'
        obj={'schema':1,'model':'VGN S99 2.4G','settings':self.snapshot['values']|{'brightness':4},'macros':{'wrong':'type'}}
        path.write_text(json.dumps(obj),encoding='utf-8')
        with patch('ui.s99_settings.filedialog.askopenfilename',return_value=str(path)):
            self.panel.import_profile()
        self.assertEqual(self.panel.variables['brightness'].get(),'0')
        self.assertIn('导入失败',self.panel.status.cget('text'))

    def test_create_multiple_macros_and_atomic_local_save(self):
        self.panel.new_macro()
        self.panel.event_action.set('按下');self.panel.add_event()
        self.panel.event_action.set('释放');self.panel.add_event()
        self.panel.new_macro();self.assertEqual(len(self.panel.macros),2)
        self.panel.delete_macro();self.assertEqual(len(self.panel.macros),1)
        self.panel.save_macros()
        self.assertEqual(len(json.loads(self.panel.local_path.read_text(encoding='utf-8'))),1)
        self.api.install_macros.assert_not_called()

    def test_cannot_close_during_hardware_transaction(self):
        self.panel.busy=True
        try:self.assertFalse(self.panel.close());self.assertTrue(self.panel.window.winfo_exists())
        finally:self.panel.busy=False

if __name__=='__main__':unittest.main()
