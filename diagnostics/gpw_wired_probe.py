"""Bounded read-only GPW transport evidence; does not change device settings."""
import json
from pathlib import Path

from config.settings import prepare_environment
from providers.logitech_hidpp import LogitechProvider


def run():
    prepare_environment()
    trace = []
    provider = LogitechProvider(trace)
    interfaces = [info.public() for info in provider.detect()]
    samples = [provider.read_battery().to_dict() for _ in range(3)]
    evidence = dict(interfaces=interfaces, samples=samples, hidpp_getter_trace=trace)
    path = Path(__file__).with_name('gpw_wired_probe.json')
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(samples, ensure_ascii=False, indent=2))
    return evidence


if __name__ == '__main__':
    run()
