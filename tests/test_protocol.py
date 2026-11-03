"""Simulated protocol faults: never communicates with real hardware."""
import unittest
from unittest.mock import patch
from providers.logitech_hidpp import HidppClient, ProtocolError, decode_battery, LogitechProvider
from providers.vgn_s99 import VgnS99Provider
from providers.voltage import estimate_percent,POINTS
from providers.base import BatteryState
from controller import LowBatteryGate
from config.settings import validate

class Channel:
    def __init__(self,build): self.build=build; self.reports=[]; self.writes=[]
    def write(self,payload,timeout):
        self.writes.append(payload)
        self.reports.extend(self.build(payload))
    def read(self,timeout):
        if not self.reports: raise TimeoutError('mock timeout')
        return self.reports.pop(0)

def reply(request,payload=b'\x32\0\0'):
    return request[:4]+payload.ljust(16,b'\0')

class ProtocolTests(unittest.TestCase):
    def test_strict_response_matching(self):
        def build(r):
            valid=reply(r)
            wrong_slot=bytearray(valid);wrong_slot[1]=2
            wrong_fn=bytearray(valid);wrong_fn[3]^=0x10
            wrong_sw=bytearray(valid);wrong_sw[3]^=1
            return [bytes(wrong_slot),bytes(wrong_fn),bytes(wrong_sw),valid[:6],valid]
        ch=Channel(build)
        self.assertEqual(HidppClient(ch).request(0,0)[0],50)
        self.assertEqual(len(ch.writes),1)

    def test_correlated_error_rejected(self):
        ch=Channel(lambda r:[bytes([0x11,r[1],0xFF,r[2],r[3],3])+bytes(14)])
        with self.assertRaises(ProtocolError): HidppClient(ch).request(0,0)

    def test_other_client_error_ignored(self):
        ch=Channel(lambda r:[bytes([0x11,2,0xFF,r[2],r[3],3])+bytes(14),reply(r)])
        self.assertEqual(HidppClient(ch).request(0,0)[0],50)

    def test_ping_echo_must_match(self):
        ch=Channel(lambda r:[reply(r,b'\4\2\0')])
        with self.assertRaises(ProtocolError): HidppClient(ch).ping()

    def test_disallowed_functions_never_written(self):
        ch=Channel(lambda r:[]);c=HidppClient(ch)
        with self.assertRaises(ValueError): c.request(9,0)
        with self.assertRaises(ValueError): c.request(0,2)
        with self.assertRaises(ValueError): c.feature(0x2201)
        self.assertFalse(ch.writes)

    def test_timeout_does_not_retry_forever(self):
        ch=Channel(lambda r:[])
        with self.assertRaises(TimeoutError): HidppClient(ch).request(0,0)
        self.assertEqual(len(ch.writes),1)

    def test_unified_level_only_not_zero_percent(self):
        decoded=decode_battery(0x1004,b'\0\4\0',b'\x0F\1')
        self.assertIsNone(decoded['battery_percent'])
        self.assertEqual(decoded['battery_level'],'高')

    def test_unified_explicit_percentage_zero_is_real(self):
        self.assertEqual(decode_battery(0x1004,b'\0\1\0',b'\x0F\3')['battery_percent'],0)

    def test_unified_missing_caps_rejected(self):
        with self.assertRaises(ProtocolError): decode_battery(0x1004,b'\x50\4\0')

    def test_invalid_percentage_and_enum(self):
        for payload in [b'\xFF\4\0',b'\x32\3\0',b'\x32\4\xFF',b'\1']:
            with self.assertRaises(ProtocolError): decode_battery(0x1004,payload,b'\x0F\3')

    def test_legacy_zero_unknown(self):
        self.assertIsNone(decode_battery(0x1000,b'\0\0\0')['battery_percent'])

    def test_legacy_charge_status(self):
        self.assertTrue(decode_battery(0x1000,b'\x32\0\1')['charging'])
        self.assertEqual(decode_battery(0x1000,b'\x64\0\3')['charge_status'],'full')

    def test_voltage_mapping_is_explicit_estimate(self):
        value=decode_battery(0x1001,b'\x0F\x7B\0')
        self.assertEqual(value['voltage_mv'],3963)
        self.assertEqual(value['battery_percent'],76)
        self.assertTrue(value['estimated'])
        self.assertFalse(value['charging'])

    def test_voltage_table_monotonic_and_bounded(self):
        values=[estimate_percent(v) for v in range(3000,4301)]
        self.assertEqual(values,sorted(values))
        self.assertTrue(all(0<=p<=100 for p in values))
        for v,p in POINTS: self.assertEqual(estimate_percent(v),p)
        with self.assertRaises(ValueError): estimate_percent(-1)

    def test_invalid_voltage_rejected(self):
        with self.assertRaises(ProtocolError): decode_battery(0x1001,b'\0\0\0')

    def test_offline_cannot_contain_live_percent(self):
        with self.assertRaises(ValueError): BatteryState('x','x',connected=False,battery_percent=50)

    def test_failure_discards_last_reading(self):
        p=LogitechProvider()
        p.last_state=BatteryState('logitech_gpw','GPW',connected=True,battery_percent=76)
        with patch.object(p,'detect',return_value=[]): state=p.read_battery()
        self.assertIsNone(state.battery_percent)
        self.assertEqual(state.connection_status,'offline')

    def test_keyboard_present_never_fabricates_online_or_battery(self):
        p=VgnS99Provider()
        with patch.object(p,'detect',return_value=[object()]): state=p.read_battery()
        self.assertIsNone(state.connected)
        self.assertIsNone(state.battery_percent)
        self.assertEqual(state.connection_status,'unsupported')

    def test_keyboard_removed_is_offline(self):
        p=VgnS99Provider()
        with patch.object(p,'detect',return_value=[]): state=p.read_battery()
        self.assertFalse(state.receiver_connected)
        self.assertFalse(state.connected)
        self.assertEqual(state.connection_status,'offline')

    def test_low_notification_deduplicated(self):
        gate=LowBatteryGate()
        state=BatteryState('m','m',connected=True,battery_percent=20,charging=False,charge_status='discharging')
        self.assertTrue(gate.should_notify(state,20))
        self.assertFalse(gate.should_notify(state,20))
        state.battery_percent=26
        self.assertFalse(gate.should_notify(state,20))
        state.battery_percent=19
        self.assertTrue(gate.should_notify(state,20))

    def test_charging_and_unknown_never_trigger_low_alarm(self):
        gate=LowBatteryGate()
        self.assertFalse(gate.should_notify(BatteryState('x','x'),20))
        self.assertFalse(gate.should_notify(BatteryState('x','x',connected=True,battery_percent=5,charging=True),20))

    def test_configuration_limits_and_default_no_autostart(self):
        for settings in [{'refresh_seconds':29},{'refresh_seconds':1801},{'low_threshold':0},{'low_threshold':True},{'tray_icons':'none'}]:
            with self.assertRaises(ValueError): validate(settings)
        self.assertNotIn('autostart',validate({}))

if __name__=='__main__': unittest.main()
