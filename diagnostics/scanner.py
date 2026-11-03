"""Metadata-only inventory; does not read keyboard input or issue device commands."""
from datetime import datetime
import json
from pathlib import Path
from hid.windows_hid import enumerate_interfaces

ROOT = Path(__file__).resolve().parents[1]
BATTERY_USAGES = {(0x06,0x20),(0x85,0x66)}
TARGETS = [(0x046d,0xc539,'Logitech LIGHTSPEED / GPW 接收器'),
           (0x046d,0xc088,'Logitech GPW USB 有线设备'),
           (0x320f,0x5088,'VGN S99 接收器（产品字符串已确认）'),
           (0x3554,0xfa09,'LEOBOG Alice 75 接收器（用户确认型号，共享 PID）')]

def standard_fields(interface):
    return [v for v in interface.values if any(v['page']==p and v['usage_min'] <= u <= v['usage_max'] for p,u in BATTERY_USAGES)]

def scan():
    interfaces = enumerate_interfaces()
    rows = ['# 本机 HID 设备清单','',f'扫描时间：{datetime.now().astimezone().isoformat(timespec="seconds")}','', '仅元数据查询；没有发送 Output/Feature Report，没有读取实际键盘输入。路径以 SHA-256 截断值脱敏；未查询序列号。','']
    for vid,pid,title in TARGETS:
        rows += [f'## {title} — {vid:04X}:{pid:04X}','', '| 接口 | 路径摘要 | 制造商 / 产品 | Usage Page:Usage | In / Out / Feature 字节 | Report IDs (In / Out / Feature) | 状态 |','|---|---|---|---|---|---|---|']
        for x in interfaces:
            if (x.vid,x.pid)!=(vid,pid): continue
            ids = ' / '.join(','.join(f'{n:02X}' for n in x.reports.get(k,[])) or '—' for k in ('input','output','feature'))
            rows.append(f'| {x.label} | {x.token} | {x.manufacturer} / {x.product} | {x.page:04X}:{x.usage:04X} | {x.input_length} / {x.output_length} / {x.feature_length} | {ids} | 接口在场；{x.error or "描述成功"} |')
        if not any((x.vid,x.pid)==(vid,pid) for x in interfaces):rows+=['','本次扫描未检测到此接收器。']
        rows += ['','标准电池字段：'+str([dict(interface=x.label,fields=standard_fields(x)) for x in interfaces if (x.vid,x.pid)==(vid,pid) and standard_fields(x)]),'']
    rows += ['## 判断与下一步','','罗技仅选择 FF00:0002、20 字节、Report ID 11 的 HID++ 长报告集合；通过 root ping、特征索引响应再确认协议。接收器在场不等于鼠标在线。','','VGN：没有标准电量 Usage；后续已从官方 Web HUB 核实并经用户授权验证 FF1C:0092 / Report ID 04 / 0x1A 电量 getter，三次均为 51%，详见 protocol_findings.md 与 diagnostics/s99_hardware_probe.json。本扫描器本身仍只查询元数据，不发送电量命令。接收器在场不单独作为键盘在线依据。Windows HID API 提供的是预解析能力表，不冒充完整原始 Report Descriptor。','', '## 其他接口（元数据）','']
    rows.insert(rows.index('## 其他接口（元数据）'),'Alice 75：仅使用已验证 FF02:0002、20/20/0 字节、Report ID 13 的厂商集合和 ReadPower 0x4A；PID 多型号共享，限制于用户确认的设备。不会查询 FF04 或标准输入集合；本扫描本身不发送电量命令。')
    rows.extend(f'- {x.vid:04X}:{x.pid:04X} {x.label} {x.page:04X}:{x.usage:04X} {x.product} {x.error}' for x in interfaces if (x.vid,x.pid) not in {(v,p) for v,p,_ in TARGETS})
    (ROOT/'device_inventory.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    (ROOT/'diagnostics'/'inventory.json').write_text(json.dumps([x.public() for x in interfaces],ensure_ascii=False,indent=2),encoding='utf-8')
    return interfaces

if __name__ == '__main__':
    data = scan()
    print(f'Scanned {len(data)} HID collections; saved device_inventory.md and diagnostics/inventory.json')
