"""Independent Alice 75 configuration transport, statically recovered from ONE.

Hardware configuration transfers remain unverified on this receiver. The native
CDev3632 frame builders and CDevG5KB serializers are recorded in diagnostics.
Writes require the previously user-identified receiver and complete fresh reads.
No firmware, reset, pairing, or arbitrary command interface is exposed.
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import time

from config.settings import ROOT
from providers.device_lock import alice75_lock
from providers.leobog_protocol import matches_interface

MODEL=json.loads((ROOT/'config/alice75_model.json').read_text(encoding='utf-8'))
MODES={m['code']:m for m in MODEL['modes']}
KEY_SLOTS={k['slot'] for k in MODEL['keys']}
CONFIG_SIZE=128
MATRIX_SIZE=512
PALETTE_SIZE=512
CUSTOM_SIZE=378
MACRO_PAGE=512
SLEEP_SECONDS=(0,30,60,90,120,180,240,300,600,900,1200)
READS={0x41,0x42,0x43,0x44,0x49}
WRITES={1,2,3,4,9}

class AliceSettingsError(ValueError):pass

def frame(command,total=1,index=0,layer=0,data=b''):
    if command not in READS|WRITES:raise AliceSettingsError('不支持的 Alice 75 指令')
    if any(type(v) is not int for v in (total,index,layer)) or not 1<=total<=127 or not 0<=index<total or not 0<=layer<=15:
        raise AliceSettingsError('无效分包或配置层')
    if len(data)>14 or command in READS and data:raise AliceSettingsError('无效数据长度')
    raw=bytes((0x13,command,total,index,(layer<<4)|len(data)))+bytes(data).ljust(14,b'\0')
    return raw+bytes((sum(raw)&255,))

def parse_frame(raw,command,layer,ack=False):
    if len(raw)!=20 or raw[0]!=0x13 or raw[1]!=command:
        raise AliceSettingsError('不是当前命令的回复')
    if sum(raw[:-1])&255!=raw[-1]:raise AliceSettingsError('Alice 75 校验不符')
    if raw[2]&0x80:raise AliceSettingsError('设备拒绝了此数据包')
    # AccessData correlates ACKs by command and sequence, not by the read-stream
    # total/layer fields. ACKs can acknowledge a sequence greater than their total.
    if ack:return raw[2]&127,raw[3],b''
    total=raw[2]&127;index=raw[3];size=raw[4]&15
    if not total or index>=total or size>14 or raw[4]>>4!=layer:
        raise AliceSettingsError('Alice 75 回复分包或配置层不符')
    return total,index,raw[5:5+size]

class Session:
    def __init__(self,channel):self.channel=channel

    def drain(self):
        until=time.monotonic()+.02
        while time.monotonic()<until:
            try:self.channel.read(max(.001,until-time.monotonic()))
            except TimeoutError:break

    def read(self,command,length,layer=0):
        if command not in READS or not 1<=length<=1778:raise AliceSettingsError('读取长度或命令无效')
        self.drain()
        self.channel.write(frame(command,layer=layer),timeout=.25)
        deadline=time.monotonic()+3
        chunks={};expected=None
        while time.monotonic()<deadline:
            try:raw=self.channel.read(max(.001,deadline-time.monotonic()))
            except TimeoutError as exc:raise TimeoutError('Alice 75 未响应；请连接原接收器、按键唤醒并退出 LEOBOG ONE 后重试') from exc
            if len(raw)!=20 or raw[:2]!=bytes((0x13,command)):continue
            count,index,data=parse_frame(raw,command,layer)
            if expected is None:
                expected=count
                if count!=(length+13)//14:raise AliceSettingsError('设备返回的分包数量与 A75 配置格式不符')
            if count!=expected:raise AliceSettingsError('回复分包总数发生变化')
            wanted=min(14,length-14*index)
            if len(data)!=wanted:raise AliceSettingsError('回复分包长度不完整')
            if index in chunks and chunks[index]!=data:raise AliceSettingsError('重复数据包内容不一致')
            chunks[index]=data
            if len(chunks)==expected:return b''.join(chunks[i] for i in range(expected))
        raise TimeoutError('Alice 75 配置回复不完整')

    def write(self,command,data,layer=0):
        if command not in WRITES or not 1<=len(data)<=1778:raise AliceSettingsError('写入长度或命令无效')
        self.drain()
        total=(len(data)+13)//14
        for index,offset in enumerate(range(0,len(data),14)):
            request=frame(command,total,index,layer,data[offset:offset+14])
            self.channel.write(request,timeout=.25)
            deadline=time.monotonic()+1
            while time.monotonic()<deadline:
                try:raw=self.channel.read(max(.001,deadline-time.monotonic()))
                except TimeoutError as exc:raise TimeoutError('Alice 75 写入应答超时；设备可能已部分修改') from exc
                if len(raw)!=20 or raw[:2]!=request[:2]:continue
                _,reply_index,_=parse_frame(raw,command,layer,ack=True)
                if reply_index!=index:continue
                break
            else:raise TimeoutError('Alice 75 缺少写入应答')
            time.sleep(.015)

@contextmanager
def session():
    from hid.windows_hid import enumerate_interfaces,HidChannel
    with alice75_lock():
        candidates=[x for x in enumerate_interfaces() if matches_interface(x)]
        if len(candidates)!=1:raise AliceSettingsError('需要连接唯一的 Alice 75 2.4G 接收器')
        info=candidates[0]
        if info.token!=MODEL['receiver_token']:
            raise AliceSettingsError('此接收器尚未确认为本机 Alice 75；请连接此前确认的接收器与 USB 端口')
        with HidChannel(info) as channel:yield Session(channel),info.token

def validate_block(raw,length,marker=True):
    if not isinstance(raw,(bytes,bytearray)) or len(raw)!=length:raise AliceSettingsError('Alice 75 配置长度不符')
    if marker and raw[-2:]!=b'\x5a\xa5':raise AliceSettingsError('设备配置缺少官方 A75 校验标记，暂不写入')

def decode_settings(raw,palette):
    validate_block(raw,CONFIG_SIZE);validate_block(palette,PALETTE_SIZE)
    code=raw[10];mode=MODES.get(code)
    result={'mode':code,'sleep':raw[24]*30,'brightness':None,'speed':None,'rainbow':None,'red':None,'green':None,'blue':None}
    if mode and mode['index']<19:
        i=mode['index'];result.update(brightness=raw[58+2*i],speed=raw[59+2*i]>>4,rainbow=raw[59+2*i]&15)
        if mode['color']:
            result.update(zip(('red','green','blue'),palette[21*code:21*code+3]))
    return result

def patch_settings(raw,palette,changes):
    validate_block(raw,CONFIG_SIZE);validate_block(palette,PALETTE_SIZE)
    if not isinstance(changes,dict) or set(changes)-{'mode','sleep','brightness','speed','rainbow','red','green','blue'}:
        raise AliceSettingsError('不支持的配置字段')
    if any(type(v) is not int for v in changes.values()):raise AliceSettingsError('配置值必须是整数')
    config=bytearray(raw);colors=bytearray(palette)
    code=changes.get('mode',raw[10]);mode=MODES.get(code)
    if not mode:raise AliceSettingsError('设备使用未知灯效，请选择支持的灯效再修改')
    if 'mode' in changes:
        config[9]=int(code==21);config[10]=code
    if 'sleep' in changes:
        if changes['sleep'] not in SLEEP_SECONDS:raise AliceSettingsError('无效休眠时间')
        config[24]=changes['sleep']//30
    for key,value in changes.items():
        if key in ('mode','sleep'):continue
        i=mode['index'];param=59+2*i
        if i>=19:raise AliceSettingsError('关闭灯光时不能调整灯效参数')
        if key=='brightness':
            if not mode['brightness'] or value not in range(5):raise AliceSettingsError('此灯效不支持该亮度')
            config[58+2*i]=value
        elif key=='speed':
            if not mode['speed'] or value not in range(5):raise AliceSettingsError('此灯效不支持该速度')
            config[param]=(config[param]&15)|(value<<4)
        elif key=='rainbow':
            if not mode['rainbow'] or value not in (0,7):raise AliceSettingsError('此灯效不支持该色彩模式')
            config[param]=(config[param]&240)|value
        elif key in ('red','green','blue'):
            if not mode['color'] or value not in range(256):raise AliceSettingsError('此灯效不支持 RGB 颜色')
            colors[code*21+('red','green','blue').index(key)]=value
    return bytes(config),bytes(colors)

def keyboard_value(hid,modifiers=0,second=0):
    if any(type(v) is not int for v in (hid,modifiers,second)) or not 0<=hid<=0xe7 or not 0<=modifiers<=255 or not 0<=second<=0xe7:
        raise AliceSettingsError('无效键码或修饰键')
    return bytes((0,modifiers,second,hid))

def macro_value(index):
    if type(index) is not int or not 0<=index<16:raise AliceSettingsError('无效宏索引')
    # Native serializer assigns zero-based macro index to byte 3, flags=1, loops=1.
    return bytes((3,1,1,index))

def encode_macros(macros):
    """4-byte address/size directory; UTF-16LE name then 4-byte native actions."""
    if not isinstance(macros,list) or not 1<=len(macros)<=16:raise AliceSettingsError('宏库需要 1–16 个宏')
    records=[];names=set()
    for macro in macros:
        if not isinstance(macro,dict) or set(macro)!={'name','events'}:raise AliceSettingsError('宏格式不正确')
        name=macro['name']
        if not isinstance(name,str) or not 1<=len(name)<=30 or name in names:raise AliceSettingsError('宏名称需要 1–30 个字符且不能重复')
        names.add(name);encoded=name.encode('utf-16-le')
        if len(encoded)>60:raise AliceSettingsError('宏名称超出设备限制')
        events=macro['events'];body=bytearray((len(encoded),))+encoded;held=set()
        if not isinstance(events,list) or not 1<=len(events)<=128:raise AliceSettingsError('每个宏需要 1–128 个动作')
        for event in events:
            if not isinstance(event,dict) or set(event)!={'type','key','action','delay'}:raise AliceSettingsError('宏动作字段不正确')
            kind,key,action,delay=(event[k] for k in ('type','key','action','delay'))
            if type(kind) is not int or kind not in (1,9,10) or type(key) is not int or not 1<=key<=255:raise AliceSettingsError('无效宏按键')
            if action not in ('down','up') or type(delay) is not int or not 0<=delay<=65535:raise AliceSettingsError('无效宏动作或延迟')
            token=(kind,key)
            if action=='down':
                if token in held:raise AliceSettingsError('宏按键重复按下')
                held.add(token)
            else:
                if token not in held:raise AliceSettingsError('宏按键未按下就释放')
                held.remove(token)
            native_type=0;native_key=key
            if kind==9:
                if key&(key-1):raise AliceSettingsError('修饰键需要单独的按下/释放动作')
                native_type=1;native_key=0xe0+(key.bit_length()-1)
            elif kind==1:
                if key not in (1,2,4,8,16):raise AliceSettingsError('无效鼠标键')
                native_type=2
            body+=bytes(((native_type<<4)|(128 if action=='up' else 0),delay>>8,delay&255,native_key))
        if held:raise AliceSettingsError('宏结束时必须释放全部按键')
        records.append(body)
    directory=bytearray();payload=bytearray()
    for record in records:
        directory+=struct.pack('<HH',4*len(records)+len(payload),len(record));payload+=record
    result=bytes(directory+payload)
    # Wireless writer selects 512-byte pages using a 4-bit page field.
    # A smaller local limit avoids extrapolating the unknown receiver capacity.
    if len(result)>2048:raise AliceSettingsError('本工具限制宏库总长不超过 2 KiB')
    return result

def save_backup(token,sections):
    folder=ROOT/'runtime/backups/alice75';folder.mkdir(parents=True,exist_ok=True)
    content={k:bytes(v).hex() for k,v in sections.items()}
    obj={'schema':1,'model':MODEL['model'],'receiver':token,'created':datetime.now().astimezone().isoformat(),'sections':content,
         'sha256':hashlib.sha256(json.dumps(content,sort_keys=True).encode()).hexdigest()}
    path=folder/(datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json')
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    return path

class Alice75Settings:
    def read_settings(self):
        with session() as (dev,token):
            raw=dev.read(0x44,CONFIG_SIZE);palette=dev.read(0x49,PALETTE_SIZE)
            return {'raw':raw,'palette':palette,'receiver':token,'values':decode_settings(raw,palette)}

    def apply_settings(self,changes,expected):
        patch_settings(expected['raw'],expected['palette'],changes)
        with session() as (dev,token):
            raw=dev.read(0x44,CONFIG_SIZE);palette=dev.read(0x49,PALETTE_SIZE)
            if token!=expected['receiver'] or raw!=expected['raw'] or palette!=expected['palette']:
                raise AliceSettingsError('设备配置已变化，请重新读取')
            config,colors=patch_settings(raw,palette,changes)
            if (config,colors)==(raw,palette):return expected|{'backup':None}
            backup=save_backup(token,{'settings':raw,'palette':palette})
            try:
                if colors!=palette:
                    dev.write(9,colors)
                    if dev.read(0x49,PALETTE_SIZE)!=colors:raise AliceSettingsError('颜色回读不一致')
                if config!=raw:dev.write(4,config)
                check=dev.read(0x44,CONFIG_SIZE)
                if check!=config:raise AliceSettingsError('配置回读不一致')
            except (OSError,ValueError) as exc:raise AliceSettingsError(f'应用未完成，设备可能已部分修改；原配置备份：{backup}') from exc
            return {'raw':check,'palette':colors,'values':decode_settings(check,colors),'receiver':token,'backup':str(backup)}

    def read_keys(self,layer=0):
        if type(layer) is not int or layer not in range(4):raise AliceSettingsError('不支持的按键层')
        with session() as (dev,token):
            raw=dev.read(0x41,MATRIX_SIZE,layer);validate_block(raw,MATRIX_SIZE)
            return {'raw':raw,'receiver':token,'layer':layer}

    def apply_key(self,index,value,expected):
        if type(index) is not int or index not in KEY_SLOTS or index>=126:raise AliceSettingsError('不是已知的 A75 按键')
        if not isinstance(value,bytes) or len(value)!=4 or value[0] not in (0,1,2,3,0xd):raise AliceSettingsError('不支持的按键功能')
        layer=expected['layer']
        if type(layer) is not int or layer not in range(4):raise AliceSettingsError('不支持的按键层')
        with session() as (dev,token):
            raw=dev.read(0x41,MATRIX_SIZE,layer);validate_block(raw,MATRIX_SIZE)
            if token!=expected['receiver'] or raw!=expected['raw']:raise AliceSettingsError('按键映射已变化，请重新读取')
            target=bytearray(raw);target[index*4:index*4+4]=value
            if bytes(target)==raw:return expected|{'backup':None}
            backup=save_backup(token,{f'layer{layer}':raw})
            try:
                dev.write(1,target,layer)
                if dev.read(0x41,MATRIX_SIZE,layer)!=bytes(target):raise AliceSettingsError('改键回读不一致')
            except (OSError,ValueError) as exc:raise AliceSettingsError(f'改键未完成，可能已部分修改；备份：{backup}') from exc
            return expected|{'raw':bytes(target),'backup':str(backup)}

    def apply_custom_color(self,index,rgb,expected_receiver):
        if index not in KEY_SLOTS or not 0<=index<126 or len(rgb)!=3 or any(type(x) is not int or not 0<=x<=255 for x in rgb):
            raise AliceSettingsError('无效键位或灯色')
        with session() as (dev,token):
            if token!=expected_receiver:raise AliceSettingsError('接收器发生变化')
            raw=dev.read(0x42,CUSTOM_SIZE,0);target=bytearray(raw)
            for channel,value in enumerate(rgb):target[channel*126+index]=value
            backup=save_backup(token,{'custom0':raw})
            try:
                dev.write(2,target,0)
                if dev.read(0x42,CUSTOM_SIZE,0)!=bytes(target):raise AliceSettingsError('单键灯色回读不一致')
            except (OSError,ValueError) as exc:raise AliceSettingsError(f'灯色应用未完成，可能已部分修改；备份：{backup}') from exc
            return {'backup':str(backup)}

    def install_macros(self,macros,*,replace_confirmed=False):
        data=encode_macros(macros)
        if replace_confirmed is not True:raise AliceSettingsError('请先保存旧宏，并确认覆盖设备宏库')
        with session() as (dev,token):
            assignments={f'layer{i}':dev.read(0x41,MATRIX_SIZE,i) for i in range(4)}
            for raw in assignments.values():validate_block(raw,MATRIX_SIZE)
            backup=save_backup(token,assignments|{'new_macro_library':data})
            try:
                for page,offset in enumerate(range(0,len(data),MACRO_PAGE)):
                    dev.write(3,data[offset:offset+MACRO_PAGE],page);time.sleep(.1)
            except (OSError,ValueError) as exc:raise AliceSettingsError(f'宏写入中断，可能已部分修改；记录：{backup}') from exc
            return {'count':len(macros),'backup':str(backup),'status':'设备已应答宏数据；实际触发尚未验证'}

    def restore_backup(self,path):
        path=Path(path)
        if path.stat().st_size>100_000:raise AliceSettingsError('备份文件过大')
        obj=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(obj,dict) or obj.get('schema')!=1 or obj.get('model')!=MODEL['model']:raise AliceSettingsError('不是本工具的 Alice 75 备份')
        sections=obj.get('sections',{})
        commands={'settings':(0x44,4,CONFIG_SIZE,0),'palette':(0x49,9,PALETTE_SIZE,0),'custom0':(0x42,2,CUSTOM_SIZE,0)}
        commands.update({f'layer{i}':(0x41,1,MATRIX_SIZE,i) for i in range(4)})
        if not sections or set(sections)-commands.keys():raise AliceSettingsError('备份包含不能完整恢复的宏记录或未知内容')
        if hashlib.sha256(json.dumps(sections,sort_keys=True).encode()).hexdigest()!=obj.get('sha256'):raise AliceSettingsError('备份校验失败')
        decoded={k:bytes.fromhex(v) for k,v in sections.items()}
        for k,raw in decoded.items():validate_block(raw,commands[k][2],k!='custom0')
        with session() as (dev,token):
            if token!=obj.get('receiver'):raise AliceSettingsError('备份与当前接收器不一致')
            before={k:dev.read(commands[k][0],len(raw),commands[k][3]) for k,raw in decoded.items()}
            for k,raw in before.items():validate_block(raw,commands[k][2],k!='custom0')
            backup=save_backup(token,before)
            try:
                for k,raw in decoded.items():
                    read,write,_,layer=commands[k];dev.write(write,raw,layer)
                    if dev.read(read,len(raw),layer)!=raw:raise AliceSettingsError('恢复回读不一致')
            except (OSError,ValueError) as exc:raise AliceSettingsError(f'恢复未完成，可能已部分修改；恢复前备份：{backup}') from exc
            return {'backup':str(backup)}
