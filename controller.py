"""Independent workers; keep live samples separate from labeled last readings."""
import json
from pathlib import Path
import queue
import threading
import time
from providers.base import BatteryState, now
from config.settings import RUNTIME
from config.battery_history import BatteryHistory, HISTORY_PATH


def read_once(providers, history_path=HISTORY_PATH):
    history = BatteryHistory(history_path)
    return [history.attach(provider.read_battery()) for provider in providers]

class LowBatteryGate:
    def __init__(self): self.alerted = set()

    def should_notify(self,state,threshold):
        p = state.battery_percent
        if state.uses_last_reading or state.connected is not True or p is None: return False
        if p>threshold+5:
            self.alerted.discard(state.device_id)
        # A verified low percentage remains actionable when charging is unknown (S99).
        low = p<=threshold and state.charging is not True and state.charge_status not in ('full','error')
        if not low or state.device_id in self.alerted: return False
        self.alerted.add(state.device_id)
        return True

class Controller:
    def __init__(self,providers,interval=60,debug=False,history_path=HISTORY_PATH):
        self.providers = providers
        self.interval = interval
        self.debug = debug
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.wakes = [threading.Event() for _ in providers]
        self.threads = []
        self.last_good = {}
        self.log_lock = threading.Lock()
        self.history = BatteryHistory(history_path)
        self.history_warning = None

    def start(self):
        for provider,wake in zip(self.providers,self.wakes):
            thread = threading.Thread(target=self._worker,args=(provider,wake),daemon=True,name=provider.last_state.device_id)
            thread.start()
            self.threads.append(thread)

    def _worker(self,provider,wake):
        while not self.stop_event.is_set():
            wake.clear()
            before = provider.last_state
            self.events.put(('reading',before.device_id))
            try:
                state = provider.read_battery()
            except Exception:
                # Unexpected exception text can contain raw paths; keep user output redacted.
                state = BatteryState(before.device_id,before.device_name,connection_status='unknown',error_message='读取失败；可手动刷新重试',observed_at=now())
            state = self.history.attach(state)
            # Keep a successful in-flight sample even if the window was closed
            # while its HID request was completing. Only UI delivery is skipped.
            if self.stop_event.is_set(): break
            if self.history.last_error and self.history_warning != self.history.last_error:
                self.history_warning = self.history.last_error
                self.events.put(('warning', self.history.last_error))
            elif not self.history.last_error:
                self.history_warning = None
            if state.last_updated:
                self.last_good[state.device_id] = state.last_updated
            else:
                state.last_updated = self.last_good.get(state.device_id)
            self.events.put(('state',state))
            if self.debug:
                try:
                    with self.log_lock:
                        path = RUNTIME/'logs'/'debug.jsonl'
                        path.parent.mkdir(parents=True,exist_ok=True)
                        if path.exists() and path.stat().st_size>1024*1024: path.write_text('',encoding='utf-8')
                        with path.open('a',encoding='utf-8') as fh: fh.write(json.dumps(state.to_dict(),ensure_ascii=False)+'\n')
                except OSError:
                    self.events.put(('warning','调试日志无法写入；设备监控继续运行'))
            wake.wait(self.interval)

    def refresh(self):
        for wake in self.wakes: wake.set()

    def configure(self,interval,debug):
        self.interval,self.debug = interval,debug
        self.refresh()

    def stop(self):
        self.stop_event.set()
        self.refresh()
        deadline = time.monotonic()+9
        for thread in self.threads: thread.join(max(0,deadline-time.monotonic()))
        return not any(t.is_alive() for t in self.threads)
