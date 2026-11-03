"""SetupAPI/HID wrappers. Enumeration never sends output or feature reports."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass, field
import hashlib
import re

k32 = C.WinDLL('kernel32', use_last_error=True)
hid = C.WinDLL('hid', use_last_error=True)
setup = C.WinDLL('setupapi', use_last_error=True)
SUCCESS = 0x110000
INVALID = C.c_void_p(-1).value

class GUID(C.Structure):
    _fields_ = [('a', W.DWORD), ('b', W.WORD), ('c', W.WORD), ('d', C.c_ubyte*8)]

class InterfaceData(C.Structure):
    _fields_ = [('size', W.DWORD), ('guid', GUID), ('flags', W.DWORD), ('reserved', C.c_void_p)]

class Attributes(C.Structure):
    _fields_ = [('size', W.ULONG), ('vid', W.USHORT), ('pid', W.USHORT), ('version', W.USHORT)]

class Caps(C.Structure):
    _fields_ = [(n, W.USHORT) for n in ('usage','page','input_length','output_length','feature_length')] + [('reserved', W.USHORT*17)] + [(n, W.USHORT) for n in ('links','input_buttons','input_values','input_indices','output_buttons','output_values','output_indices','feature_buttons','feature_values','feature_indices')]

class ValueCaps(C.Structure):
    _fields_ = [('page', W.USHORT), ('report_id', C.c_ubyte), ('alias', C.c_ubyte), ('bits', W.USHORT), ('link', W.USHORT), ('link_usage', W.USHORT), ('link_page', W.USHORT), ('is_range', C.c_ubyte), ('string_range', C.c_ubyte), ('designator_range', C.c_ubyte), ('absolute', C.c_ubyte), ('has_null', C.c_ubyte), ('reserved', C.c_ubyte), ('bit_size', W.USHORT), ('count', W.USHORT), ('reserved2', W.USHORT*5), ('units_exp', W.ULONG), ('units', W.ULONG), ('logical_min', W.LONG), ('logical_max', W.LONG), ('physical_min', W.LONG), ('physical_max', W.LONG), ('usages', W.USHORT*8)]

class Overlapped(C.Structure):
    _fields_ = [('internal', C.c_size_t), ('internal_high', C.c_size_t), ('offset', W.DWORD), ('offset_high', W.DWORD), ('event', W.HANDLE)]

def bind(lib, name, result, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = result, list(args)
    return fn

bind(k32, 'CreateFileW', W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE)
bind(k32, 'CloseHandle', W.BOOL, W.HANDLE)
bind(k32, 'CreateEventW', W.HANDLE, C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR)
bind(k32, 'WaitForSingleObject', W.DWORD, W.HANDLE, W.DWORD)
bind(k32, 'CancelIoEx', W.BOOL, W.HANDLE, C.POINTER(Overlapped))
bind(k32, 'GetOverlappedResult', W.BOOL, W.HANDLE, C.POINTER(Overlapped), C.POINTER(W.DWORD), W.BOOL)
for name in ('ReadFile','WriteFile'):
    bind(k32, name, W.BOOL, W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.POINTER(Overlapped))
bind(hid, 'HidD_GetHidGuid', None, C.POINTER(GUID))
bind(hid, 'HidD_GetAttributes', C.c_ubyte, W.HANDLE, C.POINTER(Attributes))
bind(hid, 'HidD_GetPreparsedData', C.c_ubyte, W.HANDLE, C.POINTER(C.c_void_p))
bind(hid, 'HidD_FreePreparsedData', C.c_ubyte, C.c_void_p)
bind(hid, 'HidP_GetCaps', W.LONG, C.c_void_p, C.POINTER(Caps))
for name in ('HidP_GetValueCaps','HidP_GetButtonCaps'):
    bind(hid, name, W.LONG, C.c_int, C.c_void_p, C.POINTER(W.USHORT), C.c_void_p)
for name in ('HidD_GetManufacturerString','HidD_GetProductString'):
    bind(hid, name, C.c_ubyte, W.HANDLE, C.c_void_p, W.ULONG)
bind(setup, 'SetupDiGetClassDevsW', W.HANDLE, C.POINTER(GUID), W.LPCWSTR, W.HWND, W.DWORD)
bind(setup, 'SetupDiEnumDeviceInterfaces', W.BOOL, W.HANDLE, C.c_void_p, C.POINTER(GUID), W.DWORD, C.POINTER(InterfaceData))
bind(setup, 'SetupDiGetDeviceInterfaceDetailW', W.BOOL, W.HANDLE, C.POINTER(InterfaceData), C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p)
bind(setup, 'SetupDiDestroyDeviceInfoList', W.BOOL, W.HANDLE)

@dataclass
class HidInterface:
    path: str = field(repr=False)
    vid: int = 0
    pid: int = 0
    manufacturer: str = ''
    product: str = ''
    page: int = 0
    usage: int = 0
    input_length: int = 0
    output_length: int = 0
    feature_length: int = 0
    reports: dict = field(default_factory=dict)
    values: list = field(default_factory=list)
    error: str = ''

    @property
    def token(self):
        return hashlib.sha256(self.path.encode()).hexdigest()[:10]

    @property
    def label(self):
        matches = re.findall(r'(?:mi_\w{2}|col\w{2})', self.path.lower())
        return ' '.join(matches) or 'collection'

    def public(self):
        return {k:v for k,v in self.__dict__.items() if k != 'path'} | {'interface':self.label, 'path_token':self.token, 'present':True}

def describe(path):
    info = HidInterface(path)
    # Access 0 permits metadata queries even for OS-owned mouse/keyboard collections.
    handle = k32.CreateFileW(path, 0, 3, None, 3, 0, None)
    if handle == INVALID:
        info.error = f'metadata open failed ({C.get_last_error()})'
        m = re.search(r'vid_([0-9a-f]{4})&pid_([0-9a-f]{4})', path.lower())
        if m: info.vid, info.pid = (int(x,16) for x in m.groups())
        return info
    pp = C.c_void_p()
    try:
        attrs = Attributes(C.sizeof(Attributes),0,0,0)
        if hid.HidD_GetAttributes(handle, C.byref(attrs)):
            info.vid, info.pid = attrs.vid, attrs.pid
        for attr, fn in [('manufacturer',hid.HidD_GetManufacturerString),('product',hid.HidD_GetProductString)]:
            buf = C.create_unicode_buffer(256)
            if fn(handle,buf,C.sizeof(buf)): setattr(info,attr,buf.value)
        if not hid.HidD_GetPreparsedData(handle,C.byref(pp)):
            info.error = 'preparsed data unavailable'
            return info
        caps = Caps()
        if hid.HidP_GetCaps(pp,C.byref(caps)) != SUCCESS:
            info.error = 'HID caps unavailable'
            return info
        for name in ('page','usage','input_length','output_length','feature_length'):
            setattr(info,name,getattr(caps,name))
        for kind, prefix in enumerate(('input','output','feature')):
            ids = set()
            for suffix, getter in [('values',hid.HidP_GetValueCaps),('buttons',hid.HidP_GetButtonCaps)]:
                count = W.USHORT(getattr(caps,prefix+'_'+suffix))
                if not count.value: continue
                # Both SDK capability records occupy 72 bytes; the shared prefix stores ReportID.
                buf = C.create_string_buffer(count.value * 72)
                if getter(kind,buf,C.byref(count),pp) != SUCCESS: continue
                for i in range(count.value):
                    raw = buf.raw[i*72:(i+1)*72]
                    ids.add(raw[2])
                    if suffix == 'values':
                        vc = ValueCaps.from_buffer_copy(raw)
                        info.values.append(dict(kind=prefix,page=vc.page,usage_min=vc.usages[0],usage_max=vc.usages[1] if vc.is_range else vc.usages[0],report_id=vc.report_id,logical_min=vc.logical_min,logical_max=vc.logical_max,bit_size=vc.bit_size,count=vc.count,link=vc.link))
            info.reports[prefix] = sorted(ids)
    finally:
        if pp: hid.HidD_FreePreparsedData(pp)
        k32.CloseHandle(handle)
    return info

def enumerate_interfaces():
    guid = GUID()
    hid.HidD_GetHidGuid(C.byref(guid))
    devs = setup.SetupDiGetClassDevsW(C.byref(guid),None,None,0x12)
    if devs == INVALID: raise OSError('SetupAPI enumeration failed')
    result = []
    try:
        index = 0
        while True:
            entry = InterfaceData()
            entry.size = C.sizeof(entry)
            if not setup.SetupDiEnumDeviceInterfaces(devs,None,C.byref(guid),index,C.byref(entry)):
                if C.get_last_error() == 259: break
                raise OSError('SetupAPI interface enumeration failed')
            index += 1
            size = W.DWORD()
            setup.SetupDiGetDeviceInterfaceDetailW(devs,C.byref(entry),None,0,C.byref(size),None)
            buf = C.create_string_buffer(size.value)
            C.cast(buf,C.POINTER(W.DWORD))[0] = 8 if C.sizeof(C.c_void_p)==8 else 6
            if setup.SetupDiGetDeviceInterfaceDetailW(devs,C.byref(entry),buf,size,None,None):
                result.append(describe(C.wstring_at(C.addressof(buf)+4)))
    finally:
        setup.SetupDiDestroyDeviceInfoList(devs)
    return result

class HidChannel:
    """Shared, overlapped I/O, only on a validated vendor collection."""
    def __init__(self, interface):
        if interface.page < 0xFF00:
            raise ValueError('Refusing input/system collection')
        self.info = interface
        self.handle = k32.CreateFileW(interface.path,0xC0000000,3,None,3,0x40000000,None)
        if self.handle == INVALID: raise OSError(f'HID open failed ({C.get_last_error()})')

    def _io(self, write, payload, timeout):
        buf = C.create_string_buffer(payload, len(payload)) if write else C.create_string_buffer(self.info.input_length)
        ov = Overlapped()
        ov.event = k32.CreateEventW(None,True,False,None)
        if not ov.event: raise OSError('Cannot create I/O event')
        count = W.DWORD()
        try:
            fn = k32.WriteFile if write else k32.ReadFile
            if not fn(self.handle,buf,len(buf),C.byref(count),C.byref(ov)):
                if C.get_last_error() != 997: raise OSError(f'HID I/O failed ({C.get_last_error()})')
                wait = k32.WaitForSingleObject(ov.event,max(1,int(timeout*1000)))
                if wait != 0:
                    k32.CancelIoEx(self.handle,C.byref(ov))
                    # Complete cancellation before releasing OVERLAPPED/buffer memory.
                    k32.GetOverlappedResult(self.handle,C.byref(ov),C.byref(count),True)
                    if wait == 258: raise TimeoutError('HID response timeout')
                    raise OSError('HID wait failed')
                if not k32.GetOverlappedResult(self.handle,C.byref(ov),C.byref(count),False):
                    raise OSError(f'HID completion failed ({C.get_last_error()})')
            if write and count.value != len(payload): raise OSError('Short HID write')
            return buf.raw[:count.value]
        finally:
            k32.CloseHandle(ov.event)

    def write(self, payload, timeout=0.4):
        if len(payload) != self.info.output_length: raise ValueError('Wrong output report size')
        self._io(True,payload,timeout)

    def read(self, timeout=0.4):
        return self._io(False,b'',timeout)

    def close(self):
        if self.handle is not None:
            k32.CancelIoEx(self.handle,None)
            k32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
