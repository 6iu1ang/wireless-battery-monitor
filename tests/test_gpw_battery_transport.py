"""Exercise USB/receiver selection and transitions without real device I/O."""
import unittest
from unittest.mock import patch

from hid.windows_hid import HidInterface
from providers.logitech_hidpp import LogitechProvider
from providers.gpw_protocol import session, GpwError


def interface(pid=0xC088, **changes):
    fields = dict(path=f'fixture-{pid:04x}', vid=0x046D, pid=pid,
                  manufacturer='Logitech', product='G Pro Wireless Gaming Mouse',
                  page=0xFF00, usage=2, input_length=20, output_length=20,
                  reports={'input': [0x11], 'output': [0x11]})
    fields.update(changes)
    return HidInterface(**fields)


class MouseChannel:
    def __init__(self, info, name=b'G Pro Wireless Gaming Mouse'):
        self.info, self.name = info, name
        self.writes, self.reports = [], []
        self.slot = 0xFF if info.pid == 0xC088 else 1
        # Different indices expose accidental cache reuse across transports.
        self.battery_index = 6 if info.pid == 0xC088 else 9

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def write(self, frame, timeout):
        self.writes.append(frame)
        if frame[1] != self.slot:
            raise AssertionError('Incorrect HID++ device address')
        index, function = frame[2], frame[3] >> 4
        if index == 0 and function == 1:
            payload = bytes((4, 2, frame[6]))
        elif index == 0 and function == 0:
            feature = int.from_bytes(frame[4:6], 'big')
            payload = bytes(({0x0005: 3, 0x1001: self.battery_index}.get(feature, 0),))
        elif index == 3 and function == 0:
            payload = bytes((len(self.name),))
        elif index == 3 and function == 1:
            payload = self.name[frame[4]:frame[4]+16]
        elif index == self.battery_index and function == 0:
            payload = bytes.fromhex('10 69 80') if self.slot == 0xFF else bytes.fromhex('0f 7b 00')
        else:
            raise AssertionError('Unexpected getter or stale feature index')
        self.reports.append(frame[:4] + payload.ljust(16, b'\0'))

    def read(self, timeout):
        if not self.reports:
            raise TimeoutError('fixture timeout')
        return self.reports.pop(0)


class GpwBatteryTransportTests(unittest.TestCase):
    def test_detect_only_exact_long_report_collections(self):
        wired, receiver = interface(), interface(0xC539)
        rejected = [interface(pid=0xC08B), interface(vid=0x1234),
                    interface(page=1, usage=2), interface(usage=1),
                    interface(input_length=7), interface(output_length=7),
                    interface(reports={'input': [0x10], 'output': [0x11]}),
                    interface(reports={'input': [0x11], 'output': []})]
        with patch('providers.logitech_hidpp.enumerate_interfaces', return_value=[wired, receiver]+rejected):
            self.assertEqual(LogitechProvider().detect(), [wired, receiver])
            self.assertEqual(LogitechProvider().detect(include_wired=False), [receiver])

    def test_wired_reads_without_receiver_at_address_ff(self):
        p = LogitechProvider()
        channel = MouseChannel(interface())
        with patch.object(p, 'detect', return_value=[channel.info]), patch('providers.logitech_hidpp.HidChannel', return_value=channel):
            state = p.read_battery()
        self.assertTrue(state.connected)
        self.assertFalse(state.receiver_connected)
        self.assertEqual(state.voltage_mv, 4201)
        self.assertEqual(state.battery_percent, 100)
        self.assertTrue(state.estimated)
        self.assertTrue(state.charging)
        self.assertIn('USB 有线', state.data_source)

    def test_wired_preferred_when_receiver_stays_connected(self):
        p = LogitechProvider()
        wired, receiver = interface(), interface(0xC539)
        with patch.object(p, 'detect', return_value=[receiver, wired]), patch('providers.logitech_hidpp.HidChannel', side_effect=MouseChannel) as opened:
            state = p.read_battery()
        opened.assert_called_once_with(wired)
        self.assertTrue(state.receiver_connected)
        self.assertTrue(state.charging)

    def test_switching_transports_resolves_features_again_and_cache_still_reads(self):
        p = LogitechProvider()
        receiver, wired = interface(0xC539), interface()
        samples = [[receiver], [receiver], [receiver, wired], [wired], [receiver]]
        with patch.object(p, 'detect', side_effect=samples), patch('providers.logitech_hidpp.HidChannel', side_effect=MouseChannel):
            states = [p.read_battery() for _ in samples]
        self.assertTrue(all(s.connected for s in states))
        self.assertEqual([s.voltage_mv for s in states], [3963, 3963, 4201, 4201, 3963])
        self.assertEqual([s.charging for s in states], [False, False, True, True, False])
        self.assertEqual(p.cache[1], 1)
        self.assertEqual(p.cache[2][0x1001], 9)

    def test_multiple_devices_in_selected_transport_are_rejected(self):
        for devices in ([interface(), interface(path='second-mouse')],
                        [interface(0xC539), interface(0xC539, path='second-receiver')]):
            p = LogitechProvider()
            with patch.object(p, 'detect', return_value=devices), patch('providers.logitech_hidpp.HidChannel') as opened:
                state = p.read_battery()
            opened.assert_not_called()
            self.assertEqual(state.connection_status, 'unresponsive')
            self.assertIsNone(state.battery_percent)

    def test_wrong_model_is_rejected_before_battery_queries(self):
        p = LogitechProvider()
        channel = MouseChannel(interface(), name=b'Other Logitech Mouse')
        with patch.object(p, 'detect', return_value=[channel.info]), patch('providers.logitech_hidpp.HidChannel', return_value=channel):
            state = p.read_battery()
        self.assertIsNone(state.battery_percent)
        self.assertIsNone(p.cache)
        self.assertFalse(any(frame[2] == channel.battery_index for frame in channel.writes))

    def test_unplug_and_timeout_clear_live_reading_and_cache(self):
        p = LogitechProvider()
        with patch.object(p, 'detect', side_effect=[[interface()], [], [interface()]]), patch('providers.logitech_hidpp.HidChannel', side_effect=[MouseChannel(interface()), TimeoutError('fixture timeout')]):
            self.assertTrue(p.read_battery().connected)
            unplugged = p.read_battery()
            timed_out = p.read_battery()
        self.assertEqual(unplugged.connection_status, 'offline')
        self.assertEqual(timed_out.connection_status, 'unresponsive')
        self.assertIsNone(timed_out.battery_percent)
        self.assertFalse(timed_out.receiver_connected)
        self.assertIsNone(p.cache)

    def test_settings_session_does_not_use_new_battery_transport(self):
        with patch('providers.logitech_hidpp.enumerate_interfaces', return_value=[interface()]), patch('hid.windows_hid.HidChannel') as opened:
            with self.assertRaises(GpwError), session(writable=True):
                self.fail('Wired settings have not been validated')
        opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
