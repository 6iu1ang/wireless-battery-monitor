"""GPW1 onboard editor: preserve unknown bytes, back up, compare and verify.

Wire field positions are documented by libratbag and Solaar; no G HUB runtime.
Macros use their own unused sector, never replacing an existing macro library.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path

from config.settings import ROOT
from providers.gpw_protocol import GpwError, session, valid_crc, with_crc

MODEL = 'Logitech GPW1 LIGHTSPEED'
EFFECTS = {0: '关闭', 1: '常亮', 3: '循环变色', 10: '呼吸'}
BUTTONS = ['左键', '右键', '滚轮中键', '左侧后退', '左侧前进', '底部 DPI 键', '右侧后退', '右侧前进']
SPECIALS = {0: '无动作', 3: 'DPI 提高', 4: 'DPI 降低', 5: 'DPI 循环',
            6: '默认 DPI', 7: 'DPI Shift', 8: '下一配置', 9: '上一配置', 10: '循环配置', 11: 'G-Shift'}
MOUSE = {'鼠标左键': 1, '鼠标右键': 2, '鼠标中键': 4, '鼠标后退': 8, '鼠标前进': 16}
MEDIA = {'静音': 0xE2, '音量增加': 0xE9, '音量减少': 0xEA, '播放/暂停': 0xCD,
         '下一曲': 0xB5, '上一曲': 0xB6}


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise GpwError(f'{label}需要 {low}–{high} 的整数')
    return value


def dpi_capabilities(reply):
    if reply[0] != 0:
        raise GpwError('不支持的传感器编号')
    values, step = [], 0
    for offset in range(1, 14, 2):
        value = int.from_bytes(reply[offset:offset+2], 'big')
        if not value:
            break
        if value > 0xE000:
            step = value - 0xE000
        else:
            values.append(value)
    if not values:
        raise GpwError('无法确定 DPI 范围')
    return {'min': min(values), 'max': max(values), 'step': step, 'list': values}


def validate_dpi(value, caps, *, zero=False):
    if zero and value == 0 and type(value) is int:
        return
    integer(value, caps['min'], caps['max'], 'DPI')
    if (caps['step'] and (value-caps['min']) % caps['step']) or (not caps['step'] and value not in caps['list']):
        raise GpwError('DPI 不符合设备支持的步进')


def decode_light(raw):
    mode = raw[0]
    return {'mode': mode, 'color': '#'+raw[1:4].hex(),
            'period': int.from_bytes(raw[6:8] if mode == 3 else raw[4:6], 'big'),
            'brightness': (raw[8] if mode == 3 else raw[7]) or 100}


def encode_light(values, supported):
    if not isinstance(values, dict) or set(values) != {'mode', 'color', 'period', 'brightness'}:
        raise GpwError('灯效字段不完整')
    mode = values['mode']
    if type(mode) is not int or mode not in EFFECTS or mode not in supported:
        raise GpwError('该灯区不支持所选灯效')
    color = values['color']
    if not isinstance(color, str) or len(color) != 7 or not color.startswith('#'):
        raise GpwError('颜色应为 #RRGGBB')
    try:
        rgb = bytes.fromhex(color[1:])
    except ValueError as exc:
        raise GpwError('颜色应为 #RRGGBB') from exc
    if len(rgb) != 3:
        raise GpwError('颜色应为 #RRGGBB')
    period = integer(values['period'], 1000, 20000, '灯效周期（毫秒）')
    brightness = integer(values['brightness'], 1, 100, '亮度')
    data = bytearray(11)
    data[0] = mode
    if mode in (1, 10):
        data[1:4] = rgb
    if mode == 3:
        data[6:8] = period.to_bytes(2, 'big')
        data[8] = brightness if brightness < 100 else 0
    elif mode == 10:
        data[4:6] = period.to_bytes(2, 'big')
        data[6] = 0  # default waveform
        data[7] = brightness if brightness < 100 else 0
    return bytes(data)


def decode_profile(raw, buttons=8, shift=True):
    if len(raw) < 254 or not valid_crc(raw):
        raise GpwError('板载配置校验失败，拒绝编辑')
    name = raw[160:208].decode('utf-16le', errors='replace').rstrip('\0\uffff')
    return {'name': name, 'report_ms': raw[0], 'default': raw[1], 'shift': raw[2],
            'dpi': [int.from_bytes(raw[i:i+2], 'little') for i in range(3, 13, 2)],
            'lights': [decode_light(raw[208+i*11:219+i*11]) for i in range(2)],
            'keys': [raw[32+i*4:36+i*4] for i in range(buttons)],
            'gkeys': [raw[96+i*4:100+i*4] for i in range(buttons)] if shift else []}


def patch_profile(raw, changes, caps):
    if not isinstance(changes, dict) or set(changes) - {'name', 'report_ms', 'default', 'shift', 'dpi', 'lights', 'keys', 'gkeys'}:
        raise GpwError('未知 GPW 配置字段')
    data = bytearray(raw)
    old = decode_profile(raw, caps['buttons'], caps['gshift'])
    values = old | changes
    dpi = values['dpi']
    if not isinstance(dpi, list) or len(dpi) != 5:
        raise GpwError('需要五个 DPI 槽位，0 表示禁用')
    for v in dpi:
        validate_dpi(v, caps['dpi'], zero=True)
    for k in ('default', 'shift'):
        integer(values[k], 0, 4, 'DPI 档位')
        if not dpi[values[k]]:
            raise GpwError('默认和 Shift 档位不能指向禁用的 DPI')
    if values['report_ms'] not in caps['rates'] or type(values['report_ms']) is not int:
        raise GpwError('不支持该回报率')
    if 'name' in changes:
        if not isinstance(values['name'], str) or not values['name'].strip() or '\0' in values['name']:
            raise GpwError('请填写有效配置名称')
        encoded = values['name'].encode('utf-16le')
        if len(encoded) > 46:
            raise GpwError('配置名称最多 23 个 UTF-16 字符')
        data[160:208] = encoded.ljust(48, b'\0')
    data[:3] = bytes((values['report_ms'], values['default'], values['shift']))
    for i, v in enumerate(dpi):
        data[3+i*2:5+i*2] = v.to_bytes(2, 'little')
    if 'lights' in changes:
        lights = changes['lights']
        if not isinstance(lights, dict):
            raise GpwError('灯效修改应按灯区编号提供')
        for zone, light in lights.items():
            integer(zone, 0, min(2, len(caps['zones']))-1, '灯区编号')
            data[208+zone*11:219+zone*11] = encode_light(light, caps['zones'][zone]['effects'])
    for key, offset in (('keys', 32), ('gkeys', 96)):
        if key not in changes:
            continue
        if key == 'gkeys' and not caps['gshift']:
            raise GpwError('设备不支持 G-Shift')
        if not isinstance(changes[key], dict):
            raise GpwError('按键修改格式错误')
        for index, binding in changes[key].items():
            integer(index, 0, caps['buttons']-1, '按键编号')
            binding = bytes(binding)
            if len(binding) != 4 or (binding[0] not in (0x80, 0x90) and binding != old[key][index]):
                raise GpwError('不支持该按键类型；宏请使用宏页面')
            if binding != old[key][index]:
                valid = ((binding[:2] == b'\x80\x01' and int.from_bytes(binding[2:], 'big') in MOUSE.values()) or
                         (binding[:2] == b'\x80\x02' and binding[3] <= 231 and (binding[2] or binding[3])) or
                         (binding[:2] == b'\x80\x03' and int.from_bytes(binding[2:], 'big') in MEDIA.values()) or
                         binding == b'\x80\0\xff\xff' or (binding[0] == 0x90 and binding[1] in SPECIALS))
                if not valid:
                    raise GpwError('未知的按键动作编码')
            # Never remove the sole left-click binding from the normal layer.
            data[offset+index*4:offset+index*4+4] = binding
    if b'\x80\x01\x00\x01' not in [bytes(data[i:i+4]) for i in range(32, 32+caps['buttons']*4, 4)]:
        raise GpwError('普通层必须保留至少一个鼠标左键')
    return with_crc(data)


def key_binding(kind, value=0, modifiers=0):
    if kind == 'mouse' and value in MOUSE.values():
        return b'\x80\x01' + value.to_bytes(2, 'big')
    if kind == 'key':
        integer(value, 0, 231, '按键码')
        integer(modifiers, 0, 255, '修饰键')
        if not (value or modifiers):
            raise GpwError('请选择按键或修饰键')
        return bytes((0x80, 2, modifiers, value))
    if kind == 'media' and value in MEDIA.values():
        return b'\x80\x03' + value.to_bytes(2, 'big')
    if kind == 'special' and value in SPECIALS:
        return bytes((0x90, value, 0xFF, 0))
    if kind == 'disabled':
        return b'\x80\0\xff\xff'
    raise GpwError('不支持的按键映射')


def encode_macro(events, capacity):
    """Simple one-shot keyboard sequence; delay is after each action (ms)."""
    if not isinstance(events, list) or not 1 <= len(events) <= 80:
        raise GpwError('宏需要 1–80 个动作')
    held, data = set(), bytearray()
    for e in events:
        if not isinstance(e, dict) or set(e) != {'key', 'modifiers', 'action', 'delay'}:
            raise GpwError('宏动作字段错误')
        key = integer(e['key'], 0, 231, '宏按键')
        mod = integer(e['modifiers'], 0, 255, '宏修饰键')
        delay = integer(e['delay'], 0, 65535, '宏延迟')
        token = mod, key
        if not (mod or key) or e['action'] not in ('down', 'up'):
            raise GpwError('宏按键或动作无效')
        if e['action'] == 'down':
            if token in held:
                raise GpwError('宏中按键重复按下')
            held.add(token)
            data += bytes((0x43, mod, key))
        else:
            if token not in held:
                raise GpwError('宏中按键尚未按下')
            held.remove(token)
            data += bytes((0x44, mod, key))
        if delay:
            data += b'\x40' + delay.to_bytes(2, 'big')
    if held:
        raise GpwError('宏结束前必须释放全部按键')
    data.append(0xFF)
    if len(data) > capacity:
        raise GpwError(f'宏超过单个板载存储区容量（{capacity} 字节）')
    return bytes(data)


def save_backup(receiver, desc, sectors, runtime):
    obj = {'schema': 1, 'model': MODEL, 'receiver': receiver, 'descriptor': desc.hex(),
           'created': datetime.now().astimezone().isoformat(),
           'sectors': {str(k): v.hex() for k, v in sectors.items()}, 'runtime': runtime}
    obj['sha256'] = hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    folder = ROOT/'runtime'/'backups'/'gpw'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/(datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json')
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(path)


def read_snapshot(c, token):
    desc = c.request(0x8100, 0)
    count, buttons, sectors, size = desc[3], desc[5], desc[6], int.from_bytes(desc[7:9], 'big')
    if (desc[:3] != b'\x01\x03\x01' or not 1 <= count <= 5 or not 1 <= buttons <= 8
            or not count+1 <= sectors <= 32 or size not in (255, 256)):
        raise GpwError('当前固件板载格式尚未适配')
    directory = c.read_sector(0, size)
    if not valid_crc(directory):
        raise GpwError('板载目录校验失败，请先用原软件初始化板载配置')
    profiles, seen = {}, {0}
    for i in range(count):
        entry = directory[i*4:i*4+4]
        sector = int.from_bytes(entry[:2], 'big')
        if not 0 < sector < sectors or sector in seen or entry[2] not in (0, 1):
            raise GpwError('板载目录内容异常，拒绝修改')
        seen.add(sector)
        raw = c.read_sector(sector, size)
        profiles[i+1] = {'sector': sector, 'enabled': bool(entry[2]), 'raw': raw,
                         'values': decode_profile(raw, buttons, desc[9] & 3 == 2)}
    sensor_count = c.request(0x2201, 0)[0]
    if sensor_count != 1:
        raise GpwError('不支持该传感器布局')
    dpi = dpi_capabilities(c.request(0x2201, 1, b'\0'))
    rate_mask = c.request(0x8060, 0)[0]
    rates = [ms for ms in (1, 2, 4, 8) if rate_mask & (1 << (ms-1))]
    zones = []
    info = c.request(0x8070, 0)
    if not 1 <= info[0] <= 2:
        raise GpwError('不支持该灯区布局')
    for z in range(info[0]):
        zi = c.request(0x8070, 1, bytes((z,)))
        if zi[0] != z or zi[3] > 16:
            raise GpwError('灯区能力回复异常')
        effects = []
        for e in range(zi[3]):
            ei = c.request(0x8070, 2, bytes((z, e)))
            if ei[:2] != bytes((z, e)):
                raise GpwError('灯效能力回复不匹配')
            effects.append(int.from_bytes(ei[2:4], 'big'))
        zones.append({'location': int.from_bytes(zi[1:3], 'big'), 'effects': effects})
    mode = c.request(0x8100, 2)[0]
    active = int.from_bytes(c.request(0x8100, 4)[:2], 'big')
    stage = c.request(0x8100, 11)[0]
    runtime_dpi = int.from_bytes(c.request(0x2201, 2, b'\0')[1:3], 'big')
    runtime_rate = c.request(0x8060, 1)[0]
    if mode not in (1, 2):
        raise GpwError('设备工作模式异常')
    if runtime_rate not in rates or stage > 4:
        raise GpwError('当前回报率或 DPI 档位异常')
    validate_dpi(runtime_dpi, dpi)
    for profile in profiles.values():
        values = profile['values']
        if values['report_ms'] not in rates or values['default'] > 4 or values['shift'] > 4:
            raise GpwError('板载配置的回报率或默认档位异常')
    return {'receiver': token, 'descriptor': desc, 'directory': directory, 'profiles': profiles,
            'caps': {'dpi': dpi, 'rates': rates, 'zones': zones, 'buttons': buttons,
                     'gshift': desc[9] & 3 == 2, 'size': size, 'sectors': sectors},
            'runtime': {'mode': mode, 'active': active, 'stage': stage,
                        'dpi': runtime_dpi, 'report_ms': runtime_rate}}


def assert_fresh(actual, expected):
    if not expected or any(actual[k] != expected[k] for k in ('receiver', 'descriptor', 'directory')):
        raise GpwError('设备或板载配置已变化，请重新读取')
    if {k: v['raw'] for k, v in actual['profiles'].items()} != {k: v['raw'] for k, v in expected['profiles'].items()}:
        raise GpwError('板载配置已被其他软件修改，请重新读取')


def read_restore_state(c, token, backup):
    """Read raw state even when an interrupted write left an invalid profile CRC."""
    desc = c.request(0x8100, 0)
    if backup.get('receiver') != token or backup.get('descriptor') != desc.hex():
        raise GpwError('备份与当前接收器或固件不匹配')
    count, buttons, sectors, size = desc[3], desc[5], desc[6], int.from_bytes(desc[7:9], 'big')
    if desc[:3] != b'\x01\x03\x01' or not 1 <= count <= 5 or not 1 <= buttons <= 8 or size not in (255,256) or not count+1 <= sectors <= 32:
        raise GpwError('备份的板载布局尚未适配')
    directory = c.read_sector(0, size)
    mapping = bytes.fromhex(backup.get('sectors', {}).get('0', directory.hex()))
    if len(mapping) != size or not valid_crc(mapping):
        raise GpwError('无法从当前设备或备份取得有效的板载目录')
    profiles = {}
    seen = {0}
    for n in range(1,count+1):
        sector = int.from_bytes(mapping[(n-1)*4:(n-1)*4+2], 'big')
        if not 0 < sector < sectors or sector in seen:
            raise GpwError('恢复目录的存储区编号异常')
        seen.add(sector)
        profiles[n] = {'sector':sector, 'raw':c.read_sector(sector,size)}
    rt = {'mode':c.request(0x8100,2)[0], 'active':int.from_bytes(c.request(0x8100,4)[:2],'big'),
          'stage':c.request(0x8100,11)[0], 'dpi':int.from_bytes(c.request(0x2201,2,b'\0')[1:3],'big'),
          'report_ms':c.request(0x8060,1)[0]}
    return {'receiver':token,'descriptor':desc,'directory':directory,'profiles':profiles,'runtime':rt,
            'caps':{'size':size,'sectors':sectors,'buttons':buttons,'gshift':desc[9]&3==2,
                    'dpi':dpi_capabilities(c.request(0x2201,1,b'\0'))}}


def runtime_restore(c, runtime):
    c.request(0x8100, 1, bytes((runtime['mode'],)))
    if c.request(0x8100, 2)[0] != runtime['mode']:
        raise GpwError('工作模式回读不一致')
    if runtime['mode'] == 1:
        c.request(0x8100, 3, runtime['active'].to_bytes(2, 'big'))
        if int.from_bytes(c.request(0x8100, 4)[:2], 'big') != runtime['active']:
            raise GpwError('当前配置回读不一致')
        c.request(0x8100, 12, bytes((runtime['stage'],)))
        if c.request(0x8100, 11)[0] != runtime['stage']:
            raise GpwError('DPI 档位回读不一致')


class GpwSettings:
    def read_settings(self):
        with session() as (c, token):
            return read_snapshot(c, token)

    @staticmethod
    def _write(c, snap, writes, *, reload_active=True):
        originals = {s: c.read_sector(s, snap['caps']['size']) for s in writes}
        for p in snap['profiles'].values():
            if p['sector'] in originals and originals[p['sector']] != p['raw']:
                raise GpwError('配置在写入前已变化，请重新读取')
        backup = save_backup(snap['receiver'], snap['descriptor'], {0:snap['directory']} | originals, snap['runtime'])
        try:
            # Macro sectors first, profiles next, directory last.
            for sector, data in writes.items():
                c.write_sector(sector, data)
            rt = snap['runtime']
            if reload_active and rt['mode'] == 1 and rt['active'] in writes:
                profile = decode_profile(writes[rt['active']], snap['caps']['buttons'], snap['caps']['gshift'])
                runtime_restore(c, rt | {'stage': rt['stage'] if rt['stage'] < 5 and profile['dpi'][rt['stage']] else profile['default']})
            result = read_snapshot(c, snap['receiver'])
            result['backup'] = backup
            return result
        except Exception as exc:
            raise GpwError(f'操作未完成，设备可能已部分修改。请重新读取；备份：{backup}；{exc}') from exc

    def apply_profile(self, number, changes, expected):
        integer(number, 1, len(expected['profiles']), '配置编号')
        # Validate before accessing hardware.
        patch_profile(expected['profiles'][number]['raw'], changes, expected['caps'])
        with session(writable=True) as (c, token):
            snap = read_snapshot(c, token)
            assert_fresh(snap, expected)
            p = snap['profiles'][number]
            updated = patch_profile(p['raw'], changes, snap['caps'])
            if updated == p['raw']:
                return snap | {'backup': None}
            return self._write(c, snap, {p['sector']: updated})

    def activate_profile(self, number, expected):
        integer(number, 1, len(expected['profiles']), '配置编号')
        with session(writable=True) as (c, token):
            snap = read_snapshot(c, token)
            assert_fresh(snap, expected)
            profile = snap['profiles'][number]
            if profile['enabled'] and snap['runtime']['mode'] == 1 and snap['runtime']['active'] == profile['sector']:
                return snap | {'backup': None}
            directory = bytearray(snap['directory'])
            directory[(number-1)*4+2] = 1
            backup = save_backup(token, snap['descriptor'], {0: snap['directory']}, snap['runtime'])
            try:
                if bytes(directory) != snap['directory']:
                    c.write_sector(0, with_crc(directory))
                runtime_restore(c, {'mode': 1, 'active': profile['sector'], 'stage': profile['values']['default']})
                return read_snapshot(c, token) | {'backup': backup}
            except Exception as exc:
                raise GpwError(f'切换未完成，请重新读取；备份：{backup}；{exc}') from exc

    def set_current_dpi(self, dpi, expected):
        validate_dpi(dpi, expected['caps']['dpi'])
        with session(writable=True) as (c, token):
            snap = read_snapshot(c, token)
            assert_fresh(snap, expected)
            if snap['runtime'] != expected['runtime']:
                raise GpwError('当前 DPI 或配置已变化，请重新读取')
            backup = save_backup(token, snap['descriptor'], {}, snap['runtime'])
            try:
                c.request(0x2201, 3, b'\0' + dpi.to_bytes(2, 'big'))
                if int.from_bytes(c.request(0x2201, 2, b'\0')[1:3], 'big') != dpi:
                    raise GpwError('当前 DPI 回读不一致')
                return read_snapshot(c, token) | {'backup': backup}
            except Exception as exc:
                raise GpwError(f'DPI 应用未完成，请重新读取；备份：{backup}；{exc}') from exc

    def install_macro(self, number, button, layer, events, expected):
        integer(number, 1, len(expected['profiles']), '配置编号')
        integer(button, 0, expected['caps']['buttons']-1, '按键编号')
        if layer not in ('keys', 'gkeys') or (layer == 'gkeys' and not expected['caps']['gshift']):
            raise GpwError('无效按键层')
        data = encode_macro(events, expected['caps']['size']-2)
        with session(writable=True) as (c, token):
            snap = read_snapshot(c, token)
            assert_fresh(snap, expected)
            size = snap['caps']['size']
            memory = {s: c.read_sector(s, size) for s in range(snap['caps']['sectors'])}
            # Existing macro references may jump; conservatively refuse allocating
            # alongside any unfamiliar pre-existing macro, even in disabled profiles.
            reserved = {0} | {p['sector'] for p in snap['profiles'].values()}
            for p in snap['profiles'].values():
                for binding in p['values']['keys'] + p['values']['gkeys']:
                    if binding[0] >> 4 in (0, 1):
                        target = ((binding[0] & 15) << 8) | binding[1]
                        if target not in memory or binding[2:] != b'\0\0' or not valid_crc(memory[target]):
                            raise GpwError('存在其他软件创建的宏，无法确认空闲存储区；请先在原软件导出并清除旧宏')
                        # Only our bounded, one-sector, key/delay/END program is accepted.
                        validate_macro_storage(memory[target])
                        reserved.add(target)
            free = next((s for s, raw in memory.items() if s not in reserved and
                         (raw == b'\xff'*size or (valid_crc(raw) and raw[:-2] == b'\xff'*(size-2)))), None)
            if free is None:
                # An unreferenced program in our bounded format can be reclaimed;
                # no live profile points to it and no accepted macro can jump.
                for sector, raw in memory.items():
                    if sector in reserved or not valid_crc(raw):
                        continue
                    try:
                        validate_macro_storage(raw)
                    except GpwError:
                        continue
                    free = sector
                    break
            if free is None:
                raise GpwError('没有可确认的空闲板载宏存储区')
            raw = bytearray(snap['profiles'][number]['raw'])
            offset = (32 if layer == 'keys' else 96) + button*4
            raw[offset:offset+4] = bytes((0, free, 0, 0))
            if layer == 'keys' and b'\x80\x01\0\1' not in [bytes(raw[i:i+4]) for i in range(32, 32+snap['caps']['buttons']*4, 4)]:
                raise GpwError('普通层必须保留至少一个鼠标左键')
            macro = with_crc(data.ljust(size, b'\xff'))
            return self._write(c, snap, {free: macro, snap['profiles'][number]['sector']: with_crc(raw)})

    def restore_backup(self, path, expected=None):
        path = Path(path)
        if path.stat().st_size > 131072:
            raise GpwError('备份文件过大')
        obj = json.loads(path.read_text(encoding='utf-8'))
        digest = obj.pop('sha256', None)
        if (obj.get('schema') != 1 or obj.get('model') != MODEL or
                digest != hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=True).encode()).hexdigest()):
            raise GpwError('备份型号或摘要校验失败')
        with session(writable=True) as (c, token):
            snap = read_restore_state(c, token, obj)
            if expected is not None:
                assert_fresh(snap, expected)
            writes = {int(k): bytes.fromhex(v) for k, v in obj['sectors'].items()}
            size = snap['caps']['size']
            if any(not 0 <= s < snap['caps']['sectors'] or len(b) != size or
                   not (valid_crc(b) or b == b'\xff'*size) for s, b in writes.items()):
                raise GpwError('备份存储区内容无效')
            # Restoring a macro allocation must undo the profile reference first.
            # Do not erase a macro sector now referenced by a different profile.
            prospective = {p['sector']: writes.get(p['sector'], p['raw']) for p in snap['profiles'].values()}
            for raw in prospective.values():
                p = decode_profile(raw, snap['caps']['buttons'], snap['caps']['gshift'])
                for binding in p['keys']+p['gkeys']:
                    if binding[0] >> 4 in (0, 1):
                        target = ((binding[0] & 15) << 8) | binding[1]
                        if target in writes and writes[target] == b'\xff'*size:
                            raise GpwError('其他配置仍引用此宏，无法恢复为空白存储区')
            rt = obj['runtime']
            if rt.get('mode') not in (1, 2) or not any(p['sector'] == rt.get('active') for p in snap['profiles'].values()):
                raise GpwError('备份运行状态异常')
            integer(rt.get('stage'), 0, 4, '备份 DPI 档位')
            validate_dpi(rt.get('dpi'), snap['caps']['dpi'])
            originals = {s: c.read_sector(s, size) for s in writes}
            backup = save_backup(token, snap['descriptor'], originals, snap['runtime'])
            writes = {s:b for s,b in writes.items() if b != originals[s]}
            try:
                profile_sectors = set(prospective)
                for s in sorted(writes, key=lambda s: (s == 0, s not in profile_sectors)):
                    c.write_sector(s, writes[s], allow_erased=True)
                active_modified = rt['active'] in writes
                if snap['runtime'] != rt or active_modified or 0 in writes:
                    runtime_restore(c, rt)
                    c.request(0x2201, 3, b'\0' + rt['dpi'].to_bytes(2, 'big'))
                result = read_snapshot(c, token)
                if result['runtime']['dpi'] != rt['dpi']:
                    raise GpwError('恢复后的 DPI 回读不一致')
                return result | {'backup': backup}
            except Exception as exc:
                raise GpwError(f'恢复未完成，可能已部分修改；恢复前备份：{backup}；{exc}') from exc


def validate_macro_storage(raw):
    """Recognize only a bounded single-sector program, with no jumps/repeats."""
    i = 0
    while i < len(raw)-2:
        tag = raw[i]
        if tag == 255:
            if raw[i+1:-2] != b'\xff'*(len(raw)-i-3):
                break
            return
        if tag not in (0x40, 0x43, 0x44) or i+3 > len(raw)-2:
            break
        i += 3
    raise GpwError('发现无法完整识别的旧宏，拒绝分配存储区')
