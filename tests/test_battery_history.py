"""Sleep display, restart cache and UI regressions; no HID communication."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from config.battery_history import BatteryHistory
from controller import Controller, LowBatteryGate, read_once
from providers.base import BatteryReading, BatteryState
from ui.cards import DeviceCard
from ui.dashboard import Dashboard
from ui.tray import DEVICES, combined_details, fit_tooltip, tooltip_line

TIME='2026-10-25T14:25:12+08:00'


def success(key='logitech_gpw',percent=62):
    return BatteryState(key,key,connected=True,receiver_connected=True,connection_status='online',
                        battery_percent=percent,estimated=True,voltage_mv=3866,last_updated=TIME,
                        charging=True,charge_status='charging',data_source='fixture')


class HistoryTests(unittest.TestCase):
    def test_sleep_preserves_display_but_never_current_charge_or_timestamp(self):
        history=BatteryHistory(None);history.attach(success())
        asleep=history.attach(BatteryState('logitech_gpw','GPW',receiver_connected=True,
                                          connection_status='unresponsive',observed_at='2026-10-25T16:00:00+08:00'))
        self.assertIsNone(asleep.battery_percent);self.assertIsNone(asleep.charging)
        self.assertEqual(asleep.display_battery,'约 62%');self.assertEqual(asleep.display_updated,TIME)
        self.assertTrue(asleep.uses_last_reading);self.assertEqual(asleep.charge_status,'unknown')

    def test_wake_replaces_cached_value_and_returns_to_live_display(self):
        history=BatteryHistory(None);history.attach(success())
        state=history.attach(replace(success(percent=59),last_updated='2026-10-25T16:00:00+08:00'))
        self.assertEqual(state.display_percent,59);self.assertFalse(state.uses_last_reading)
        self.assertEqual(history.readings['logitech_gpw'].recorded_at,state.last_updated)

    def test_disconnect_and_unsupported_keep_cached_percentage_and_status(self):
        history=BatteryHistory(None);history.attach(success())
        for status in ('offline','unsupported'):
            state=history.attach(BatteryState('logitech_gpw','GPW',connection_status=status))
            self.assertEqual(state.display_percent,62);self.assertTrue(state.uses_last_reading)
            self.assertEqual(state.connection_status,status);self.assertIsNone(state.connected)
        resumed=history.attach(BatteryState('logitech_gpw','GPW',connection_status='unresponsive'))
        self.assertEqual(resumed.display_percent,62)

    def test_no_success_yet_remains_unknown_and_devices_do_not_share_cache(self):
        history=BatteryHistory(None);history.attach(success())
        other=history.attach(BatteryState('vgn_s99','S99',connection_status='unresponsive'))
        self.assertEqual(other.display_battery,'未知');self.assertFalse(other.uses_last_reading)

    def test_zero_percent_and_level_only_are_retained(self):
        history=BatteryHistory(None);history.attach(success(percent=0))
        asleep=history.attach(BatteryState('logitech_gpw','GPW',connection_status='unresponsive'))
        self.assertEqual(asleep.display_percent,0)
        history.attach(replace(success(),battery_percent=None,battery_level='低'))
        asleep=history.attach(BatteryState('logitech_gpw','GPW',connection_status='unresponsive'))
        self.assertEqual(asleep.display_battery,'低');self.assertIsNone(asleep.display_percent)

    def test_restart_loads_timestamp_value_estimate_and_voltage(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            path=Path(folder)/'history.json'
            BatteryHistory(path).attach(success())
            state=BatteryHistory(path).attach(BatteryState('logitech_gpw','GPW',connection_status='unresponsive'))
            self.assertEqual(state.display_percent,62);self.assertEqual(state.display_updated,TIME)
            self.assertTrue(state.display_estimated);self.assertEqual(state.last_reading.voltage_mv,3866)
            self.assertIsNone(state.charging)

    def test_corrupt_or_invalid_cache_is_ignored_without_fabricating_readings(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            path=Path(folder)/'history.json'
            for content in ('{','[]',json.dumps({'schema':1,'readings':{'x':{'battery_percent':101}}})):
                path.write_text(content,encoding='utf-8')
                self.assertFalse(BatteryHistory(path).readings)

    def test_old_low_value_never_alerts_or_resets_alert_gate(self):
        history=BatteryHistory(None);history.attach(success(percent=5))
        state=history.attach(BatteryState('logitech_gpw','GPW',connection_status='unresponsive'))
        gate=LowBatteryGate()
        self.assertFalse(gate.should_notify(state,20));self.assertFalse(gate.alerted)

    def test_combined_and_tooltip_include_last_value_without_claiming_online(self):
        history=BatteryHistory(None)
        states={key:history.attach(success(key,p)) for (_,key,_),p in zip(DEVICES,(62,51,93))}
        states['vgn_s99']=history.attach(BatteryState('vgn_s99','S99',connection_status='unresponsive'))
        percent,_,tip=combined_details(states)
        self.assertEqual(percent,51);self.assertIn('上次',tip);self.assertIn('10-25 14:25',tip)
        self.assertIn('无响应/休眠',tip);self.assertIn('Alice75',fit_tooltip(tip))
        self.assertIsNone(states['vgn_s99'].connected)

    def test_all_sleeping_tooltips_fit_windows_utf16_limit(self):
        history=BatteryHistory(None)
        for _,key,_ in DEVICES:history.attach(success(key))
        states={key:history.attach(BatteryState(key,title,connection_status='unresponsive')) for _,key,title in DEVICES}
        _,_,tip=combined_details(states)
        self.assertEqual(fit_tooltip(tip),tip)

    def test_independent_writers_preserve_all_devices_and_newest_timestamps(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            path=Path(folder)/'history.json'
            # All objects start before the first writer has saved a device.
            first,second,third=BatteryHistory(path),BatteryHistory(path),BatteryHistory(path)
            first.attach(success('logitech_gpw',62))
            second.attach(success('vgn_s99',51))
            third.attach(success('leobog_alice75',93))
            first.attach(replace(success('logitech_gpw',59),last_updated='2026-10-27T16:00:00+08:00'))
            second.attach(success('logitech_gpw',70))  # stale writer must not regress GPW
            saved=BatteryHistory(path)
            self.assertEqual(set(saved.readings),{'logitech_gpw','vgn_s99','leobog_alice75'})
            self.assertEqual(saved.readings['logitech_gpw'].battery_percent,59)
            self.assertEqual(saved.readings['vgn_s99'].battery_percent,51)
            self.assertEqual(saved.readings['leobog_alice75'].battery_percent,93)

    def test_unresponsive_instance_can_load_another_writers_latest_reading(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            path=Path(folder)/'history.json'
            sleeping=BatteryHistory(path)
            BatteryHistory(path).attach(success('vgn_s99',48))
            state=sleeping.attach(BatteryState('vgn_s99','S99',connection_status='unresponsive'))
            self.assertEqual(state.display_percent,48);self.assertEqual(state.display_updated,TIME)

    def test_save_failure_keeps_memory_reports_error_and_retries(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            path=Path(folder)/'history.json'
            history=BatteryHistory(path)
            with patch.object(Path,'replace',side_effect=OSError('injected replacement error')):
                history.attach(success('vgn_s99',51))
            self.assertIn('未能保存',history.last_error)
            self.assertEqual(history.readings['vgn_s99'].battery_percent,51)
            self.assertFalse(list(Path(folder).glob('*.tmp')))
            history.attach(success('logitech_gpw',62))
            self.assertIsNone(history.last_error)
            self.assertEqual(set(BatteryHistory(path).readings),{'vgn_s99','logitech_gpw'})

    def test_legacy_cache_migrates_into_durable_config_without_dropping_new_entries(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            legacy=Path(folder)/'cache'/'last-battery.json'
            durable=Path(folder)/'config'/'last-battery.json'
            BatteryHistory(legacy).attach(success('vgn_s99',51))
            BatteryHistory(durable).attach(success('logitech_gpw',62))
            with patch('config.battery_history.HISTORY_PATH',durable),patch('config.battery_history.LEGACY_HISTORY_PATH',legacy):
                history=BatteryHistory(durable)
            self.assertEqual(set(history.readings),{'vgn_s99','logitech_gpw'})
            self.assertEqual(set(BatteryHistory(durable).readings),set(history.readings))
            self.assertTrue(legacy.exists())

    def test_read_once_saves_all_three_and_uses_their_history_after_restart(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            path=Path(folder)/'history.json'
            providers=[Mock(read_battery=Mock(return_value=success(key,p))) for (_,key,_),p in zip(DEVICES,(62,51,93))]
            states=read_once(providers,path)
            self.assertEqual(len(BatteryHistory(path).readings),3)
            for provider,(_,key,title) in zip(providers,DEVICES):
                provider.read_battery.return_value=BatteryState(key,title,connected=False,connection_status='offline')
            states=read_once(providers,path)
            self.assertEqual([s.display_percent for s in states],[62,51,93])
            self.assertTrue(all(s.uses_last_reading and s.connection_status=='offline' for s in states))
            self.assertEqual(combined_details({s.device_id:s for s in states})[0],51)

    def test_successful_read_finishing_during_shutdown_is_saved(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            path=Path(folder)/'history.json'
            started,release=threading.Event(),threading.Event()
            provider=Mock(last_state=BatteryState('vgn_s99','S99'))
            def read():
                started.set();release.wait(2)
                return success('vgn_s99',51)
            provider.read_battery.side_effect=read
            controller=Controller([provider],history_path=path)
            controller.start()
            try:
                self.assertTrue(started.wait(2))
                controller.stop_event.set();release.set()
                self.assertTrue(controller.stop())
                self.assertEqual(BatteryHistory(path).readings['vgn_s99'].battery_percent,51)
            finally:
                release.set();controller.stop()

    def test_verified_old_s99_report_is_recovered_with_original_date_only(self):
        from diagnostics.recover_battery_history import recover
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            folder=Path(folder)
            state=replace(success('vgn_s99',51),estimated=False,voltage_mv=None,
                          data_source='VGN S99 官方电量查询 / 0x1A')
            report={'settings_read':True,'keys_read':True,'cleanup':True,'states':{'vgn_s99':state.to_dict()}}
            (folder/'s99_ui_validation.json').write_text(json.dumps(report),encoding='utf-8')
            history=BatteryHistory(folder/'saved.json')
            result=recover(history,folder)
            self.assertEqual(history.readings['vgn_s99'].recorded_at,TIME)
            self.assertEqual(history.readings['vgn_s99'].battery_percent,51)
            self.assertIn('旧版实测记录',history.readings['vgn_s99'].data_source)
            self.assertIn('vgn_s99',result['recovered'])
            self.assertEqual(recover(history,folder)['recovered'],{})

    def test_recovery_rejects_unvalidated_probes_and_arbitrary_preview_files(self):
        from diagnostics.recover_battery_history import recover
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            folder=Path(folder)
            (folder/'s99_hardware_probe.json').write_text(json.dumps({'samples':[{'reply_validated':False,'battery_percent':99,'time':TIME}]}),encoding='utf-8')
            (folder/'preview.json').write_text(json.dumps({'states':{'vgn_s99':success('vgn_s99',99).to_dict()}}),encoding='utf-8')
            history=BatteryHistory(None)
            self.assertEqual(recover(history,folder)['recovered'],{})
            self.assertFalse(history.readings)

    def test_older_recovery_does_not_override_a_newer_live_sample(self):
        from diagnostics.recover_battery_history import recover
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            folder=Path(folder)
            (folder/'s99_hardware_probe.json').write_text(json.dumps({'samples':[{'reply_validated':True,'battery_percent':51,'time':TIME}]}),encoding='utf-8')
            history=BatteryHistory(None)
            history.attach(replace(success('vgn_s99',40),last_updated='2026-10-27T16:00:00+08:00'))
            self.assertEqual(recover(history,folder)['recovered'],{})
            self.assertEqual(history.readings['vgn_s99'].battery_percent,40)


class HistoryUiTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        self.card=DeviceCard(self.root,'GPW','mouse')

    def tearDown(self):
        self.root.destroy()

    def test_card_shows_last_reading_time_and_not_charging(self):
        history=BatteryHistory(None);history.attach(success())
        state=history.attach(BatteryState('logitech_gpw','GPW',connection_status='unresponsive'))
        self.card.render(state)
        self.assertEqual(self.card.value.cget('text'),'约 62%')
        self.assertIn('上次读数',self.card.status.cget('text'))
        self.assertIn('2026-10-25 14:25:12',self.card.note.cget('text'))
        self.assertNotIn('充电中',self.card.detail.cget('text'))

    def test_dashboard_refresh_keeps_value_in_panel_and_tray(self):
        previous=success()
        app=Dashboard.__new__(Dashboard)
        app.states={'logitech_gpw':previous};app.cards={'logitech_gpw':self.card}
        app.tray=Mock();app._update_overview=Mock()
        app.mark_reading('logitech_gpw')
        reading=app.states['logitech_gpw']
        self.assertEqual(reading.display_percent,62);self.assertTrue(reading.uses_last_reading)
        self.assertIn('上次',tooltip_line('GPW',reading));self.assertIn('上次电量',self.card.detail.cget('text'))

    def test_offline_card_keeps_value_and_truthful_receiver_status(self):
        history=BatteryHistory(None);history.attach(success())
        state=history.attach(BatteryState('logitech_gpw','GPW',connected=False,connection_status='offline'))
        self.card.render(state)
        self.assertEqual(self.card.value.cget('text'),'约 62%')
        self.assertIn('接收器离线',self.card.status.cget('text'))
        self.assertIn('上次读数',self.card.status.cget('text'))
        self.assertIn('接收器已断开',self.card.detail.cget('text'))


if __name__=='__main__':unittest.main()
