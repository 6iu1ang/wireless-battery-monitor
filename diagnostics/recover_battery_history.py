"""Explicit migration of whitelisted, verified hardware records from older builds.

Never scan arbitrary screenshots, fixtures or UI preview reports. Every recovered
reading keeps the original measurement time and identifies its source report.
"""
from dataclasses import asdict
import hashlib
import json

from config.settings import ROOT, prepare_environment
from config.battery_history import BatteryHistory, newer
from providers.base import BatteryReading, BatteryState


def recover(history, folder):
    candidates, evidence = {}, {}

    def accept(key, reading, path):
        if not newer(reading, history.readings.get(key)) or not newer(reading, candidates.get(key)):
            return
        candidates[key] = reading
        evidence[key] = {'file':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                         'battery_percent':reading.battery_percent, 'recorded_at':reading.recorded_at}

    # This named report comes from s99_ui_validation.py's real Provider calls.
    # Its simulated macro editor work never supplies battery values.
    path = folder/'s99_ui_validation.json'
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
        if obj.get('settings_read') is True and obj.get('keys_read') is True and obj.get('cleanup') is True:
            sources = {'logitech_gpw':'Logitech HID++', 'vgn_s99':'VGN S99 官方电量查询',
                       'leobog_alice75':'LEOBOG ONE ReadPower'}
            for key, prefix in sources.items():
                state = obj.get('states', {}).get(key, {})
                if (state.get('device_id') != key or state.get('connected') is not True or
                        state.get('connection_status') != 'online' or state.get('error_message') or
                        not state.get('data_source', '').startswith(prefix)):
                    continue
                try:
                    reading = BatteryState(**state).current_reading()
                    if reading is not None:
                        accept(key, BatteryReading(reading.battery_percent, reading.battery_level,
                               reading.recorded_at, reading.estimated, reading.voltage_mv,
                               reading.data_source+'；旧版实测记录：s99_ui_validation.json'), path)
                except (TypeError, ValueError):
                    continue
    except (OSError, ValueError, TypeError, AttributeError):
        pass

    for name, key, source in [('s99_hardware_probe.json','vgn_s99','VGN S99 / 0x1A'),
                              ('alice75_hardware_probe.json','leobog_alice75','LEOBOG ONE / 0x4A')]:
        path = folder/name
        try:
            obj = json.loads(path.read_text(encoding='utf-8'))
            for sample in obj.get('samples', []):
                if sample.get('reply_validated') is not True:
                    continue
                try:
                    reading = BatteryReading(sample['battery_percent'], None, sample['time'],
                                             data_source=source+'；旧版实测记录：'+name)
                    accept(key, reading, path)
                except (KeyError, TypeError, ValueError):
                    continue
        except (OSError, ValueError, TypeError, AttributeError):
            continue

    if candidates:
        history.import_readings(candidates)
    return {'recovered':evidence, 'save_error':history.last_error,
            'saved_readings':{key:asdict(value) for key,value in history.readings.items()}}


if __name__=='__main__':
    prepare_environment()
    report = recover(BatteryHistory(), ROOT/'diagnostics')
    (ROOT/'diagnostics'/'battery-history-recovery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))
