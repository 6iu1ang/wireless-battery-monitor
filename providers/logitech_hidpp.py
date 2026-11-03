"""Conservative HID++ 2.0 queries. No settings, pairing, notifications or firmware writes.

Wire reference: OpenLogi 0x1004 and logitray (MIT). Independently implemented.
Only C539 (receiver) and C088 (wired GPW) FF00:0002 / report 11 collections
are opened. Both transports must pass the same protocol/name checks.
"""
import secrets
import time
from providers.base import BatteryState, Provider, now
from hid.windows_hid import enumerate_interfaces, HidChannel
from providers.voltage import estimate_percent
from providers.gpw_lock import device_lock

class ProtocolError(Exception): pass

class HidppClient:
    def __init__(self, channel, slot=1, trace=None, budget=7):
        self.channel, self.slot, self.trace = channel, slot, trace
        self.swid = secrets.randbelow(15)+1
        self.features = {}
        self.deadline = time.monotonic()+budget

    def request(self, index, function, params=b''):
        # Explicit getter allowlist, based on feature IDs resolved through IRoot.
        allowed = {0:{0,1}}
        for feature, idx in self.features.items():
            allowed[idx] = {0,1} if feature in (0x0001,0x0005,0x1000,0x1004) else {0}
        if function not in allowed.get(index,set()): raise ValueError('Non-getter HID++ function blocked')
        if len(params)>3: raise ValueError('Only three-byte getter parameters permitted')
        remaining = self.deadline-time.monotonic()
        if remaining <= 0: raise TimeoutError('Provider query budget exhausted')
        command = (function<<4)|self.swid
        frame = bytes([0x11,self.slot,index,command])+params.ljust(3,b'\0')+bytes(13)
        until = min(self.deadline,time.monotonic()+0.65)
        self.channel.write(frame,timeout=min(0.4,remaining))
        while time.monotonic()<until:
            report = self.channel.read(timeout=max(0.001,until-time.monotonic()))
            if len(report)!=20 or report[0]!=0x11 or report[1]!=self.slot: continue
            if report[2] in (0xFF,0x8F) and report[3:5] == bytes([index,command]):
                raise ProtocolError(f'HID++ error 0x{report[5]:02X}')
            if report[2:4]!=bytes([index,command]): continue
            if self.trace is not None:
                self.trace.append({'slot':self.slot,'feature_index':index,'function':function,'params':params.hex(),'response_payload':report[4:].hex()})
            return report[4:]
        raise TimeoutError('No matching HID++ reply')

    def ping(self):
        echo = secrets.randbelow(255)+1
        reply = self.request(0,1,bytes([0,0,echo]))
        if reply[2]!=echo or reply[0]<2: raise ProtocolError('HID++ 2.0 ping identity mismatch')
        return f'{reply[0]}.{reply[1]}'

    def feature(self, feature):
        if feature not in (0x0001,0x0003,0x0005,0x1000,0x1001,0x1004):
            raise ValueError('Feature is not on the read-only allowlist')
        if feature in self.features: return self.features[feature]
        reply = self.request(0,0,feature.to_bytes(2,'big'))
        if reply[0] not in (0,0xFF): self.features[feature] = reply[0]
        return reply[0] if reply[0]!=0xFF else 0

    def name(self):
        idx = self.feature(0x0005)
        if not idx: return None
        length = self.request(idx,0)[0]
        if not 1<=length<=96: raise ProtocolError('Invalid device name length')
        text = bytearray()
        while len(text)<length:
            text.extend(self.request(idx,1,bytes([len(text)]))[:min(16,length-len(text))])
        return text.decode('utf-8',errors='replace').strip('\0')

def decode_battery(feature, payload, capabilities=None):
    if len(payload)<3: raise ProtocolError('Truncated battery data')
    if feature in (0x1000,0x1004):
        percent, level, status = payload[:3]
        if percent>100: raise ProtocolError('Out of range battery reading')
        if feature==0x1004:
            if status>4 or level not in (0,1,2,4,8): raise ProtocolError('Invalid unified battery enum')
            if capabilities is None or len(capabilities)<2: raise ProtocolError('Missing battery capabilities')
            has_percent = bool(capabilities[1]&2)
            percent = percent if has_percent else None
            level = {0:None,1:'极低',2:'低',4:'高',8:'满电'}[level]
            charge = {0:('discharging',False),1:('charging',True),2:('charging_slow',True),3:('full',False),4:('error',None)}[status]
        else:
            # 0 is the legacy feature's unknown sentinel, not an empty battery.
            percent = percent or None
            level = None
            if status>7: raise ProtocolError('Invalid legacy battery status')
            charge = {0:('discharging',False),1:('charging',True),2:('charging_final',True),3:('full',False),4:('charging_slow',True),5:('error',None),6:('error',None),7:('unknown',None)}[status]
        return dict(battery_percent=percent,battery_level=level,charging=charge[1],charge_status=charge[0])
    if feature==0x1001:
        voltage = int.from_bytes(payload[:2],'big')
        if not 2500<=voltage<=5000: raise ProtocolError('Implausible battery voltage')
        # Generic documented one-cell Logitech Li-ion table; always label as estimated.
        flags = payload[2]
        external = bool(flags&0x80)
        full = external and (flags&3)==2
        return dict(voltage_mv=voltage,battery_percent=estimate_percent(voltage),estimated=True,charging=external and not full,charge_status='full' if full else ('charging' if external else 'discharging'))
    raise ProtocolError('Unsupported battery feature')

class LogitechProvider(Provider):
    def __init__(self, trace=None):
        self.trace = trace
        self.cache = None
        self.last_state = self._state()

    def _state(self, **kwargs):
        return BatteryState('logitech_gpw','Logitech GPW 一代',observed_at=now(),**kwargs)

    def detect(self, *, include_wired=True):
        pids = (0xC539,0xC088) if include_wired else (0xC539,)
        return [x for x in enumerate_interfaces() if x.pid in pids
                and (x.vid,x.page,x.usage,x.input_length,x.output_length)==(0x046D,0xFF00,2,20,20)
                and 0x11 in x.reports.get('input',[]) and 0x11 in x.reports.get('output',[])]

    def read_battery(self):
        try:
            with device_lock():
                return self._read_battery()
        except (OSError, TimeoutError) as exc:
            self.cache = None
            self.last_state = self._state(connection_status='unresponsive', error_message=str(exc))
            return self.last_state

    def _read_battery(self):
        try:
            interfaces = self.detect()
            if not interfaces:
                self.cache = None
                self.last_state = self._state(connected=False,connection_status='offline',error_message='未检测到 GPW 有线设备或 LIGHTSPEED 接收器')
                return self.last_state
            receiver_connected = any(x.pid==0xC539 for x in interfaces)
            # Plugging in the cable takes the mouse off its wireless slot even
            # when the receiver stays present. Always select the direct device.
            wired = [x for x in interfaces if x.pid==0xC088]
            candidates = wired or interfaces
            if len(candidates)>1: raise ProtocolError('检测到多个匹配 GPW 接口，无法安全确定目标')
            info = candidates[0]
            with HidChannel(info) as channel:
                client = HidppClient(channel,slot=0xFF if info.pid==0xC088 else 1,trace=self.trace)
                if self.cache and self.cache[0]==info.token:
                    _,slot,features,capabilities = self.cache
                    client.slot = slot
                    client.features = features.copy()
                    client.ping()
                else:
                    # C539 addresses slot 1; direct USB C088 addresses the device
                    # itself at 0xFF. Never reuse feature indices across paths.
                    client.ping()
                    name = client.name()
                    if name != 'G Pro Wireless Gaming Mouse':
                        raise ProtocolError('HID++ 设备名称与 GPW 不符：'+str(name))
                    capabilities = {}
                    # Resolve documented battery features through IRoot, never assume indices.
                    for fid in (0x1004,0x1000,0x1001):
                        idx = client.feature(fid)
                        if idx and fid==0x1004: capabilities[fid] = client.request(idx,0)
                    self.cache = (info.token,client.slot,client.features.copy(),capabilities)
                errors = []
                for fid in (0x1004,0x1000,0x1001):
                    idx = client.features.get(fid)
                    if not idx: continue
                    try:
                        payload = client.request(idx,1 if fid==0x1004 else 0)
                        decoded = decode_battery(fid,payload,capabilities.get(fid))
                        transport = 'USB 有线' if info.pid==0xC088 else 'LIGHTSPEED 无线'
                        self.last_state = self._state(connected=True,receiver_connected=receiver_connected,connection_status='online',data_source=f'Logitech HID++ 2.0 / 0x{fid:04X} / {transport}',last_updated=now(),**decoded)
                        return self.last_state
                    except (TimeoutError,ProtocolError) as exc: errors.append(str(exc))
                self.last_state = self._state(connected=True,receiver_connected=receiver_connected,connection_status='unsupported',error_message='; '.join(errors) or '设备未提供支持的电量特征')
        except (OSError,ProtocolError,TimeoutError) as exc:
            self.cache = None
            self.last_state = self._state(connected=None,receiver_connected=locals().get('receiver_connected',False),connection_status='unresponsive',error_message=str(exc))
        return self.last_state
