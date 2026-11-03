"""Real local GUI/HID lifecycle check. Writes only explicitly requested diagnostics in E:."""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import struct
import time
import tkinter as tk
import zlib
from config.settings import ROOT,prepare_environment
from ui.dashboard import Dashboard
from ui.tray import bind,user,gdi,BitmapHeader
from providers.logitech_hidpp import LogitechProvider
from providers.vgn_s99 import VgnS99Provider
from providers.leobog_alice75 import LeobogAlice75Provider

def resource_usage():
    kernel=C.WinDLL('kernel32',use_last_error=True)
    psapi=C.WinDLL('psapi',use_last_error=True)
    class Memory(C.Structure):
        _fields_=[('size',W.DWORD),('faults',W.DWORD)]+[(name,C.c_size_t) for name in ('peak_working','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]
    bind(kernel,'GetCurrentProcess',W.HANDLE)
    bind(kernel,'GetProcessHandleCount',W.BOOL,W.HANDLE,C.POINTER(W.DWORD))
    bind(psapi,'GetProcessMemoryInfo',W.BOOL,W.HANDLE,C.POINTER(Memory),W.DWORD)
    process=kernel.GetCurrentProcess()
    memory=Memory();memory.size=C.sizeof(Memory)
    psapi.GetProcessMemoryInfo(process,C.byref(memory),memory.size)
    handles=W.DWORD();kernel.GetProcessHandleCount(process,C.byref(handles))
    return dict(working_set_mib=round(memory.working/1024**2,2),handles=handles.value)

def screenshot(widget,path):
    hwnd=widget.winfo_id()
    bind(user,'GetClientRect',W.BOOL,W.HWND,C.POINTER(W.RECT))
    bind(user,'GetDC',W.HDC,W.HWND)
    bind(user,'ReleaseDC',C.c_int,W.HWND,W.HDC)
    bind(user,'PrintWindow',W.BOOL,W.HWND,W.HDC,W.UINT)
    rect=W.RECT();user.GetClientRect(hwnd,C.byref(rect))
    w,h=rect.right,rect.bottom
    source=user.GetDC(hwnd)
    dc=gdi.CreateCompatibleDC(source)
    bits=C.c_void_p()
    header=BitmapHeader(C.sizeof(BitmapHeader),w,-h,1,32,0,w*h*4,0,0,0,0)
    bitmap=gdi.CreateDIBSection(dc,C.byref(header),0,C.byref(bits),None,0)
    old=gdi.SelectObject(dc,bitmap)
    try:
        if not user.PrintWindow(hwnd,dc,3):raise OSError('PrintWindow failed')
        gdi.GdiFlush()
        raw=C.string_at(bits,w*h*4)
        rows=bytearray()
        for y in range(h):
            rows.append(0)
            row=raw[y*w*4:(y+1)*w*4]
            for i in range(0,len(row),4):rows.extend((row[i+2],row[i+1],row[i]))
        def chunk(kind,data):
            return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xFFFFFFFF)
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')
        path.write_bytes(png)
    finally:
        gdi.SelectObject(dc,old);gdi.DeleteObject(bitmap);gdi.DeleteDC(dc);user.ReleaseDC(hwnd,source)

def pump(root,seconds):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        root.update()
        time.sleep(0.025)

def run():
    prepare_environment()
    results=[]
    for cycle in range(3):
        root=tk.Tk()
        app=Dashboard(root,[LogitechProvider(),VgnS99Provider(),LeobogAlice75Provider()],hidden=True)
        pump(root,2)
        assert len(app.states)==3,'all provider results must arrive'
        assert app.states['logitech_gpw'].connection_status in ('online','unresponsive'),'GPW must report a truthful hardware state'
        if app.states['logitech_gpw'].connected is not True:
            assert app.states['logitech_gpw'].battery_percent is None,'unresponsive GPW must not retain old battery'
        keyboard=app.states['vgn_s99']
        if keyboard.connected is True:
            assert type(keyboard.battery_percent) is int and 0<=keyboard.battery_percent<=100
            assert keyboard.estimated is False,'S99 percentage comes from protocol, not a voltage curve'
            assert type(keyboard.charging) is bool,'charge field follows local V HUB DevicePowerInfo.bean'
        else:
            assert keyboard.battery_percent is None,'unresponsive S99 must not retain old battery'
        alice=app.states['leobog_alice75']
        if alice.connected is True:
            assert type(alice.battery_percent) is int and not alice.estimated
            assert alice.charge_status in ('charging','not_charging')
        else:assert alice.battery_percent is None
        assert len(app.cards)==3
        record=dict(cycle=cycle+1,states={k:v.to_dict() for k,v in app.states.items()},resources=resource_usage(),tray_icons=len(app.tray.icons))
        for mode,wanted in [('mouse',{1}),('keyboard',{2,4}),('both',{1,2,4}),('combined',{3})]:
            app.tray.update(app.states,mode)
            assert set(app.tray.icons)==wanted,'tray selection must match settings'
        app.tray.update(app.states,rebuild=True)
        record['tray_mode_switch_and_rebuild']=True
        if cycle==0:
            # Capture only this application's own window, never the desktop or other apps.
            app.show();pump(root,0.2)
            bounds=app.card_canvas.bbox('all')
            app.card_canvas.yview_moveto(1);pump(root,0.1)
            assert app.card_canvas.yview()[1]>=0.99,'third card must be reachable'
            record['scrollable_three_cards']=bool(bounds)
            app.card_canvas.yview_moveto(0);pump(root,0.1)
            screenshot(root,ROOT/'diagnostics'/'dashboard.png')
            app.on_close();root.update()
            assert root.state()=='withdrawn' and not app.quitting,'close must keep tray alive'
            start_cpu=time.process_time();start_wall=time.monotonic()
            pump(root,8)
            record['idle_cpu_percent_one_core']=round(100*(time.process_time()-start_cpu)/(time.monotonic()-start_wall),2)
            record['after_idle_resources']=resource_usage()
        app.refresh()
        assert all(s.battery_percent is None for s in app.states.values()),'refresh must hide old percentages'
        pump(root,1)
        assert app.states['logitech_gpw'].connection_status in ('online','unresponsive')
        # Resume event routing is simulated; actual Windows sleep is not performed.
        app.events.put(('resume',None));pump(root,1)
        assert app.states['logitech_gpw'].connection_status in ('online','unresponsive')
        if cycle==2:
            app.settings['close_to_tray']=False
            app.on_close()
            assert app.quitting
            record['close_window_exit_mode']=True
        else:app.quit()
        assert not app.tray.icons and app.tray.hwnd is None,'tray resources must be removed'
        assert not any(t.is_alive() for t in app.controller.threads),'workers must stop'
        record['cleanup_ok']=True
        record['after_close_resources']=resource_usage()
        results.append(record)
    (ROOT/'diagnostics'/'gui_validation.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__':run()
