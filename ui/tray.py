"""Native Windows notification area and balloons, no third-party dependencies."""
import ctypes as C
from ctypes import wintypes as W
import os
import queue
from config.autostart import OWNER

user = C.WinDLL('user32',use_last_error=True)
gdi = C.WinDLL('gdi32',use_last_error=True)
shell = C.WinDLL('shell32',use_last_error=True)
kernel = C.WinDLL('kernel32',use_last_error=True)
LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
CALLBACK = 0x8000+23
SHOW_PANEL = 0x8000+24
WINDOW_NAME = 'Wireless Battery Monitor Tray '+OWNER
DEVICES = ((1,'logitech_gpw','GPW'),(2,'vgn_s99','S99'),(4,'leobog_alice75','Alice75'))

def icon_ids(mode):
    return {3} if mode=='combined' else ({1,2,4} if mode=='both' else ({1} if mode=='mouse' else {2,4}))

def combined_details(states):
    devices = [states.get(key) for _,key,_ in DEVICES]
    complete = all(s is not None and (s.connected is True or s.uses_last_reading) and s.display_percent is not None for s in devices)
    lowest = min(devices,key=lambda s:s.display_percent) if complete else None
    tip = '\n'.join(tooltip_line(title,state) for (_,_,title),state in zip(DEVICES,devices))
    return (lowest.display_percent if lowest else None,'ALL~' if lowest and lowest.display_estimated else 'ALL',tip)

def fit_tooltip(text):
    # NOTIFYICONDATA holds 127 UTF-16 code units plus the terminating NUL.
    return text.encode('utf-16-le')[:254].decode('utf-16-le',errors='ignore')

def bind(lib,name,result,*args):
    fn = getattr(lib,name)
    fn.restype,fn.argtypes = result,list(args)
    return fn

class WndClass(C.Structure):
    _fields_ = [('style',W.UINT),('proc',WNDPROC),('cls_extra',C.c_int),('wnd_extra',C.c_int),('instance',W.HINSTANCE),('icon',W.HICON),('cursor',W.HANDLE),('background',W.HBRUSH),('menu',W.LPCWSTR),('name',W.LPCWSTR)]

class NotifyData(C.Structure):
    _fields_ = [('size',W.DWORD),('hwnd',W.HWND),('id',W.UINT),('flags',W.UINT),('callback',W.UINT),('icon',W.HICON),('tip',W.WCHAR*128),('state',W.DWORD),('state_mask',W.DWORD),('info',W.WCHAR*256),('version',W.UINT),('title',W.WCHAR*64),('info_flags',W.DWORD),('guid',C.c_ubyte*16),('balloon_icon',W.HICON)]

class BitmapHeader(C.Structure):
    _fields_ = [('size',W.DWORD),('width',W.LONG),('height',W.LONG),('planes',W.WORD),('bits',W.WORD),('compression',W.DWORD),('image_size',W.DWORD),('x',W.LONG),('y',W.LONG),('used',W.DWORD),('important',W.DWORD)]

class IconInfo(C.Structure):
    _fields_ = [('is_icon',W.BOOL),('x',W.DWORD),('y',W.DWORD),('mask',W.HBITMAP),('color',W.HBITMAP)]

bind(kernel,'GetModuleHandleW',W.HMODULE,W.LPCWSTR)
bind(user,'RegisterClassW',W.ATOM,C.POINTER(WndClass))
bind(user,'UnregisterClassW',W.BOOL,W.LPCWSTR,W.HINSTANCE)
bind(user,'CreateWindowExW',W.HWND,W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,C.c_void_p)
bind(user,'DefWindowProcW',LRESULT,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
bind(user,'DestroyWindow',W.BOOL,W.HWND)
bind(user,'RegisterWindowMessageW',W.UINT,W.LPCWSTR)
bind(user,'CreatePopupMenu',W.HMENU)
bind(user,'AppendMenuW',W.BOOL,W.HMENU,W.UINT,C.c_size_t,W.LPCWSTR)
bind(user,'TrackPopupMenu',W.UINT,W.HMENU,W.UINT,C.c_int,C.c_int,C.c_int,W.HWND,C.c_void_p)
bind(user,'DestroyMenu',W.BOOL,W.HMENU)
bind(user,'GetCursorPos',W.BOOL,C.POINTER(W.POINT))
bind(user,'SetForegroundWindow',W.BOOL,W.HWND)
bind(user,'PostMessageW',W.BOOL,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
bind(user,'CreateIconIndirect',W.HICON,C.POINTER(IconInfo))
bind(user,'DestroyIcon',W.BOOL,W.HICON)
bind(user,'DrawTextW',C.c_int,W.HDC,W.LPCWSTR,C.c_int,C.POINTER(W.RECT),W.UINT)
bind(user,'GetSystemMetrics',C.c_int,C.c_int)
bind(user,'PeekMessageW',W.BOOL,C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT)
bind(user,'TranslateMessage',W.BOOL,C.POINTER(W.MSG))
bind(user,'DispatchMessageW',LRESULT,C.POINTER(W.MSG))
bind(shell,'Shell_NotifyIconW',W.BOOL,W.DWORD,C.POINTER(NotifyData))
bind(gdi,'CreateCompatibleDC',W.HDC,W.HDC)
bind(gdi,'DeleteDC',W.BOOL,W.HDC)
bind(gdi,'CreateDIBSection',W.HBITMAP,W.HDC,C.POINTER(BitmapHeader),W.UINT,C.POINTER(C.c_void_p),W.HANDLE,W.DWORD)
bind(gdi,'CreateBitmap',W.HBITMAP,C.c_int,C.c_int,W.UINT,W.UINT,C.c_void_p)
bind(gdi,'SelectObject',W.HANDLE,W.HDC,W.HANDLE)
bind(gdi,'DeleteObject',W.BOOL,W.HANDLE)
bind(gdi,'CreateFontW',W.HFONT,C.c_int,C.c_int,C.c_int,C.c_int,C.c_int,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.LPCWSTR)
bind(gdi,'SetTextColor',W.DWORD,W.HDC,W.DWORD)
bind(gdi,'SetBkMode',C.c_int,W.HDC,C.c_int)
bind(gdi,'GdiFlush',W.BOOL)
bind(gdi,'GetTextExtentPoint32W',W.BOOL,W.HDC,W.LPCWSTR,C.c_int,C.POINTER(W.SIZE))

def tooltip_line(title,state):
    text=f'{title}：{state.display_battery if state else "未知"}'
    if state:
        if state.uses_last_reading:text+='（上次估算）' if state.display_estimated else '（上次）'
        elif state.display_estimated:text+='（估算）'
        text+=' · '+{'online':'在线','offline':'接收器断开','unresponsive':'无响应/休眠','unsupported':'暂不支持','unknown':'状态未知','reading':'读取中'}.get(state.connection_status,'状态未知')
        if state.display_updated:
            text+=' · '+(state.display_updated[5:16].replace('T',' ') if state.uses_last_reading else state.display_updated[11:19])
    return text

def render_icon_pixels(percent,estimated=False,*,light=None,size=32):
    """iOS-style white battery shell and green fill, with no text.

    The green fill follows the reading; an unknown value leaves the shell empty.
    Output is premultiplied BGRA, rendered directly at the notification icon size.
    """
    green=(48,209,88)
    scale=size/32

    def rounded(px,py,left,top,right,bottom,radius):
        if not (left<=px<=right and top<=py<=bottom):return False
        cx=min(max(px,left+radius),right-radius)
        cy=min(max(py,top+radius),bottom-radius)
        return (px-cx)**2+(py-cy)**2<=radius**2

    pixels=bytearray(size*size*4)
    end=3.5+22*(percent or 0)/100
    for y in range(size):
        for x in range(size):
            shell_coverage=fill_coverage=0
            for sy in range(4):
                for sx in range(4):
                    px,py=(x+(sx+.5)/4)/scale,(y+(sy+.5)/4)/scale
                    body=rounded(px,py,.5,5,28.5,27,4)
                    inner=rounded(px,py,2.5,7,26.5,25,2)
                    terminal=rounded(px,py,29.5,12,32,20,1)
                    fill=(percent is not None and px<end
                          and rounded(px,py,3.5,8,25.5,24,1.5))
                    if (body and not inner) or terminal:shell_coverage+=1
                    elif fill:fill_coverage+=1
            offset=4*(y*size+x)
            pixels[offset:offset+4]=(
                *[round((255*shell_coverage+c*fill_coverage)/16) for c in reversed(green)],
                round(255*(shell_coverage+fill_coverage)/16))
    return bytes(pixels)


def make_icon(percent,estimated=False):
    size=max(16,min(64,user.GetSystemMetrics(49) or 16))
    pixels=render_icon_pixels(percent,estimated,size=size)
    dc=gdi.CreateCompatibleDC(None)
    bits=C.c_void_p()
    header=BitmapHeader(C.sizeof(BitmapHeader),size,-size,1,32,0,size*size*4,0,0,0,0)
    bitmap=gdi.CreateDIBSection(dc,C.byref(header),0,C.byref(bits),None,0)
    mask=gdi.CreateBitmap(size,size,1,1,C.create_string_buffer(((size+15)//16)*2*size))
    try:
        if not dc or not bitmap or not mask:raise OSError('托盘位图创建失败')
        C.memmove(bits,pixels,len(pixels))
        result=user.CreateIconIndirect(C.byref(IconInfo(True,0,0,mask,bitmap)))
        if not result:raise OSError('CreateIconIndirect failed')
        return result
    finally:
        if bitmap:gdi.DeleteObject(bitmap)
        if mask:gdi.DeleteObject(mask)
        if dc:gdi.DeleteDC(dc)

class Tray:
    def __init__(self,events,mode='combined'):
        self.events,self.mode = events,mode
        self.states = {}
        self.icons = {}
        self.instance = kernel.GetModuleHandleW(None)
        self.class_name = f'WirelessBatteryMonitorTray-{os.getpid()}'
        self.callback = WNDPROC(self._wndproc)
        self.taskbar_created = user.RegisterWindowMessageW('TaskbarCreated')
        wc = WndClass(0,self.callback,0,0,self.instance,None,None,None,None,self.class_name)
        if not user.RegisterClassW(C.byref(wc)): raise OSError('托盘窗口类注册失败')
        self.hwnd = user.CreateWindowExW(0,self.class_name,WINDOW_NAME,0,0,0,0,0,None,None,self.instance,None)
        if not self.hwnd:
            user.UnregisterClassW(self.class_name,self.instance)
            raise OSError('托盘窗口创建失败')
        try: self.update({})
        except Exception:
            self.close()
            raise

    def _data(self,id,flags=0):
        return NotifyData(size=C.sizeof(NotifyData),hwnd=self.hwnd,id=id,flags=flags,callback=CALLBACK)

    def _wndproc(self,hwnd,msg,wparam,lparam):
        if msg==SHOW_PANEL:
            self.events.put(('open',None))
            return 0
        if msg==CALLBACK:
            if lparam in (0x202,0x203): self.events.put(('open',None))
            elif lparam in (0x205,0x7B): self._menu(hwnd)
            return 0
        if msg==self.taskbar_created:
            self.events.put(('tray_rebuild',None))
            return 0
        if msg in (0x1A,0x31A):self.events.put(('tray_rebuild',None))
        if msg==0x218 and wparam in (6,7,18): self.events.put(('resume',None))
        if msg==0x219: self.events.put(('devices',None))
        if msg==0x10:
            self.events.put(('quit',None))
            return 0
        return user.DefWindowProcW(hwnd,msg,wparam,lparam)

    def _menu(self,hwnd):
        menu = user.CreatePopupMenu()
        try:
            for id,key,title in DEVICES:
                state = self.states.get(key)
                text = f'{title}  {state.display_battery if state else "未知"}'+('（上次读数）' if state and state.uses_last_reading else '')
                user.AppendMenuW(menu,2,0,text)
            user.AppendMenuW(menu,0x800,0,None)
            for id,label in [(101,'打开面板'),(102,'立即刷新'),(103,'完全退出')]: user.AppendMenuW(menu,0,id,label)
            point = W.POINT()
            user.GetCursorPos(C.byref(point))
            user.SetForegroundWindow(hwnd)
            command = user.TrackPopupMenu(menu,0x102,point.x,point.y,0,hwnd,None)
            user.PostMessageW(hwnd,0,0,0)
            if command in (101,102,103): self.events.put(({101:'open',102:'refresh',103:'quit'}[command],None))
        finally: user.DestroyMenu(menu)

    def update(self,states,mode=None,rebuild=False):
        self.states = states.copy()
        if mode: self.mode=mode
        wanted = icon_ids(self.mode)
        for id in list(self.icons):
            if id not in wanted or rebuild:
                shell.Shell_NotifyIconW(2,C.byref(self._data(id)))
                user.DestroyIcon(self.icons.pop(id))
        for id in wanted:
            if id==3:
                percent,label,tip=combined_details(states)
            else:
                _,key,title = next(device for device in DEVICES if device[0]==id)
                state = states.get(key)
                percent = state.display_percent if state else None
                label = ('M~' if state and state.display_estimated else 'M') if id==1 else ('K1' if id==2 else 'K2')
                tip=tooltip_line(title,state)
            icon = make_icon(percent,estimated=label.endswith('~'))
            data = self._data(id,7)
            data.icon = icon
            data.tip = fit_tooltip(tip)
            exists = id in self.icons
            if not shell.Shell_NotifyIconW(1 if exists else 0,C.byref(data)):
                user.DestroyIcon(icon)
                raise OSError('Windows 托盘图标注册失败')
            if exists: user.DestroyIcon(self.icons[id])
            self.icons[id] = icon

    def notify(self,title,message):
        if not self.icons: return False
        data = self._data(next(iter(self.icons)),0x10)
        data.title,data.info,data.info_flags = title[:63],message[:255],2
        return bool(shell.Shell_NotifyIconW(1,C.byref(data)))

    def pump(self):
        msg = W.MSG()
        for _ in range(30):
            if not user.PeekMessageW(C.byref(msg),self.hwnd,0,0,1): break
            user.TranslateMessage(C.byref(msg))
            user.DispatchMessageW(C.byref(msg))

    def close(self):
        for id,icon in list(self.icons.items()):
            shell.Shell_NotifyIconW(2,C.byref(self._data(id)))
            user.DestroyIcon(icon)
        self.icons.clear()
        if self.hwnd:
            user.DestroyWindow(self.hwnd)
            self.hwnd = None
        user.UnregisterClassW(self.class_name,self.instance)
