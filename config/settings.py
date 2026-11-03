import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT/'runtime'
SETTINGS = RUNTIME/'config'/'settings.json'
DEFAULTS = dict(refresh_seconds=60,low_threshold=20,tray_icons='combined',close_to_tray=True,debug_logging=False)

def prepare_environment():
    for name in ('temp','cache','config','logs'):
        (RUNTIME/name).mkdir(parents=True,exist_ok=True)
    for key in ('TEMP','TMP','TMPDIR'):
        os.environ[key] = str(RUNTIME/'temp')
    os.environ['PIP_CACHE_DIR'] = str(RUNTIME/'cache'/'pip')
    os.environ['PYTHONPYCACHEPREFIX'] = str(RUNTIME/'cache'/'pycache')
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['PYTHONUTF8'] = '1'

def validate(data):
    result = DEFAULTS.copy()
    for key,lo,hi in [('refresh_seconds',30,1800),('low_threshold',1,99)]:
        value = data.get(key,result[key])
        if type(value) is not int or not lo<=value<=hi: raise ValueError(f'{key} 必须在 {lo}–{hi} 之间')
        result[key] = value
    if data.get('tray_icons',result['tray_icons']) not in ('combined','mouse','keyboard','both'): raise ValueError('无效的托盘图标设置')
    result['tray_icons'] = data.get('tray_icons',result['tray_icons'])
    for key in ('close_to_tray','debug_logging'):
        if key in data and type(data[key]) is not bool: raise ValueError(f'{key} 必须为布尔值')
        result[key] = data.get(key,result[key])
    return result

def load():
    if not SETTINGS.exists(): return DEFAULTS.copy(),None
    try:
        return validate(json.loads(SETTINGS.read_text(encoding='utf-8'))),None
    except (ValueError,OSError,TypeError) as exc:
        return DEFAULTS.copy(),'配置无法读取，已使用默认设置：'+str(exc)

def save(settings):
    data = validate(settings)
    SETTINGS.parent.mkdir(parents=True,exist_ok=True)
    temporary = SETTINGS.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(SETTINGS)
