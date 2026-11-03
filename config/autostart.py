"""Only user opt-in creates a per-user Startup shortcut. No registry changes."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from config.settings import ROOT

OWNER = hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:10]

def startup_directory():
    shell = C.WinDLL('shell32',use_last_error=True)
    fn = shell.SHGetFolderPathW
    fn.argtypes = [W.HWND,C.c_int,W.HANDLE,W.DWORD,W.LPWSTR]
    fn.restype = W.LONG
    buf = C.create_unicode_buffer(260)
    if fn(None,7,None,0,buf)!=0: raise OSError('无法定位用户启动文件夹')
    return Path(buf.value)

def shortcut_path(): return startup_directory()/f'WirelessBatteryMonitor-{OWNER}.lnk'

def enabled(): return shortcut_path().exists()

def ps_quote(value): return "'"+str(value).replace("'","''")+"'"

def set_enabled(active, destination=None):
    path = Path(destination) if destination is not None else shortcut_path()
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    if not pythonw.exists(): raise OSError('找不到 pythonw.exe')
    script = ROOT/'main.py'
    args = f'-B "{script}" --hidden'
    # Verify ownership before replacing or deleting an existing shortcut.
    command = "$ErrorActionPreference='Stop'; $shell=New-Object -ComObject WScript.Shell; "
    command += f'$path={ps_quote(path)}; $target={ps_quote(pythonw)}; $arguments={ps_quote(args)}; '
    command += "if(Test-Path -LiteralPath $path){$old=$shell.CreateShortcut($path); if($old.TargetPath -ne $target -or $old.Arguments -ne $arguments){throw '启动快捷方式不属于本项目，已保留'}}; "
    if active:
        command += f'$link=$shell.CreateShortcut($path); $link.TargetPath=$target; $link.Arguments=$arguments; $link.WorkingDirectory={ps_quote(ROOT)}; $link.Description={ps_quote("Wireless Battery Monitor "+OWNER)}; $link.WindowStyle=7; $link.Save()'
    else:
        command += 'if(Test-Path -LiteralPath $path){Remove-Item -LiteralPath $path}'
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],capture_output=True,timeout=12,creationflags=0x08000000,env=os.environ.copy())
    if result.returncode: raise OSError('启动项设置失败：'+result.stderr.decode(errors='replace')[-350:])
