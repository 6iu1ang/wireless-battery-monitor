"""Generic Logitech one-cell Li-ion estimate; not a GPW calibration.

Table data: Solaar, lib/logitech_receiver/hidpp20.py,
estimate_battery_level_percentage, GPL-2.0-or-later.
Copyright (C) 2012-2013 Daniel Pavel, 2014-2024 Solaar Contributors.
https://github.com/pwr-Solaar/Solaar/blob/master/lib/logitech_receiver/hidpp20.py
The mapping is approximate, affected by load, charging, temperature and aging.
"""
POINTS = ((3500,0),(3579,2),(3646,5),(3671,10),(3717,20),(3751,30),(3778,40),(3811,50),(3859,60),(3922,70),(3989,80),(4067,90),(4186,100))

def estimate_percent(millivolts):
    if type(millivolts) is not int or not 2500<=millivolts<=5000:
        raise ValueError('Invalid Li-ion voltage')
    if millivolts<=POINTS[0][0]: return 0
    if millivolts>=POINTS[-1][0]: return 100
    for (lo,lp),(hi,hp) in zip(POINTS,POINTS[1:]):
        if lo<=millivolts<=hi: return round(lp+(hp-lp)*(millivolts-lo)/(hi-lo))
    raise ValueError('Voltage is outside mapping')
