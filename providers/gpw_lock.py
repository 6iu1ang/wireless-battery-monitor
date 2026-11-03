"""Serialize GPW battery and settings across threads and monitor processes."""
from contextlib import contextmanager
import threading

_lock = threading.RLock()


@contextmanager
def device_lock():
    import ctypes as C
    from ctypes import wintypes as W
    with _lock:
        kernel = C.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
        kernel.CreateMutexW.restype = W.HANDLE
        kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        kernel.ReleaseMutex.argtypes = [W.HANDLE]
        kernel.CloseHandle.argtypes = [W.HANDLE]
        handle = kernel.CreateMutexW(None, False, 'Local\\WirelessBatteryMonitor-GPW-HID')
        if not handle:
            raise OSError('无法建立 GPW 通信锁')
        acquired = False
        try:
            acquired = kernel.WaitForSingleObject(handle, 1000) in (0, 0x80)
            if not acquired:
                raise TimeoutError('GPW 正在处理其他操作，请稍后重试')
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
