"""Durable per-device readings; merge concurrent writers without losing devices."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from config.settings import RUNTIME
from providers.base import BatteryReading

HISTORY_PATH = RUNTIME/'config'/'last-battery.json'
LEGACY_HISTORY_PATH = RUNTIME/'cache'/'last-battery.json'


def newer(candidate, previous, *, equal=False):
    if previous is None:
        return True
    first, second = datetime.fromisoformat(candidate.recorded_at), datetime.fromisoformat(previous.recorded_at)
    return first >= second if equal else first > second


@contextmanager
def history_lock(path):
    # A named mutex protects the read/merge/replace transaction, including --once
    # running alongside the GUI. Each thread also holds its history object's lock.
    if os.name != 'nt':
        yield
        return
    import ctypes as C
    from ctypes import wintypes as W
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
    kernel.CreateMutexW.restype = W.HANDLE
    kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
    kernel.ReleaseMutex.argtypes = [W.HANDLE]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    token = hashlib.sha256(str(path.resolve()).lower().encode()).hexdigest()[:16]
    handle = kernel.CreateMutexW(None, False, 'Local\\WirelessBatteryMonitor-History-'+token)
    if not handle:
        raise OSError('Cannot create battery history mutex')
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 2000) in (0, 0x80)
        if not acquired:
            raise OSError('Battery history is busy')
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


class BatteryHistory:
    def __init__(self, path=HISTORY_PATH):
        self.path = Path(path) if path is not None else None
        self.readings = {}
        self.lock = threading.Lock()
        self.last_error = None
        if self.path is None:
            return
        self.readings = self._load(self.path)
        if self.path == HISTORY_PATH and LEGACY_HISTORY_PATH.exists():
            self.import_readings(self._load(LEGACY_HISTORY_PATH))

    @staticmethod
    def _load(path):
        result = {}
        try:
            if path.stat().st_size > 65536:
                return result
            obj = json.loads(path.read_text(encoding='utf-8'))
            if obj.get('schema') != 1 or not isinstance(obj.get('readings'), dict):
                return result
            for key, value in obj['readings'].items():
                if not isinstance(key, str) or len(key) > 80:
                    continue
                try:
                    result[key] = BatteryReading(**value)
                except (ValueError, TypeError):
                    continue
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return result

    def _merge_and_save(self, additions):
        if self.path is None:
            for key, value in additions.items():
                if newer(value, self.readings.get(key), equal=True):
                    self.readings[key] = value
            return
        temporary = None
        try:
            with history_lock(self.path):
                saved = self._load(self.path)
                for key, value in self.readings.items():
                    if newer(value, saved.get(key)):
                        saved[key] = value
                for key, value in additions.items():
                    if newer(value, saved.get(key), equal=True):
                        saved[key] = value
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix='last-battery-', suffix='.tmp', dir=self.path.parent)
                temporary = Path(name)
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump({'schema':1,'readings':{k:asdict(v) for k,v in saved.items()}},stream,ensure_ascii=False,indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
                self.readings = saved
                self.last_error = None
        except OSError:
            # Preserve the latest values in memory and retry on the next sample.
            for key, value in additions.items():
                if newer(value, self.readings.get(key), equal=True):
                    self.readings[key] = value
            self.last_error = '电量历史未能保存，重启后可能丢失；请检查项目配置目录是否可写。'
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def import_readings(self, readings):
        """Merge independently verified old records, retaining their original dates."""
        with self.lock:
            self._merge_and_save(readings)

    def attach(self, state):
        with self.lock:
            reading = state.current_reading()
            if reading is not None:
                self._merge_and_save({state.device_id: reading})
            elif self.path is not None:
                # Pick up successful readings saved by another invocation even
                # when this process's own receiver currently does not respond.
                for key, value in self._load(self.path).items():
                    if newer(value, self.readings.get(key)):
                        self.readings[key] = value
            state.last_reading = self.readings.get(state.device_id)
            if not state.last_updated and state.last_reading is not None:
                state.last_updated = state.last_reading.recorded_at
            return state
