"""S99 battery getter, verified on this receiver after explicit user authorization.

Only report 04 / command 1A is sent. No keyboard-input collection is opened.
"""
from providers.base import BatteryState, Provider, now
from hid.windows_hid import enumerate_interfaces
from providers.s99_protocol import matches_interface,query_battery

class VgnS99Provider(Provider):
    def __init__(self):
        self.last_state = BatteryState('vgn_s99','VGN S99')

    def detect(self):
        return [x for x in enumerate_interfaces() if (x.vid,x.pid)==(0x320F,0x5088) and 'S99' in x.product.upper()]

    def read_battery(self):
        interfaces=[]
        try:
            interfaces = self.detect()
            if not interfaces:
                self.last_state = BatteryState('vgn_s99','VGN S99',connected=False,receiver_connected=False,connection_status='offline',observed_at=now(),error_message='未检测到 VGN S99 2.4G 接收器')
                return self.last_state
            candidates=[info for info in interfaces if matches_interface(info)]
            if len(candidates)!=1:
                self.last_state = BatteryState('vgn_s99','VGN S99',connected=None,receiver_connected=True,connection_status='unsupported',data_source='Windows HID 描述信息（未读取电量）',observed_at=now(),error_message='接收器已连接，但无法唯一确认已验证的 S99 电量接口。可按 Fn + 右 Ctrl 查看灯条。')
                return self.last_state
            percent,charging=query_battery(candidates[0],timeout=1.0,with_charging=True)
            self.last_state = BatteryState('vgn_s99','VGN S99',connected=True,receiver_connected=True,connection_status='online',battery_percent=percent,charging=charging,charge_status='charging' if charging else 'not_charging',data_source='VGN S99 官方电量查询 / 0x1A（V HUB 字段定义）',last_updated=now(),observed_at=now())
        except (OSError,ValueError) as exc:
            self.last_state = BatteryState('vgn_s99','VGN S99',connected=None,receiver_connected=bool(interfaces),connection_status='unresponsive' if interfaces else 'unknown',observed_at=now(),error_message=str(exc))
        return self.last_state
