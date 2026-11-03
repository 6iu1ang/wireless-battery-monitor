import types
import unittest
from unittest.mock import patch
from providers.s99_protocol import REQUEST,HEADER,decode_report,decode_power,matches_interface,S99ProtocolError,query_battery
from providers.vgn_s99 import VgnS99Provider
from providers.base import BatteryState
from controller import LowBatteryGate

def report(percent):return bytes([4])+HEADER+bytes([percent])+bytes(55)

class S99ProtocolTests(unittest.TestCase):
    def interface(self,**changes):
        data=dict(vid=0x320F,pid=0x5088,page=0xFF1C,usage=0x92,input_length=64,output_length=64,product='VGN S99 2.4G Dongle',reports={'input':[4],'output':[4]})
        data.update(changes)
        return types.SimpleNamespace(**data)

    def test_request_is_one_fixed_documented_getter(self):
        self.assertEqual(len(REQUEST),64)
        self.assertEqual(REQUEST[:8],bytes.fromhex('04 00 00 1a 06 00 00 00'))
        self.assertEqual(REQUEST[8:],bytes(56))

    def test_percentage_byte_is_not_report_id_or_header(self):
        self.assertEqual(decode_report(report(83)),83)
        self.assertEqual(decode_report(report(0)),0)
        self.assertEqual(decode_report(report(100)),100)

    def test_invalid_or_unrelated_report_rejected(self):
        good=report(50)
        cases=[good[:14],bytes(64),good[:3]+bytes([6])+good[4:],good[:4]+bytes([24])+good[5:],report(255)]
        for case in cases:
            with self.subTest(case=case[:9].hex()):
                with self.assertRaises(S99ProtocolError):decode_report(case)

    def test_all_interface_attributes_required(self):
        self.assertTrue(matches_interface(self.interface()))
        for changes in [dict(pid=0x5055),dict(page=1),dict(usage=6),dict(product='VGN V98pro'),dict(input_length=8),dict(output_length=33),dict(reports={'input':[5],'output':[5]})]:
            self.assertFalse(matches_interface(self.interface(**changes)))

    def test_wrong_interface_cannot_open_any_channel(self):
        with patch('hid.windows_hid.HidChannel') as channel:
            with self.assertRaises(S99ProtocolError):query_battery(self.interface(page=1))
            channel.assert_not_called()

    def test_provider_uses_official_percentage_and_charge_fields(self):
        provider=VgnS99Provider()
        with patch.object(provider,'detect',return_value=[self.interface()]),patch('providers.vgn_s99.query_battery',return_value=(51,False)):
            state=provider.read_battery()
        self.assertTrue(state.connected)
        self.assertEqual(state.battery_percent,51)
        self.assertFalse(state.estimated)
        self.assertFalse(state.charging)
        self.assertEqual(state.charge_status,'not_charging')
        self.assertIsNotNone(state.last_updated)

    def test_timeout_clears_previous_successful_reading(self):
        provider=VgnS99Provider()
        with patch.object(provider,'detect',return_value=[self.interface()]),patch('providers.vgn_s99.query_battery',side_effect=[(51,False),TimeoutError('mock timeout')]):
            self.assertEqual(provider.read_battery().battery_percent,51)
            state=provider.read_battery()
        self.assertIsNone(state.battery_percent)
        self.assertIsNone(state.connected)
        self.assertTrue(state.receiver_connected)
        self.assertEqual(state.connection_status,'unresponsive')

    def test_charge_byte_follows_local_vhub_definition(self):
        self.assertEqual(decode_power(report(51)),(51,False))
        charging=bytearray(report(51));charging[9]=1
        self.assertEqual(decode_power(charging),(51,True))

    def test_multiple_receivers_are_never_blindly_queried(self):
        provider=VgnS99Provider()
        with patch.object(provider,'detect',return_value=[self.interface(),self.interface()]),patch('providers.vgn_s99.query_battery') as query:
            state=provider.read_battery()
        query.assert_not_called()
        self.assertIsNone(state.battery_percent)
        self.assertEqual(state.connection_status,'unsupported')

    def test_verified_low_keyboard_percentage_can_alert_with_unknown_charge(self):
        state=BatteryState('vgn_s99','VGN S99',connected=True,battery_percent=19,charging=None,charge_status='unknown')
        gate=LowBatteryGate()
        self.assertTrue(gate.should_notify(state,20))
        self.assertFalse(gate.should_notify(state,20))

if __name__=='__main__':unittest.main()
