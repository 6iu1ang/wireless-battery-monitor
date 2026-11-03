from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')

@dataclass(frozen=True)
class BatteryReading:
    battery_percent: Optional[int]
    battery_level: Optional[str]
    recorded_at: str
    estimated: bool = False
    voltage_mv: Optional[int] = None
    data_source: str = ''

    def __post_init__(self):
        if self.battery_percent is not None and (type(self.battery_percent) is not int or not 0 <= self.battery_percent <= 100):
            raise ValueError('Invalid historical percentage')
        if self.battery_level is not None and (not isinstance(self.battery_level, str) or not 1 <= len(self.battery_level) <= 30):
            raise ValueError('Invalid historical level')
        if self.battery_percent is None and not self.battery_level:
            raise ValueError('Historical reading requires a value')
        if not isinstance(self.recorded_at, str) or datetime.fromisoformat(self.recorded_at).tzinfo is None:
            raise ValueError('Historical reading requires a dated timestamp')
        if type(self.estimated) is not bool or not isinstance(self.data_source, str) or len(self.data_source) > 256:
            raise ValueError('Invalid historical metadata')
        if self.voltage_mv is not None and (type(self.voltage_mv) is not int or not 0 <= self.voltage_mv <= 10000):
            raise ValueError('Invalid historical voltage')

@dataclass
class BatteryState:
    device_id: str
    device_name: str
    connected: Optional[bool] = None
    battery_percent: Optional[int] = None
    battery_level: Optional[str] = None
    charging: Optional[bool] = None
    data_source: str = ''
    last_updated: Optional[str] = None
    error_message: Optional[str] = None
    receiver_connected: bool = False
    connection_status: str = 'unknown'
    charge_status: str = 'unknown'
    observed_at: Optional[str] = None
    voltage_mv: Optional[int] = None
    estimated: bool = False
    last_reading: Optional[BatteryReading] = None

    def __post_init__(self):
        p = self.battery_percent
        if p is not None and (type(p) is not int or not 0 <= p <= 100):
            raise ValueError('Invalid battery percentage')
        if self.connected is not True and (p is not None or self.charging is not None):
            raise ValueError('Battery data requires a responding device')

    def to_dict(self): return asdict(self)

    def current_reading(self):
        if self.connected is True and self.last_updated and (self.battery_percent is not None or self.battery_level):
            return BatteryReading(self.battery_percent, self.battery_level, self.last_updated,
                                  self.estimated, self.voltage_mv, self.data_source)
        return None

    @property
    def uses_last_reading(self):
        return (self.last_reading is not None and self.battery_percent is None and not self.battery_level
                and self.connection_status in ('unresponsive', 'unknown', 'reading', 'offline', 'unsupported'))

    @property
    def display_percent(self):
        return self.last_reading.battery_percent if self.uses_last_reading else self.battery_percent

    @property
    def display_estimated(self):
        return self.last_reading.estimated if self.uses_last_reading else self.estimated

    @property
    def display_updated(self):
        return self.last_reading.recorded_at if self.uses_last_reading else self.last_updated

    @property
    def display_battery(self):
        if self.display_percent is not None: return ('约 ' if self.display_estimated else '')+f'{self.display_percent}%'
        level = self.last_reading.battery_level if self.uses_last_reading else self.battery_level
        if level: return level
        return '暂不支持' if self.connection_status == 'unsupported' else '未知'

class Provider:
    def detect(self): raise NotImplementedError
    def read_battery(self): raise NotImplementedError
    def get_connection_status(self): return self.last_state.connection_status
