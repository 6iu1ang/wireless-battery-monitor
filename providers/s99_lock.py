"""Serialize S99 battery and configuration traffic, including other new instances."""
from contextlib import contextmanager
import threading

_lock = threading.RLock()

@contextmanager
def device_lock():
    with _lock:
        import ctypes as C
        from ctypes import wintypes as W
        kernel = C.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
        kernel.CreateMutexW.restype = W.HANDLE
        kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        kernel.ReleaseMutex.argtypes = [W.HANDLE]
        kernel.CloseHandle.argtypes = [W.HANDLE]
        handle = kernel.CreateMutexW(None, False, 'Local\\WirelessBatteryMonitor-S99-HID')
        if not handle: raise OSError('无法建立 S99 通信锁')
        acquired = False
        try:
            acquired = kernel.WaitForSingleObject(handle, 1000) in (0, 0x80)
            if not acquired: raise TimeoutError('S99 正在处理另一项操作，请稍后刷新')
            yield
        finally:
            if acquired: kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
