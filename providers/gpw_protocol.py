"""GPW1 HID++ settings, independently implemented from documented wire formats.

References: libratbag hidpp20.c/.h (MIT) and Solaar (GPL-2.0-or-later).
Only the verified C539 long-report collection and exact device name are accepted.
The battery client's getter allowlist remains separate and unchanged.
"""
from contextlib import contextmanager
import binascii
import secrets
import time

from providers.logitech_hidpp import ProtocolError, LogitechProvider
from providers.gpw_lock import device_lock

READS = {0x0000: {0, 1}, 0x0005: {0, 1}, 0x2201: {0, 1, 2},
         0x8060: {0, 1}, 0x8070: {0, 1, 2, 14},
         0x8100: {0, 2, 4, 5, 11}}
WRITES = {0x2201: {3}, 0x8060: {2}, 0x8070: {3},
          0x8100: {1, 3, 6, 7, 8, 12}}


class GpwError(ValueError):
    pass


class GpwProtocolError(ProtocolError):
    def __init__(self, feature, function, code):
        self.code = code
        super().__init__(f'GPW 指令 0x{feature:04X}/{function:X} 返回错误 0x{code:02X}')


def crc(data):
    return binascii.crc_hqx(bytes(data), 0xFFFF)


def with_crc(data):
    return bytes(data[:-2]) + crc(data[:-2]).to_bytes(2, 'big')


def valid_crc(data):
    return len(data) >= 2 and crc(data[:-2]) == int.from_bytes(data[-2:], 'big')


class GpwClient:
    def __init__(self, channel, *, writable=False, budget=50, trace=None):
        self.channel = channel
        self.writable = writable
        self.deadline = time.monotonic() + budget
        self.features = {0: 0}
        self.swid = secrets.randbelow(15) + 1
        self.trace = trace

    def request(self, feature, function, params=b''):
        if feature not in READS or (function not in READS[feature] and
                not (self.writable and function in WRITES.get(feature, set()))):
            raise GpwError('未授权的 GPW 指令')
        if len(params) > 16:
            raise GpwError('GPW 报文参数过长')
        index = self.feature(feature)
        if feature and not index:
            raise GpwError(f'鼠标不支持特征 0x{feature:04X}')
        self.swid = self.swid % 15 + 1
        command = (function << 4) | self.swid
        frame = bytes((0x11, 1, index, command)) + bytes(params).ljust(16, b'\0')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('GPW 操作超时，请唤醒鼠标后重试')
        self.channel.write(frame, timeout=min(.4, remaining))
        until = min(self.deadline, time.monotonic() + 1.2)
        while time.monotonic() < until:
            report = self.channel.read(timeout=max(.001, until - time.monotonic()))
            if len(report) != 20 or report[:2] != b'\x11\x01':
                continue
            if report[2] in (0xFF, 0x8F) and report[3:5] == bytes((index, command)):
                raise GpwProtocolError(feature, function, report[5])
            if report[2:4] != bytes((index, command)):
                continue
            if self.trace is not None:
                self.trace.append({'feature': f'0x{feature:04X}', 'function': function,
                                   'params': bytes(params).hex(), 'reply': report[4:].hex()})
            return report[4:]
        raise TimeoutError('GPW 无匹配回复，请移动鼠标后重试')

    def feature(self, feature):
        if feature not in READS:
            raise GpwError('不支持的 GPW 特征')
        if feature not in self.features:
            reply = self.request(0, 0, feature.to_bytes(2, 'big'))
            self.features[feature] = reply[0] if reply[0] != 255 else 0
        return self.features[feature]

    def identify(self):
        echo = secrets.randbelow(255) + 1
        reply = self.request(0, 1, bytes((0, 0, echo)))
        if reply[0] < 2 or reply[2] != echo:
            raise GpwError('GPW 协议身份验证失败')
        length = self.request(0x0005, 0)[0]
        if not 1 <= length <= 96:
            raise GpwError('设备名称长度无效')
        name = bytearray()
        while len(name) < length:
            name.extend(self.request(0x0005, 1, bytes((len(name),)))[:length-len(name)])
        if name.decode('utf-8', errors='replace') != 'G Pro Wireless Gaming Mouse':
            raise GpwError('当前接收器上的设备并非 GPW 一代')
        return f'{reply[0]}.{reply[1]}'

    def read_sector(self, sector, size):
        if not 0 <= sector <= 0x1FF or not 16 <= size <= 4096:
            raise GpwError('无效板载存储区域')
        data = bytearray(size)
        for offset in range(0, size, 16):
            offset = min(offset, size-16)
            params = sector.to_bytes(2, 'big') + offset.to_bytes(2, 'big')
            data[offset:offset+16] = self.request(0x8100, 5, params)
        return bytes(data)

    def write_sector(self, sector, data, *, allow_erased=False):
        # Caller must check allocation, freshness and back up before entering here.
        if not self.writable or not 0 <= sector < 0x100 or not 254 <= len(data) <= 4096 or not (
                valid_crc(data) or (allow_erased and data == b'\xff'*len(data))):
            raise GpwError('拒绝无效的 GPW 板载写入')
        self.request(0x8100, 6, sector.to_bytes(2, 'big') + b'\0\0' + len(data).to_bytes(2, 'big'))
        for offset in range(0, len(data), 16):
            self.request(0x8100, 7, data[offset:offset+16].ljust(16, b'\xff'))
        try:
            self.request(0x8100, 8)
        except GpwProtocolError as exc:
            # GPW1 commits an all-FF sector but reports INVALID_VALUE because
            # erased flash has no valid CRC. Only a verified backup restore may
            # accept this, and only if every byte subsequently matches exactly.
            if not (allow_erased and data == b'\xff'*len(data) and exc.code == 4):
                raise
        check = self.read_sector(sector, len(data))
        if check != data:
            raise GpwError('板载配置回读不一致')


@contextmanager
def session(*, writable=False, trace=None):
    from hid.windows_hid import HidChannel
    with device_lock():
        candidates = LogitechProvider().detect(include_wired=False)
        if len(candidates) != 1:
            raise GpwError('请连接唯一的 GPW 一代 LIGHTSPEED 接收器')
        info = candidates[0]
        with HidChannel(info) as channel:
            client = GpwClient(channel, writable=writable, trace=trace)
            client.identify()
            yield client, info.token
