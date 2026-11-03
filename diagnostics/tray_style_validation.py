"""Synthetic icon style preview; no HID access or battery notifications."""
import base64
import ctypes as C
from ctypes import wintypes as W
import json
import queue
import struct
import tkinter as tk
import zlib
from config.settings import ROOT,prepare_environment
from diagnostics.gui_validation import screenshot,pump
from providers.base import BatteryState
from ui.tray import (Tray,DEVICES,render_icon_pixels,make_icon,user,kernel,bind)


def png(pixels,size=32):
    rows=bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            b,g,r,a=pixels[4*(y*size+x):4*(y*size+x)+4]
            rows.extend((*[min(255,round(c*255/a)) if a else 0 for c in (r,g,b)],a))
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',size,size,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')


def run():
    prepare_environment()
    root=tk.Tk()
    root.title('白框绿色电池托盘图标预览 · 示例数据')
    root.configure(bg='#f9f9f9')
    tk.Label(root,text='托盘图标 · 样式预览',bg='#f9f9f9',fg='#202123',
             font=('Microsoft YaHei UI',15,'bold')).pack(anchor='w',padx=24,pady=(20,4))
    tk.Label(root,text='以下为样式示例，非当前设备读数。每组显示 16 / 32 像素与放大预览。',
             bg='#f9f9f9',fg='#707173',font=('Microsoft YaHei UI',9)).pack(anchor='w',padx=24,pady=(0,16))
    images=[]
    samples=[(76,False,'正常'),(75,True,'估算'),(None,False,'未知'),(9,False,'低电量'),(0,False,'零电量'),(100,False,'满电量')]
    try:
        for background,foreground in [('#ffffff','#202123'),('#202123','#eeeeee')]:
            row=tk.Frame(root,bg=background,padx=20,pady=14)
            row.pack(fill='x',padx=24,pady=(0,10))
            for percent,estimate,title in samples:
                group=tk.Frame(row,bg=background,padx=12)
                group.pack(side='left',expand=True)
                tk.Label(group,text=title,bg=background,fg=foreground,font=('Microsoft YaHei UI',9)).pack(pady=(0,10))
                pixels=render_icon_pixels(percent,estimate,light=background=='#ffffff')
                assert len(pixels)==4096
                assert pixels[3]==0 and max(pixels[3::4])==255
                assert any(0<a<255 for a in pixels[3::4]),'rounded edges must have alpha coverage'
                icons=tk.Frame(group,bg=background)
                icons.pack()
                for size,data in [(16,render_icon_pixels(percent,estimate,light=background=='#ffffff',size=16)),(32,pixels)]:
                    image=tk.PhotoImage(data=base64.b64encode(png(data,size)))
                    images.append(image)
                    tk.Label(icons,image=image,bg=background,bd=0).pack(side='left',padx=3)
                large=tk.PhotoImage(data=base64.b64encode(png(pixels))).zoom(2)
                images.append(large)
                tk.Label(group,image=large,bg=background,bd=0).pack(pady=(12,0))
        tray=Tray(queue.Queue())
        try:
            for percent in (76,9,None,100):
                states={key:BatteryState(key,title,connected=percent is not None,
                                        battery_percent=percent,connection_status='online' if percent is not None else 'offline')
                        for _,key,title in DEVICES}
                tray.update(states,rebuild=True)
                assert set(tray.icons)=={3}
            bind(user,'GetGuiResources',W.DWORD,W.HANDLE,W.DWORD)
            bind(kernel,'GetCurrentProcess',W.HANDLE)
            process=kernel.GetCurrentProcess()
            before=user.GetGuiResources(process,0)
            for _ in range(30):
                icon=make_icon(75,True)
                user.DestroyIcon(icon)
            after=user.GetGuiResources(process,0)
            assert after==before,'icon rendering must release GDI objects'
            pump(root,0.2)
            screenshot(root,ROOT/'diagnostics'/'tray-icon-preview.png')
        finally:tray.close()
    finally:root.destroy()
    result=dict(style='ios_white_shell_green_fill',synthetic_preview=True,hardware_queries=0,light_dark_and_16_32_px=True,
                rounded_alpha=True,combined_icon_count=1,rebuild_ok=True,gdi_objects_before=before,
                gdi_objects_after=after,cleanup_ok=True)
    (ROOT/'diagnostics'/'tray_style_validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':run()
