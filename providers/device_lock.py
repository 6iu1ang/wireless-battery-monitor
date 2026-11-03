"""Named locks keep device-setting transfers separate from battery polling."""
from contextlib import contextmanager
import threading

_locks = {'alice75': threading.RLock()}

@contextmanager
def alice75_lock():
    with _locks['alice75']:
        import ctypes as C
        from ctypes import wintypes as W
        kernel=C.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.argtypes=[C.c_void_p,W.BOOL,W.LPCWSTR]
        kernel.CreateMutexW.restype=W.HANDLE
        kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]
        kernel.ReleaseMutex.argtypes=[W.HANDLE]
        kernel.CloseHandle.argtypes=[W.HANDLE]
        handle=kernel.CreateMutexW(None,False,'Local\\WirelessBatteryMonitor-Alice75-HID')
        if not handle:raise OSError('无法建立 Alice 75 通信锁')
        acquired=False
        try:
            acquired=kernel.WaitForSingleObject(handle,1000) in (0,0x80)
            if not acquired:raise TimeoutError('Alice 75 正在处理另一项操作，请稍后刷新')
            yield
        finally:
            if acquired:kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
