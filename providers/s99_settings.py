"""Independent S99 2.4G configuration implementation.

Wire facts: local V HUB EvisionApi and official Web HUB Td; see
diagnostics/vhub_settings_evidence.json. No vendor code is imported at runtime.
Only the verified 320F:5088 vendor collection is accepted. All writes are explicit.
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time

from providers.s99_protocol import matches_interface
from providers.s99_lock import device_lock
from config.settings import ROOT

CHUNK = 24
MATRIX_SIZE = 336
FUNCTION_SIZE = 48
LIGHT_MODES = dict(enumerate([
    '随波逐流','彩云纷飞','峰回路转','光之审判','呼吸','常亮','踏雪无痕','泛起涟漪',
    '奔逸绝尘','暗流涌动','百花争艳','流星赶月','大鹏展翅','川流不息','落雨纷纷',
    '左右逢源','众志成城','速度激情','自定义','关闭'], 1))
# Offset, valid values. Unknown fields are always preserved from a fresh read.
FIELDS = {
    'mode': (1, range(1, 21)), 'brightness': (2, range(5)),
    'speed': (3, range(5)), 'direction': (4, range(2)), 'rainbow': (5, range(2)),
    'red': (6, range(256)), 'green': (7, range(256)), 'blue': (8, range(256)),
    'wasd_swap': (19, range(2)), 'win_lock': (21, range(2)),
    'polling': (22, range(4)), 'mac_mode': (27, range(2)),
}
SLEEP_SECONDS = (60, 180, 300, 600, 1800, 65535)

class SettingsError(ValueError): pass

def packet(command, size, offset=0, data=b''):
    if command not in (5, 6, 7, 8, 9, 10, 11, 21, 27, 38, 39):
        raise SettingsError('未实现的 S99 指令')
    if type(size) is not int or not 1 <= size <= CHUNK:
        raise SettingsError('无效分包长度')
    if type(offset) is not int or not 0 <= offset <= 65535:
        raise SettingsError('无效偏移')
    if command in (6, 9, 11, 21, 39):
        if len(data) != size: raise SettingsError('写入长度不符')
    elif data: raise SettingsError('读取指令不接受写入数据')
    return (bytes((4, 0, 0, command, size, offset & 255, offset >> 8, 0))
            + data).ljust(64, b'\0')

def decode_settings(raw):
    if len(raw) != FUNCTION_SIZE: raise SettingsError('配置长度不符')
    result = {name: raw[offset] for name, (offset, _) in FIELDS.items()}
    result['sleep'] = int.from_bytes(raw[13:15], 'little')
    # Official UI interprets zero as NKRO, any nonzero value as 6-key mode.
    result['six_key'] = raw[20] != 0
    return result

def patch_settings(raw, changes):
    if len(raw) != FUNCTION_SIZE: raise SettingsError('配置长度不符')
    if not isinstance(changes, dict): raise SettingsError('配置必须为对象')
    result = bytearray(raw)
    for name, value in changes.items():
        if name == 'sleep':
            if type(value) is not int or value not in SLEEP_SECONDS:
                raise SettingsError('不支持的休眠时间')
            result[13:15] = value.to_bytes(2, 'little')
        elif name == 'six_key':
            if type(value) is not bool: raise SettingsError('六键模式必须为布尔值')
            result[20] = 1 if value else 0
        else:
            if name not in FIELDS: raise SettingsError('未知配置字段：' + str(name))
            offset, values = FIELDS[name]
            if type(value) is not int or value not in values:
                raise SettingsError('配置值超出范围：' + name)
            result[offset] = value
    return bytes(result)

def encode_macros(macros):
    """Vendor format: 55 AA, total length, count, pointers, 4-byte events."""
    if not isinstance(macros, list) or not 1 <= len(macros) <= 32:
        raise SettingsError('宏库需要 1–32 个宏')
    header = bytearray(16)
    header[:2] = b'\x55\xaa'
    header[4:6] = len(macros).to_bytes(2, 'little')
    addresses, body = bytearray(), bytearray()
    names = set()
    for macro in macros:
        if not isinstance(macro, dict) or set(macro) != {'name', 'events'}:
            raise SettingsError('宏必须包含 name 和 events')
        if not isinstance(macro['name'], str) or not 1 <= len(macro['name']) <= 40:
            raise SettingsError('宏名称需要 1–40 个字符')
        if macro['name'] in names: raise SettingsError('宏名称不能重复')
        names.add(macro['name'])
        events = macro['events']
        if not isinstance(events, list) or not 1 <= len(events) <= 128:
            raise SettingsError('每个宏需要 1–128 个动作')
        addresses += (16 + 2 * len(macros) + len(body)).to_bytes(2, 'little')
        body += len(events).to_bytes(2, 'little') + b'\0\0'
        held = set()
        for event in events:
            if not isinstance(event, dict) or set(event) != {'type', 'key', 'action', 'delay'}:
                raise SettingsError('宏动作需要 type、key、action、delay')
            kind, key, action, delay = (event[k] for k in ('type', 'key', 'action', 'delay'))
            if type(kind) is not int or kind not in (1, 9, 10):
                raise SettingsError('宏类型仅支持鼠标 1、修饰键 9、普通键 10')
            if type(key) is not int or not 1 <= key <= 255:
                raise SettingsError('无效宏键码')
            if action not in ('down', 'up') or type(delay) is not int or not 0 <= delay <= 65535:
                raise SettingsError('无效宏动作或延迟')
            token = kind, key
            if action == 'down':
                if token in held: raise SettingsError('宏中有重复按下且未释放的按键')
                held.add(token)
            else:
                if token not in held: raise SettingsError('宏中有未按下就释放的按键')
                held.remove(token)
            body += delay.to_bytes(2, 'little') + bytes((kind | (128 if action == 'down' else 0), key))
        if held: raise SettingsError('宏结束时必须释放全部按键')
    total = 16 + len(addresses) + len(body)
    if total > 8192: raise SettingsError('宏库过大（本工具限制 8 KiB）')
    header[2:4] = total.to_bytes(2, 'little')
    return bytes(header + addresses + body)

def save_backup(token, sections):
    folder = ROOT / 'runtime' / 'backups' / 's99'
    folder.mkdir(parents=True, exist_ok=True)
    data = {k: bytes(v).hex() for k, v in sections.items()}
    obj = {'schema': 1, 'model': 'VGN S99 2.4G', 'receiver': token,
           'created': datetime.now().astimezone().isoformat(), 'sections': data}
    obj['sha256'] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    path = folder / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    return path

class Session:
    def __init__(self, channel): self.channel = channel

    def exchange(self, request):
        # Drain before each request: no transaction ID exists in this protocol.
        deadline = time.monotonic() + .015
        while time.monotonic() < deadline:
            try: self.channel.read(max(.001, deadline-time.monotonic()))
            except TimeoutError: break
        self.channel.write(request, timeout=.25)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try: report = self.channel.read(max(.001, deadline-time.monotonic()))
            except TimeoutError as exc:
                raise TimeoutError('S99 设置读取超时；请按键唤醒，并退出 V HUB 后重新读取') from exc
            if len(report) == 64 and report[:8] == request[:8]:
                return report[8:8+request[4]]
        raise TimeoutError('S99 设置读取超时；请唤醒键盘并退出 V HUB 后重试')

    def read(self, command, length):
        return b''.join(self.exchange(packet(command, min(CHUNK, length-offset), offset))
                        for offset in range(0, length, CHUNK))

    def write(self, command, data):
        for offset in range(0, len(data), CHUNK):
            part = data[offset:offset+CHUNK]
            self.exchange(packet(command, len(part), offset, part))
            time.sleep(.02)

@contextmanager
def session():
    from hid.windows_hid import enumerate_interfaces, HidChannel
    with device_lock():
        candidates = [x for x in enumerate_interfaces() if matches_interface(x)]
        if len(candidates) != 1:
            raise SettingsError('需要连接唯一的 VGN S99 2.4G 接收器')
        with HidChannel(candidates[0]) as channel:
            yield Session(channel), candidates[0].token

class S99Settings:
    def read_settings(self):
        with session() as (dev, token):
            raw = dev.read(5, FUNCTION_SIZE)
            return {'raw': raw, 'values': decode_settings(raw), 'receiver': token}

    def apply_settings(self, changes, expected):
        # Validate all desired values before opening hardware.
        patch_settings(bytes(FUNCTION_SIZE), changes)
        with session() as (dev, token):
            raw = dev.read(5, FUNCTION_SIZE)
            if token != expected['receiver'] or raw != expected['raw']:
                raise SettingsError('设备配置已变化，请重新读取后再应用')
            updated = patch_settings(raw, changes)
            if updated == raw: return self._result(raw, token, None)
            backup = save_backup(token, {'settings': raw})
            try:
                dev.write(6, updated)
                check = dev.read(5, FUNCTION_SIZE)
                if check != updated: raise SettingsError('设备回读与写入不一致')
            except (OSError, ValueError) as exc:
                raise SettingsError(f'应用未完成，设备可能已部分修改；原配置备份：{backup}') from exc
            return self._result(check, token, backup)

    @staticmethod
    def _result(raw, token, backup):
        return {'raw': raw, 'values': decode_settings(raw), 'receiver': token,
                'backup': str(backup) if backup else None}

    def read_keys(self):
        with session() as (dev, token):
            return {'receiver': token, 'default': dev.read(7, MATRIX_SIZE),
                    'normal': dev.read(8, MATRIX_SIZE), 'fn': dev.read(38, MATRIX_SIZE)}

    def apply_key(self, index, value, layer, expected):
        if layer not in ('normal', 'fn') or type(index) is not int or not 0 <= index < MATRIX_SIZE//3:
            raise SettingsError('无效按键或层')
        if not isinstance(value, (tuple, list, bytes)) or len(value) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in value):
            raise SettingsError('按键映射必须为三个字节')
        # Explicitly supported forms, including a default value previously read from this key.
        default = expected['default'][index*3:index*3+3]
        if not (value[0] in (0, 32, 112, 113) or bytes(value) == default):
            raise SettingsError('不支持的按键映射类型')
        read_cmd, write_cmd = (8, 9) if layer == 'normal' else (38, 39)
        with session() as (dev, token):
            raw = dev.read(read_cmd, MATRIX_SIZE)
            if token != expected['receiver'] or raw != expected[layer]:
                raise SettingsError('按键映射已变化，请重新读取')
            updated = bytearray(raw)
            updated[index*3:index*3+3] = bytes(value)
            if bytes(updated) == raw: return expected | {'backup': None}
            backup = save_backup(token, {layer: raw})
            try:
                dev.write(write_cmd, updated)
                check = dev.read(read_cmd, MATRIX_SIZE)
                if check != bytes(updated): raise SettingsError('按键回读不一致')
            except (OSError, ValueError) as exc:
                raise SettingsError(f'改键未完成，可能已部分修改；原映射备份：{backup}') from exc
            return expected | {layer: check, 'backup': str(backup)}

    def install_macros(self, macros, *, replace_confirmed=False):
        data = encode_macros(macros)
        if replace_confirmed is not True:
            raise SettingsError('覆盖宏库前，必须确认已在 V HUB 中保存需要保留的宏')
        with session() as (dev, token):
            # The installed SDK does not implement a complete macro readback.
            # Save current assignments and the exact new library; never call this a full old-library backup.
            normal, fn = dev.read(8, MATRIX_SIZE), dev.read(38, MATRIX_SIZE)
            backup = save_backup(token, {'normal': normal, 'fn': fn, 'new_macro_library': data})
            try: dev.write(21, data)
            except (OSError, ValueError) as exc:
                raise SettingsError(f'宏写入中断，可能只写入了一部分；记录：{backup}') from exc
            return {'receiver': token, 'backup': str(backup), 'count': len(macros),
                    'status': '设备已应答全部宏数据包；尚未验证实际触发'}

    def restore_backup(self, path):
        path = Path(path)
        if path.stat().st_size > 100_000: raise SettingsError('备份文件过大')
        obj = json.loads(path.read_text(encoding='utf-8'))
        if obj.get('schema') != 1 or obj.get('model') != 'VGN S99 2.4G':
            raise SettingsError('不是本工具的 S99 备份')
        sections = obj.get('sections', {})
        if hashlib.sha256(json.dumps(sections, sort_keys=True).encode()).hexdigest() != obj.get('sha256'):
            raise SettingsError('备份校验失败')
        commands = {'settings': (5, 6, FUNCTION_SIZE), 'normal': (8, 9, MATRIX_SIZE), 'fn': (38, 39, MATRIX_SIZE)}
        if not sections or set(sections) - commands.keys():
            raise SettingsError('此记录包含宏数据，不能作为完整旧配置恢复；请选择设置或改键备份')
        decoded = {k: bytes.fromhex(v) for k, v in sections.items()}
        if any(len(v) != commands[k][2] for k, v in decoded.items()):
            raise SettingsError('备份数据长度不符')
        with session() as (dev, token):
            if obj.get('receiver') != token: raise SettingsError('备份与当前接收器不匹配')
            current = {k: dev.read(commands[k][0], len(v)) for k, v in decoded.items()}
            backup = save_backup(token, current)
            try:
                for k, raw in decoded.items():
                    dev.write(commands[k][1], raw)
                    if dev.read(commands[k][0], len(raw)) != raw: raise SettingsError('恢复回读不一致')
            except (OSError, ValueError) as exc:
                raise SettingsError(f'恢复未完成，可能已部分修改；恢复前备份：{backup}') from exc
            return {'backup': str(backup)}
