"""Bounded LEOBOG/Compx battery getter, independently implemented from native protocol facts.

Source: installed LEOBOG ONE OemDrv.exe, CDev3632::ReadPower at 0x496c00,
packet builder at 0x496460, additive checksum at 0x496d10.
No OEM executable or function is loaded or executed by this module.
"""
import time
from providers.device_lock import alice75_lock

REPORT_ID=0x13
COMMAND=0x4A
REQUEST=bytes((REPORT_ID,COMMAND,1,0,0))+bytes(14)+bytes((0x5E,))

class LeobogProtocolError(ValueError):pass

def matches_interface(info):
    fields=tuple(getattr(info,k,None) for k in ('vid','pid','page','usage','input_length','output_length','feature_length'))
    reports=getattr(info,'reports',{})
    return (fields==(0x3554,0xFA09,0xFF02,2,20,20,0)
            and getattr(info,'manufacturer',None)=='Compx'
            and getattr(info,'product',None)=='2.4G Wireless Receiver'
            and reports.get('input')==[REPORT_ID] and reports.get('output')==[REPORT_ID])

def decode_report(report):
    if len(report)!=20 or report[0]!=REPORT_ID:raise LeobogProtocolError('Not a LEOBOG vendor report')
    if report[1]!=COMMAND:raise LeobogProtocolError('Not a battery getter reply')
    if sum(report[:-1])&255!=report[-1]:raise LeobogProtocolError('LEOBOG report checksum mismatch')
    if report[2]&0x7F!=1 or report[3]!=0 or not 2<=report[4]&15<=14:
        raise LeobogProtocolError('Unexpected LEOBOG battery packet layout')
    percent,flags=report[5:7]
    if percent>100:raise LeobogProtocolError('LEOBOG battery percentage out of range')
    # Exact boolean used by native ReadPower; it does not prove full/discharging states.
    charging=bool(flags&0xF0) and not bool(flags&0x0F)
    return dict(battery_percent=percent,charging=charging,charge_status='charging' if charging else 'not_charging')

def query_battery(info,timeout=1.0,trace=None):
    from hid.windows_hid import HidChannel
    if not matches_interface(info):raise LeobogProtocolError('Receiver does not match the verified vendor interface')
    if not 0<timeout<=1.0:raise ValueError('Query budget must be at most one second')
    deadline=time.monotonic()+timeout
    with alice75_lock(), HidChannel(info) as channel:
        until=min(deadline,time.monotonic()+0.03)
        while time.monotonic()<until:
            try:channel.read(max(0.001,until-time.monotonic()))
            except TimeoutError:break
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('LEOBOG battery query budget expired')
        channel.write(REQUEST,timeout=min(0.25,remaining))
        rejected=0
        while time.monotonic()<deadline:
            report=channel.read(max(0.001,deadline-time.monotonic()))
            try:value=decode_report(report)
            except LeobogProtocolError:
                rejected+=1
                continue
            if trace is not None:
                trace.update(request_hex=REQUEST.hex(' '),response_header_hex=report[:5].hex(' '),battery_payload_hex=report[5:7].hex(' '),response_checksum_hex=f'{report[-1]:02x}',report_length=len(report),rejected_reports=rejected)
            return value
    raise TimeoutError('No valid LEOBOG battery reply')
