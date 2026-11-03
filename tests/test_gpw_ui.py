"""Real Tk controls, mocked GPW API. No hardware configuration is sent."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from test_gpw_settings import sample
from ui.gpw_settings import GpwSettingsWindow


class GpwEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path('runtime/temp'))
        self.root=tk.Tk();self.root.withdraw()
        self.api=Mock();self.snap=sample();self.api.read_settings.return_value=copy.deepcopy(self.snap)
        with patch.object(GpwSettingsWindow,'API_FACTORY',Mock(return_value=self.api)),patch('ui.gpw_settings.ROOT',Path(self.temp.name)):
            self.panel=GpwSettingsWindow(self.root)
        self.panel.window.withdraw();self.finish()

    def finish(self):
        end=time.monotonic()+3
        while self.panel.busy and time.monotonic()<end:self.root.update();time.sleep(.01)
        self.assertFalse(self.panel.busy)

    def tearDown(self):
        self.panel.close();self.root.destroy();self.temp.cleanup()

    def test_read_and_noop_never_apply(self):
        self.panel.apply_settings();self.api.apply_profile.assert_not_called()
        self.assertIn('没有修改',self.panel.status.cget('text'))

    def test_performance_edits_are_confined_to_changed_fields(self):
        self.api.apply_profile.return_value=self.snap
        self.panel.dpi[1].set('850');self.panel.apply_settings();self.finish()
        self.assertEqual(self.api.apply_profile.call_args.args[:2],(1,{'dpi':[400,850,1600,3200,6400]}))

    def test_only_edited_light_zone_is_written(self):
        self.api.apply_profile.return_value=self.snap
        self.panel.light_vars[1]['mode'].set('常亮');self.panel.light_mode_changed(1)
        self.panel.light_vars[1]['color'].set('#123456');self.panel.apply_settings();self.finish()
        changes=self.api.apply_profile.call_args.args[1]
        self.assertEqual(list(changes),['lights']);self.assertEqual(list(changes['lights']),[1])

    def test_key_combo_and_gshift_encoding(self):
        self.api.apply_profile.return_value=self.snap
        self.panel.layer.set('G-Shift 层');self.panel.key_tree.selection_set('3')
        self.panel.target.set('键盘：C');self.panel.modifiers['Ctrl'].set(True)
        self.panel.apply_key();self.finish()
        self.assertEqual(self.api.apply_profile.call_args.args[1],{'gkeys':{3:bytes.fromhex('80020106')}})

    def test_profile_selection_is_editor_only(self):
        self.panel.profile.set('3');self.panel.select_profile()
        self.api.activate_profile.assert_not_called();self.api.apply_profile.assert_not_called()
        self.assertEqual(self.panel.loaded_profile,3)

    def test_cancel_profile_switch_preserves_unsaved_values(self):
        self.panel.dpi[1].set('850');self.panel.profile.set('2')
        with patch('ui.gpw_settings.messagebox.askyesno',return_value=False):self.panel.select_profile()
        self.assertEqual(self.panel.profile.get(),'1');self.assertEqual(self.panel.dpi[1].get(),'850')

    def test_failed_write_clears_snapshot_and_requires_refresh(self):
        self.api.apply_profile.side_effect=ValueError('simulated failure')
        self.panel.dpi[1].set('850');self.panel.apply_settings();self.finish()
        self.assertIsNone(self.panel.snapshot)
        self.panel.apply_settings();self.assertEqual(self.api.apply_profile.call_count,1)

    def test_cannot_close_during_device_transfer(self):
        event=threading.Event()
        self.panel.run(lambda:event.wait(1),lambda _:None,'busy')
        self.assertFalse(self.panel.close());self.assertFalse(self.panel.closed)
        event.set();self.finish();self.assertTrue(self.panel.close())

    def test_macro_editor_local_save_and_binding_arguments(self):
        self.panel.new_macro();self.panel.add_event();self.panel.event_action.set('释放');self.panel.add_event()
        self.panel.save_macros()
        saved=json.loads(self.panel.local_path.read_text(encoding='utf-8'))
        self.assertEqual(saved[0]['events'][0],{'key':4,'modifiers':0,'action':'down','delay':50})
        self.api.install_macro.return_value=self.snap
        self.panel.install_macro();self.finish()
        self.assertEqual(self.api.install_macro.call_args.args[:3],(1,3,'keys'))

    def test_export_import_does_not_touch_hardware(self):
        path=Path(self.temp.name)/'gpw.json'
        self.panel.dpi[1].set('900')
        with patch('ui.gpw_settings.filedialog.asksaveasfilename',return_value=str(path)):self.panel.export_profile()
        self.panel.dpi[1].set('800')
        with patch('ui.gpw_settings.filedialog.askopenfilename',return_value=str(path)):self.panel.import_profile()
        self.assertEqual(self.panel.dpi[1].get(),'900');self.api.apply_profile.assert_not_called()

    def test_invalid_import_is_atomic(self):
        path=Path(self.temp.name)/'bad.json'
        self.panel.dpi[1].set('900')
        with patch('ui.gpw_settings.filedialog.asksaveasfilename',return_value=str(path)):self.panel.export_profile()
        obj=json.loads(path.read_text(encoding='utf-8'));obj['values']['name']='changed';obj['values']['dpi']=[0]*5
        path.write_text(json.dumps(obj),encoding='utf-8')
        name=self.panel.name.get()
        with patch('ui.gpw_settings.filedialog.askopenfilename',return_value=str(path)):self.panel.import_profile()
        self.assertEqual(self.panel.name.get(),name);self.assertIn('导入失败',self.panel.status.cget('text'))

    def test_light_controls_follow_supported_mode(self):
        self.panel.light_vars[0]['mode'].set('关闭');self.panel.light_mode_changed(0)
        _,color,_,period,bright=self.panel.light_controls[0]
        self.assertEqual(str(color.cget('state')),'disabled');self.assertEqual(str(period.cget('state')),'disabled')
        self.panel.light_vars[0]['mode'].set('呼吸');self.panel.light_mode_changed(0)
        self.assertEqual(str(color.cget('state')),'normal');self.assertEqual(str(bright.cget('state')),'normal')


if __name__=='__main__':unittest.main()
