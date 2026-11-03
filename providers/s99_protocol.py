"""S99 battery getter, independently implemented from vendor wire-format facts.

Official Web HUB, index-gX9tcLNJ.js, SHA-256
ef100007e4a6f41c3c8b05aabbd18ba868a94e513cbcb522a4cd278893aea3d2.
The S99 model selects the weisheng controller, report 4, command 0x1A.
No downloaded JavaScript is imported or executed.
"""
import time
from providers.s99_lock import device_lock

REPORT_ID = 0x04
COMMAND = 0x1A
HEADER = bytes((0,0,COMMAND,6,0,0,0))
REQUEST = bytes((REPORT_ID,))+HEADER+bytes(56)

class S99ProtocolError(ValueError):pass

def matches_interface(info):
    fields=tuple(getattr(info,key,None) for key in ('vid','pid','page','usage','input_length','output_length'))
    reports=getattr(info,'reports',{})
    return (fields==(0x320F,0x5088,0xFF1C,0x0092,64,64)
            and getattr(info,'product',None)=='VGN S99 2.4G Dongle'
            and reports.get('input')==[REPORT_ID]
            and reports.get('output')==[REPORT_ID])

def decode_report(report):
    if len(report)!=64 or report[0]!=REPORT_ID:
        raise S99ProtocolError('Not an S99 battery report')
    if report[1:8]!=HEADER:
        raise S99ProtocolError('S99 battery reply header mismatch')
    percent=report[8]
    if percent>100:raise S99ProtocolError('S99 battery percentage out of range')
    return percent

def decode_power(report):
    percent = decode_report(report)
    # Local V HUB EvisionApi DevicePowerInfo.bean(): payload[1] != 0.
    # Actual charge-state transitions have not yet been tested on this device.
    return percent, bool(report[9])

def query_battery(info,timeout=1.0,trace=None,with_charging=False):
    from hid.windows_hid import HidChannel
    if not matches_interface(info):raise S99ProtocolError('S99 interface not on the exact allowlist')
    if not 0<timeout<=1.0:raise ValueError('S99 query timeout must be at most one second')
    with device_lock(), HidChannel(info) as channel:
        # A fresh handle plus a short drain prevents queued old reports being treated as current.
        until=time.monotonic()+0.03
        while time.monotonic()<until:
            try:channel.read(max(0.001,until-time.monotonic()))
            except TimeoutError:break
        deadline=time.monotonic()+timeout
        channel.write(REQUEST,timeout=min(0.25,timeout))
        rejected=0
        while time.monotonic()<deadline:
            report=channel.read(max(0.001,deadline-time.monotonic()))
            try:percent=decode_report(report)
            except S99ProtocolError:
                rejected+=1
                continue
            if trace is not None:
                # Store only this exact battery report's header + six battery bytes.
                trace.update(request_header_hex=REQUEST[:8].hex(' '),report_length=len(report),response_header_hex=report[:8].hex(' '),battery_payload_hex=report[8:14].hex(' '),rejected_reports=rejected)
            return decode_power(report) if with_charging else percent
    raise TimeoutError('No matching S99 battery reply')
