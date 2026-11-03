import unittest
from providers.base import BatteryState
from ui.tray import DEVICES, combined_details, fit_tooltip, icon_ids


class TrayDeviceTests(unittest.TestCase):
    def states(self):
        return {key:BatteryState(key,title,connected=True,battery_percent=p,connection_status='online',
                               last_updated='2026-10-22T10:11:45+08:00',estimated=key=='logitech_gpw')
                for (_,key,title),p in zip(DEVICES,(76,51,93))}

    def test_three_device_tooltip_and_combined_minimum(self):
        percent,label,tip=combined_details(self.states())
        self.assertEqual(percent,51)
        self.assertEqual(label,'ALL')
        for text in ('GPW','S99','Alice75','93%','10:11:45'):self.assertIn(text,tip)
        self.assertEqual(tip.count('\n'),2)
        self.assertEqual(fit_tooltip(tip),tip)
        states=self.states();states['logitech_gpw'].battery_percent=30
        self.assertEqual(combined_details(states)[:2],(30,'ALL~'))

    def test_unknown_device_keeps_aggregate_unknown_without_hiding_other_values(self):
        states=self.states()
        states['vgn_s99']=BatteryState('vgn_s99','S99',connected=False,connection_status='offline')
        percent,_,tip=combined_details(states)
        self.assertIsNone(percent)
        self.assertIn('接收器断开',tip)
        self.assertIn('Alice75：93%',tip)
        self.assertIsNone(combined_details({})[0])

    def test_icon_selection_and_utf16_limit(self):
        self.assertEqual(icon_ids('combined'),{3})
        self.assertEqual(icon_ids('both'),{1,2,4})
        self.assertEqual(icon_ids('keyboard'),{2,4})
        self.assertEqual(icon_ids('mouse'),{1})
        self.assertLessEqual(len(fit_tooltip('电量😀'*100).encode('utf-16-le')),254)
