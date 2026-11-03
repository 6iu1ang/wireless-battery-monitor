"""Wireless Battery Monitor — Python 3.10+, Windows, standard library only."""
import sys
sys.dont_write_bytecode = True
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
from config.settings import ROOT,prepare_environment

def main():
    prepare_environment()
    parser = argparse.ArgumentParser(description='Wireless Battery Monitor')
    parser.add_argument('--once',action='store_true',help='读取一次 JSON 后退出')
    parser.add_argument('--scan',action='store_true',help='只读枚举 HID 元数据')
    parser.add_argument('--hidden',action='store_true',help='启动后留在托盘')
    parser.add_argument('--smoke-seconds',type=int,default=0,help='GUI 自动退出冒烟测试')
    args = parser.parse_args()
    if sys.platform!='win32': parser.error('仅支持 Windows')
    if args.scan:
        from diagnostics.scanner import scan
        print(f'{len(scan())} HID collections; device_inventory.md')
        return 0
    from providers.logitech_hidpp import LogitechProvider
    from providers.vgn_s99 import VgnS99Provider
    from providers.leobog_alice75 import LeobogAlice75Provider
    providers = [LogitechProvider(),VgnS99Provider(),LeobogAlice75Provider()]
    if args.once:
        from controller import read_once
        print(json.dumps([s.to_dict() for s in read_once(providers)],ensure_ascii=False,indent=2))
        return 0
    kernel = C.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes = [C.c_void_p,C.c_int,C.c_wchar_p]
    kernel.CreateMutexW.restype = C.c_void_p
    kernel.CloseHandle.argtypes = [C.c_void_p]
    token = hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:10]
    mutex = kernel.CreateMutexW(None,False,'Local\\WirelessBatteryMonitor-'+token)
    exists = C.get_last_error()==183
    if not mutex: raise OSError('无法建立单实例锁')
    try:
        if exists:
            user=C.WinDLL('user32',use_last_error=True)
            user.FindWindowW.argtypes=[C.c_wchar_p,C.c_wchar_p]
            user.FindWindowW.restype=C.c_void_p
            user.PostMessageW.argtypes=[C.c_void_p,C.c_uint,C.c_size_t,C.c_ssize_t]
            hwnd=user.FindWindowW(None,'Wireless Battery Monitor Tray '+token)
            if hwnd:user.PostMessageW(hwnd,0x8000+24,0,0)
            return 0
        try: C.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError,OSError): pass
        import tkinter as tk
        from ui.dashboard import Dashboard
        root = tk.Tk()
        app = None
        try:
            app = Dashboard(root,providers,hidden=args.hidden)
            if args.smoke_seconds:
                if not 1<=args.smoke_seconds<=120: raise ValueError('smoke-seconds must be 1–120')
                root.after(args.smoke_seconds*1000,app.quit)
            root.mainloop()
        finally:
            if app and not app.quitting: app.quit()
            elif app is None:
                try: root.destroy()
                except tk.TclError: pass
    finally:
        kernel.CloseHandle(mutex)
    return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as exc:
        # A pythonw launch still reports startup errors; no implicit permanent log.
        C.windll.user32.MessageBoxW(None,str(exc),'Wireless Battery Monitor — 启动失败',0x10)
        raise
