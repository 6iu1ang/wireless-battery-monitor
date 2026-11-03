"""Explicit, bounded GPW getter probe, with a redacted protocol trace."""
import json
import time
from pathlib import Path
from providers.logitech_hidpp import LogitechProvider
from providers.vgn_s99 import VgnS99Provider

def run(count=3):
    trace = []
    mouse = LogitechProvider(trace)
    keyboard = VgnS99Provider()
    samples = []
    for i in range(count):
        start = time.monotonic()
        row = {'mouse':mouse.read_battery().to_dict(),'keyboard':keyboard.read_battery().to_dict(),'duration_seconds':round(time.monotonic()-start,3)}
        samples.append(row)
        print(json.dumps(row,ensure_ascii=False))
        if i<count-1: time.sleep(1)
    output = Path(__file__).with_name('hardware_probe.json')
    output.write_text(json.dumps({'samples':samples,'hidpp_getter_trace':trace},ensure_ascii=False,indent=2),encoding='utf-8')
    return samples

if __name__ == '__main__': run()
