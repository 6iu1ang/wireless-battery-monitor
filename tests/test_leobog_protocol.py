import types
import unittest
from unittest.mock import patch
from providers.leobog_protocol import REQUEST,decode_report,matches_interface,query_battery,LeobogProtocolError
from providers.leobog_alice75 import LeobogAlice75Provider

def packet(percent,flags=0):
    data=bytes((0x13,0x4A,1,0,2,percent,flags))+bytes(12)
    return data+bytes((sum(data)&255,))

class LeobogProtocolTests(unittest.TestCase):
    def info(self,**changes):
        data=dict(vid=0x3554,pid=0xFA09,page=0xFF02,usage=2,input_length=20,output_length=20,feature_length=0,manufacturer='Compx',product='2.4G Wireless Receiver',reports={'input':[0x13],'output':[0x13]})
        data.update(changes)
        return types.SimpleNamespace(**data)

    def test_fixed_getter_and_checksum(self):
        self.assertEqual(len(REQUEST),20)
        self.assertEqual(REQUEST[:5],bytes.fromhex('13 4A 01 00 00'))
        self.assertEqual(REQUEST[-1],0x5E)
        self.assertEqual(sum(REQUEST[:-1])&255,REQUEST[-1])

    def test_percent_and_native_charging_boolean(self):
        self.assertEqual(decode_report(packet(73))['battery_percent'],73)
        self.assertTrue(decode_report(packet(73,0x10))['charging'])
        self.assertFalse(decode_report(packet(73,0x11))['charging'])
        self.assertEqual(decode_report(packet(73,0x11))['charge_status'],'not_charging')

    def test_invalid_replies_rejected(self):
        good=packet(73);bad_crc=good[:-1]+bytes((good[-1]^1,))
        for value in [good[:8],bytes(20),bad_crc,packet(255),bytes((0x13,0xCA))+good[2:]]:
            with self.assertRaises(LeobogProtocolError):decode_report(value)

    def test_keyboard_and_other_vendor_collections_rejected(self):
        self.assertTrue(matches_interface(self.info()))
        for change in [dict(page=1,usage=6),dict(page=0xFF04,feature_length=8),dict(pid=0x010C),dict(input_length=64),dict(manufacturer='unknown'),dict(reports={'input':[6],'output':[6]})]:
            self.assertFalse(matches_interface(self.info(**change)))

    def test_wrong_interface_never_opened(self):
        with patch('hid.windows_hid.HidChannel') as channel:
            with self.assertRaises(LeobogProtocolError):query_battery(self.info(page=1))
            channel.assert_not_called()

    def test_validated_getter_writes_once_and_ignores_unrelated_response(self):
        with patch('hid.windows_hid.HidChannel') as channel:
            handle=channel.return_value.__enter__.return_value
            handle.read.side_effect=[TimeoutError(),bytes(20),packet(93,1)]
            self.assertEqual(query_battery(self.info())['battery_percent'],93)
            handle.write.assert_called_once()
            self.assertEqual(handle.write.call_args.args,(REQUEST,))
            self.assertTrue(all(0<call.args[0]<=1 for call in handle.read.call_args_list))

    def test_provider_uses_verified_reply_and_clears_on_timeout(self):
        provider=LeobogAlice75Provider()
        with patch.object(provider,'detect',return_value=[self.info()]),patch('providers.leobog_alice75.query_battery',side_effect=[decode_report(packet(93,1)),TimeoutError('mock timeout')]):
            state=provider.read_battery()
            self.assertEqual(state.battery_percent,93)
            self.assertFalse(state.estimated)
            self.assertFalse(state.charging)
            self.assertEqual(state.charge_status,'not_charging')
            self.assertIsNotNone(state.last_updated)
            failed=provider.read_battery()
        self.assertIsNone(failed.battery_percent)
        self.assertIsNone(failed.charging)
        self.assertIsNone(failed.connected)
        self.assertEqual(failed.connection_status,'unresponsive')

    def test_provider_absent_or_ambiguous_never_queries(self):
        for interfaces,status in [([], 'offline'),([self.info(),self.info()],'unsupported'),([self.info(page=1)],'unsupported')]:
            provider=LeobogAlice75Provider()
            with patch.object(provider,'detect',return_value=interfaces),patch('providers.leobog_alice75.query_battery') as query:
                state=provider.read_battery()
            query.assert_not_called()
            self.assertEqual(state.connection_status,status)
            self.assertIsNone(state.battery_percent)

    def test_unplug_after_success_clears_percentage(self):
        provider=LeobogAlice75Provider()
        with patch.object(provider,'detect',side_effect=[[self.info()],[]]),patch('providers.leobog_alice75.query_battery',return_value=decode_report(packet(93,1))):
            self.assertEqual(provider.read_battery().battery_percent,93)
            state=provider.read_battery()
        self.assertFalse(state.connected)
        self.assertFalse(state.receiver_connected)
        self.assertIsNone(state.battery_percent)

if __name__=='__main__':unittest.main()
