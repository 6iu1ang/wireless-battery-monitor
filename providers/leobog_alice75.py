"""Alice 75 receiver battery getter, verified with explicit user authorization."""
from providers.base import BatteryState, Provider, now
from hid.windows_hid import enumerate_interfaces
from providers.leobog_protocol import matches_interface, query_battery


class LeobogAlice75Provider(Provider):
    def __init__(self):
        self.last_state = BatteryState('leobog_alice75', 'LEOBOG Alice 75')

    def detect(self):
        # This receiver PID is shared: never probe other interfaces or select
        # arbitrarily among multiple receivers. This installation is user-identified.
        return [i for i in enumerate_interfaces() if (i.vid, i.pid) == (0x3554, 0xFA09)]

    def read_battery(self):
        interfaces = []
        fields = dict(device_id='leobog_alice75', device_name='LEOBOG Alice 75', observed_at=now())
        try:
            interfaces = self.detect()
            if not interfaces:
                state = BatteryState(**fields, connected=False, connection_status='offline',
                                     error_message='未检测到 Alice 75 2.4G 接收器')
            else:
                candidates = [i for i in interfaces if matches_interface(i)]
                if len(candidates) != 1:
                    state = BatteryState(**fields, receiver_connected=True, connection_status='unsupported',
                                         error_message='无法唯一确认已验证的 Alice 75 电量接口')
                else:
                    value = query_battery(candidates[0], timeout=1.0)
                    state = BatteryState(**fields, **value, connected=True, receiver_connected=True,
                                         connection_status='online', last_updated=now(),
                                         data_source='LEOBOG ONE ReadPower / 0x4A')
        except (OSError, ValueError) as exc:
            state = BatteryState(**fields, receiver_connected=bool(interfaces),
                                 connection_status='unresponsive' if interfaces else 'unknown',
                                 error_message=str(exc))
        self.last_state = state
        return state
