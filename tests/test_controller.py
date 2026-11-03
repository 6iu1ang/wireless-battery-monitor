import queue
import time
import unittest
from unittest.mock import patch
from controller import Controller
from providers.base import BatteryState

class FakeProvider:
    def __init__(self,id,delay=0):
        self.id,self.delay=id,delay
        self.count=0
        self.last_state=BatteryState(id,id)
    def read_battery(self):
        self.count+=1
        time.sleep(self.delay)
        if self.count>1: raise OSError('mock disconnect')
        return BatteryState(self.id,self.id,connected=True,battery_percent=42,last_updated='2026-10-21T23:00:00+08:00')

class ControllerTests(unittest.TestCase):
    def wait_state(self,c,id):
        until=time.monotonic()+2
        while time.monotonic()<until:
            kind,value=c.events.get(timeout=1)
            if kind=='state' and value.device_id==id:return value
        self.fail('worker deadline exceeded')

    def test_independent_workers_and_clear_stale_data(self):
        c=Controller([FakeProvider('slow',0.1),FakeProvider('fast')],60,history_path=None)
        c.start()
        try:
            state=self.wait_state(c,'fast')
            self.assertEqual(state.battery_percent,42)
            c.refresh()
            failed=self.wait_state(c,'fast')
            self.assertIsNone(failed.battery_percent)
            self.assertEqual(failed.display_percent,42)
            self.assertTrue(failed.uses_last_reading)
            self.assertEqual(failed.last_updated,state.last_updated)
        finally: self.assertTrue(c.stop())
        self.assertFalse(any(t.is_alive() for t in c.threads))

    def test_reading_failure_does_not_kill_worker(self):
        c=Controller([FakeProvider('x')],60,history_path=None)
        c.start()
        try:
            self.wait_state(c,'x')
            c.refresh()
            self.wait_state(c,'x')
            c.refresh()
            self.assertIsNone(self.wait_state(c,'x').battery_percent)
        finally: self.assertTrue(c.stop())

if __name__=='__main__': unittest.main()
