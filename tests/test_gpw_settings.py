"""GPW emulator tests. These tests never open or write a physical device."""
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from providers.gpw_protocol import GpwClient, GpwError, GpwProtocolError, ProtocolError, valid_crc, with_crc, crc
from providers.gpw_settings import (GpwSettings, read_snapshot, patch_profile, key_binding,
    encode_macro, encode_light, validate_macro_storage, save_backup)


def profile():
    b = bytearray(b'\xff'*255)
    b[:3] = bytes((1,1,0))
    for i,v in enumerate([400,800,1600,3200,6400]):
        b[3+i*2:5+i*2] = v.to_bytes(2,'little')
    for i,v in enumerate([1,2,4,8,16,8,8,16]):
        b[32+i*4:36+i*4] = key_binding('mouse',v)
        b[96+i*4:100+i*4] = key_binding('mouse',v)
    b[160:208] = '测试配置'.encode('utf-16le').ljust(48,b'\0')
    b[208:230] = bytes.fromhex('03 0000000000 1f40 00 0000')*2
    return with_crc(b)


class Emulator:
    """Stateful wire emulator, including 255-byte sectors and runtime reloads."""
    def __init__(self):
        self.memory = {i:b'\xff'*255 for i in range(16)}
        self.memory.update({i:profile() for i in range(1,6)})
        directory = bytearray(b'\xff'*255)
        for i in range(5):
            directory[i*4:i*4+4] = bytes((0,i+1,int(i==0),0))
        directory[20:24] = b'\xff\xff\0\0'
        self.memory[0] = with_crc(directory)
        self.mode,self.active,self.stage,self.dpi,self.rate = 1,1,1,800,1
        self.reports,self.frames,self.mutations = [],[],[]
        self.indices = {0:0,0x0005:2,0x2201:12,0x8060:11,0x8100:9,0x8070:7}
        self.pending = None
        self.fail_write = self.bad_readback = False

    def client(self, writable=False):
        return GpwClient(self,writable=writable,budget=50)

    def write(self,frame,timeout):
        self.frames.append(frame)
        feature = next(k for k,v in self.indices.items() if v==frame[2])
        fn, p = frame[3]>>4,frame[4:]
        reply = self.call(feature,fn,p)
        self.reports.append(frame[:4]+reply.ljust(16,b'\0'))

    def read(self,timeout):
        if not self.reports:
            raise TimeoutError('fixture timeout')
        return self.reports.pop(0)

    def call(self,f,fn,p):
        if f==0:
            return bytes((4,2,p[2])) if fn==1 else bytes((self.indices.get(int.from_bytes(p[:2],'big'),0),0,1))
        if f==5:
            name = b'G Pro Wireless Gaming Mouse'
            return bytes((len(name),)) if fn==0 else name[p[0]:p[0]+16]
        if f==0x2201:
            if fn==0:return b'\1'
            if fn==1:return bytes.fromhex('000064e032640000')
            if fn==2:return b'\0'+self.dpi.to_bytes(2,'big')+b'\x03\x20'
            self.dpi=int.from_bytes(p[1:3],'big');self.mutations.append(('dpi',self.dpi));return p
        if f==0x8060:
            if fn==0:return b'\x8b'
            if fn==1:return bytes((self.rate,))
            self.rate=p[0];self.mutations.append(('rate',self.rate));return p
        if f==0x8070:
            if fn==0:return bytes.fromhex('0200030004')
            if fn==1:return bytes((p[0],0,p[0]+1,4,0))
            if fn==2:return p[:2]+(0,1,3,10)[p[1]].to_bytes(2,'big')
            return bytes(16)
        if fn==0:return bytes.fromhex('0103010501081000ff0a04')
        if fn==2:return bytes((self.mode,))
        if fn==4:return self.active.to_bytes(2,'big')
        if fn==11:return bytes((self.stage,))
        if fn==5:
            sector,offset = int.from_bytes(p[:2],'big'),int.from_bytes(p[2:4],'big')
            return self.memory[sector][offset:offset+16]
        self.mutations.append((fn,p))
        if fn==6:
            if self.fail_write:raise OSError('injected write failure')
            self.pending=(int.from_bytes(p[:2],'big'),int.from_bytes(p[4:6],'big'),bytearray())
        elif fn==7:self.pending[2].extend(p)
        elif fn==8:
            s,size,data=self.pending;data=bytes(data[:size])
            if self.bad_readback:data=bytes((data[0]^1,))+data[1:]
            self.memory[s]=data;self.pending=None
        elif fn==1:self.mode=p[0]
        elif fn==3:
            self.active=int.from_bytes(p[:2],'big')
            self.stage=self.memory[self.active][1];self.reload()
        elif fn==12:self.stage=p[0];self.reload()
        return p

    def reload(self):
        raw=self.memory[self.active]
        self.dpi=int.from_bytes(raw[3+self.stage*2:5+self.stage*2],'little')
        self.rate=raw[0]


def sample():
    e=Emulator()
    return read_snapshot(e.client(),'fixture-receiver')


class ProtocolTests(unittest.TestCase):
    def test_identify_and_read_only_blocks_all_setters(self):
        e=Emulator();c=e.client()
        self.assertEqual(c.identify(),'4.2')
        before=len(e.frames)
        for f,fn in [(0x2201,3),(0x8060,2),(0x8100,1),(0x8100,6),(0x8070,3),(0x00c0,0)]:
            with self.subTest(f=f,fn=fn),self.assertRaises(GpwError):c.request(f,fn)
        self.assertEqual(len(e.frames),before)

    def test_setter_client_still_rejects_firmware_pairing_unknown_functions(self):
        e=Emulator();c=e.client(True)
        for f,fn in [(0x00c0,0),(0,2),(0x8100,9),(0x2201,4)]:
            with self.assertRaises(GpwError):c.request(f,fn)
        self.assertFalse(e.frames)

    def test_old_software_id_slot_and_truncated_reply_are_ignored(self):
        e=Emulator();c=e.client();c.features[5]=2
        original=e.write
        def write(frame,timeout):
            original(frame,timeout)
            valid=e.reports.pop()
            wrong=bytearray(valid);wrong[3]^=1
            slot=bytearray(valid);slot[1]=2
            e.reports.extend([bytes(wrong),bytes(slot),valid[:7],valid])
        e.write=write
        self.assertEqual(c.request(5,0)[0],27)

    def test_requests_change_software_id(self):
        e=Emulator();c=e.client();c.request(0,0);c.request(0,0)
        self.assertNotEqual(e.frames[0][3],e.frames[1][3])

    def test_ping_echo_is_verified(self):
        e=Emulator();original=e.call
        e.call=lambda f,fn,p:b'\4\2\0' if f==0 and fn==1 else original(f,fn,p)
        with self.assertRaises(GpwError):e.client().identify()

    def test_matching_protocol_error_is_rejected(self):
        e=Emulator();c=e.client()
        def write(frame,timeout):e.reports.append(bytes((0x11,1,0xff,frame[2],frame[3],2))+bytes(14))
        e.write=write
        with self.assertRaisesRegex(ProtocolError,'返回错误'):c.request(0,0)

    def test_255_byte_sector_final_read_overlaps_and_write_pads(self):
        e=Emulator();c=e.client(True);raw=profile()
        self.assertEqual(c.read_sector(1,255),raw)
        self.assertEqual(e.frames[-1][6:8],b'\0\xef')
        c.write_sector(1,raw)
        self.assertEqual(e.memory[1],raw)
        self.assertTrue(all(len(f)==20 for f in e.frames))
        chunks=[f for f in e.frames if f[2]==9 and f[3]>>4==7]
        self.assertEqual(len(chunks),16)
        self.assertEqual(chunks[-1][-1],255)

    def test_crc_and_rom_write_rejected_before_hardware(self):
        e=Emulator();c=e.client(True)
        for sector,data in [(0x101,profile()),(1,bytes(255)),(1,b'\xff'*255)]:
            with self.assertRaises(GpwError):c.write_sector(sector,data)
        self.assertFalse(e.frames)
        self.assertEqual(crc(b'123456789'),0x29b1)

    def test_erased_restore_accepts_crc_error_only_after_exact_readback(self):
        e=Emulator();c=e.client(True)
        original=e.call
        def call(f,fn,p):
            result=original(f,fn,p)
            if f==0x8100 and fn==8:
                raise GpwProtocolError(f,fn,4)
            return result
        # Exercise the same transport exception as a correlated firmware error.
        e.call=call
        c.write_sector(6,b'\xff'*255,allow_erased=True)
        self.assertEqual(e.memory[6],b'\xff'*255)
        e.bad_readback=True
        with self.assertRaisesRegex(GpwError,'回读不一致'):c.write_sector(6,b'\xff'*255,allow_erased=True)

    def test_normal_profile_never_ignores_invalid_value_error(self):
        e=Emulator();c=e.client(True);original=e.call
        def call(f,fn,p):
            result=original(f,fn,p)
            if f==0x8100 and fn==8:raise GpwProtocolError(f,fn,4)
            return result
        e.call=call
        with self.assertRaises(GpwProtocolError):c.write_sector(2,profile(),allow_erased=True)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.e=Emulator();self.api=GpwSettings();self.c=self.e.client(True)
        self.snap=read_snapshot(self.c,'fixture-receiver')
        self.e.mutations.clear()

    def mocked(self):
        return patch('providers.gpw_settings.session',return_value=nullcontext((self.c,'fixture-receiver')))

    def test_read_snapshot_uses_hardware_capabilities(self):
        caps=self.snap['caps']
        self.assertEqual(caps['dpi'],{'min':100,'max':25600,'step':50,'list':[100,25600]})
        self.assertEqual(caps['rates'],[1,2,4,8])
        self.assertEqual(caps['zones'][1]['effects'],[0,1,3,10])
        self.assertFalse(self.e.mutations)

    def test_read_rejects_bad_directory_crc_or_unknown_layout(self):
        self.e.memory[0]=bytes(255)
        with self.assertRaisesRegex(GpwError,'目录校验'):read_snapshot(self.c,'fixture-receiver')

    def test_patch_preserves_unknown_fields_and_other_layer(self):
        raw=profile()
        out=patch_profile(raw,{'name':'新名称','dpi':[450,800,1600,3200,6400]},self.snap['caps'])
        self.assertEqual(out[13:160],raw[13:160]);self.assertEqual(out[208:-2],raw[208:-2])
        self.assertEqual(out[3:5],(450).to_bytes(2,'little'))
        self.assertTrue(valid_crc(out))

    def test_invalid_values_never_open_hardware(self):
        for changes in [{'firmware':1},{'dpi':[401,800,1600,3200,6400]}, {'report_ms':3},
                        {'dpi':[0]*5},{'default':5},{'shift':True},{'name':'x'*24},
                        {'keys':{0:key_binding('disabled')}}]:
            with self.subTest(changes=changes),patch('providers.gpw_settings.session') as opened:
                with self.assertRaises(GpwError):self.api.apply_profile(1,changes,self.snap)
                opened.assert_not_called()

    def test_unchanged_profile_never_backs_up_or_writes(self):
        with self.mocked(),patch('providers.gpw_settings.save_backup') as backup:
            self.api.apply_profile(1,{},self.snap)
        self.assertFalse(self.e.mutations);backup.assert_not_called()

    def test_stale_or_wrong_receiver_never_writes(self):
        for key in ('receiver','directory','profile'):
            expected=copy.deepcopy(self.snap)
            if key=='profile':expected['profiles'][1]['raw']=bytes(255)
            else:expected[key]='wrong'
            with self.mocked(),self.assertRaises(GpwError):self.api.apply_profile(1,{'name':'new'},expected)
        self.assertFalse(self.e.mutations)

    def test_backup_precedes_write_and_active_dpi_stage_is_preserved(self):
        with self.mocked(),patch('providers.gpw_settings.save_backup',return_value='backup.json') as backup:
            original=self.e.write
            def write(frame,timeout):
                if frame[2]==9 and frame[3]>>4 in (1,3,6,7,8,12):self.assertTrue(backup.called)
                original(frame,timeout)
            self.e.write=write
            result=self.api.apply_profile(1,{'dpi':[400,850,1600,3200,6400]},self.snap)
        self.assertEqual(result['runtime']['dpi'],850)
        self.assertEqual(result['runtime']['stage'],1)
        self.assertEqual(result['backup'],'backup.json')

    def test_inactive_profile_write_does_not_switch_runtime(self):
        with self.mocked(),patch('providers.gpw_settings.save_backup',return_value='backup.json'):
            result=self.api.apply_profile(2,{'report_ms':8},self.snap)
        self.assertEqual(result['runtime'],self.snap['runtime'])

    def test_write_failure_reports_partial_state_and_backup(self):
        self.e.fail_write=True
        with self.mocked(),patch('providers.gpw_settings.save_backup',return_value='backup.json'):
            with self.assertRaisesRegex(GpwError,'backup.json'):self.api.apply_profile(2,{'name':'new'},self.snap)

    def test_readback_mismatch_is_never_success(self):
        self.e.bad_readback=True
        with self.mocked(),patch('providers.gpw_settings.save_backup',return_value='backup.json'):
            with self.assertRaisesRegex(GpwError,'回读不一致'):self.api.apply_profile(2,{'name':'new'},self.snap)

    def test_activate_enables_only_selected_profile_and_preserves_others(self):
        with self.mocked(),patch('providers.gpw_settings.save_backup',return_value='backup.json'):
            result=self.api.activate_profile(3,self.snap)
        self.assertEqual(result['runtime']['active'],3)
        self.assertTrue(result['profiles'][1]['enabled']);self.assertTrue(result['profiles'][3]['enabled'])
        self.assertFalse(result['profiles'][2]['enabled'])

    def test_already_active_profile_keeps_current_dpi_and_does_not_write(self):
        with self.mocked(),patch('providers.gpw_settings.save_backup') as backup:
            result=self.api.activate_profile(1,self.snap)
        self.assertEqual(result['runtime'],self.snap['runtime'])
        self.assertFalse(self.e.mutations);backup.assert_not_called()

    def test_light_encodings_distinguish_period_offsets(self):
        values={'mode':3,'color':'#123456','period':5000,'brightness':40}
        cycle=encode_light(values,[0,1,3,10]);breath=encode_light(values|{'mode':10},[0,1,3,10])
        self.assertEqual(cycle[6:9],bytes.fromhex('138828'))
        self.assertEqual(breath[1:6],bytes.fromhex('1234561388'))
        self.assertEqual(breath[7],40)
        with self.assertRaises(GpwError):encode_light(values,[0,1])

    def test_keyboard_and_media_mappings(self):
        self.assertEqual(key_binding('key',6,1),bytes.fromhex('80020106'))
        self.assertEqual(key_binding('media',0xcd),bytes.fromhex('800300cd'))
        self.assertEqual(key_binding('special',11),bytes.fromhex('900bff00'))

    def test_gshift_change_leaves_normal_layer_untouched(self):
        raw=profile();binding=key_binding('key',6,1)
        out=patch_profile(raw,{'gkeys':{3:binding}},self.snap['caps'])
        self.assertEqual(out[32:96],raw[32:96]);self.assertEqual(out[108:112],binding)

    def test_macro_format_delays_are_big_endian_and_keys_balanced(self):
        events=[{'key':4,'modifiers':0,'action':'down','delay':50},
                {'key':4,'modifiers':0,'action':'up','delay':0}]
        self.assertEqual(encode_macro(events,253),bytes.fromhex('430004400032440004ff'))
        with self.assertRaises(GpwError):encode_macro(events[:1],253)
        with self.assertRaises(GpwError):encode_macro(events,2)
        self.events=events

    def test_macro_new_sector_before_binding_preserves_other_profiles(self):
        events=[{'key':4,'modifiers':0,'action':'down','delay':50},
                {'key':4,'modifiers':0,'action':'up','delay':0}]
        originals=self.e.memory.copy()
        with self.mocked(),patch('providers.gpw_settings.save_backup',return_value='backup.json'):
            result=self.api.install_macro(2,3,'keys',events,self.snap)
        binding=result['profiles'][2]['values']['keys'][3]
        self.assertEqual(binding,bytes((0,6,0,0)))
        self.assertTrue(valid_crc(self.e.memory[6]));validate_macro_storage(self.e.memory[6])
        for s in (0,1,3,4,5):self.assertEqual(self.e.memory[s],originals[s])
        starts=[int.from_bytes(p[:2],'big') for fn,p in self.e.mutations if fn==6]
        self.assertEqual(starts,[6,2])

    def test_unknown_old_macro_prevents_allocation_and_writes(self):
        raw=bytearray(self.e.memory[3]);raw[44:48]=bytes((0,8,0,1));self.e.memory[3]=with_crc(raw)
        snap=read_snapshot(self.c,'fixture-receiver')
        events=[{'key':4,'modifiers':0,'action':'down','delay':0},{'key':4,'modifiers':0,'action':'up','delay':0}]
        with self.mocked(),self.assertRaisesRegex(GpwError,'旧宏'):self.api.install_macro(2,3,'keys',events,snap)
        self.assertFalse(self.e.mutations)

    def test_restore_validates_digest_before_device_open(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            with patch('providers.gpw_settings.ROOT',Path(folder)):
                path=Path(save_backup('fixture-receiver',self.snap['descriptor'],{2:profile()},self.snap['runtime']))
            obj=json.loads(path.read_text(encoding='utf-8'));obj['receiver']='tampered'
            path.write_text(json.dumps(obj),encoding='utf-8')
            with patch('providers.gpw_settings.session') as opened:
                with self.assertRaises(GpwError):self.api.restore_backup(path,self.snap)
                opened.assert_not_called()

    def test_restore_is_backed_up_and_recovers_exact_sector(self):
        old=self.e.memory[2]
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            with patch('providers.gpw_settings.ROOT',Path(folder)):
                path=save_backup('fixture-receiver',self.snap['descriptor'],{2:old},self.snap['runtime'])
            self.e.memory[2]=patch_profile(old,{'name':'changed'},self.snap['caps'])
            expected=read_snapshot(self.c,'fixture-receiver')
            with self.mocked(),patch('providers.gpw_settings.save_backup',return_value='before-restore.json') as backup:
                result=self.api.restore_backup(path,expected)
            self.assertEqual(self.e.memory[2],old);self.assertTrue(backup.called)
            self.assertEqual(result['backup'],'before-restore.json')

    def test_recovery_can_restore_a_profile_with_invalid_crc(self):
        old=self.e.memory[2]
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            with patch('providers.gpw_settings.ROOT',Path(folder)):
                path=save_backup('fixture-receiver',self.snap['descriptor'],{0:self.e.memory[0],2:old},self.snap['runtime'])
            self.e.memory[2]=bytes(255)
            with self.mocked(),patch('providers.gpw_settings.save_backup',return_value='corrupt-state.json'):
                result=self.api.restore_backup(path)
            self.assertEqual(result['profiles'][2]['raw'],old)

    def test_recovery_can_restore_a_corrupt_directory_from_backup(self):
        old=self.e.memory[0]
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            with patch('providers.gpw_settings.ROOT',Path(folder)):
                path=save_backup('fixture-receiver',self.snap['descriptor'],{0:old},self.snap['runtime'])
            self.e.memory[0]=bytes(255)
            with self.mocked(),patch('providers.gpw_settings.save_backup',return_value='corrupt-state.json'):
                self.api.restore_backup(path)
            self.assertEqual(self.e.memory[0],old)

    def test_unreferenced_bounded_macro_can_be_reclaimed_when_no_blank_sector(self):
        events=[{'key':4,'modifiers':0,'action':'down','delay':0},{'key':4,'modifiers':0,'action':'up','delay':0}]
        macro=with_crc(encode_macro(events,253).ljust(255,b'\xff'))
        for s in range(6,16):self.e.memory[s]=macro
        with self.mocked(),patch('providers.gpw_settings.save_backup',return_value='backup.json'):
            result=self.api.install_macro(2,3,'keys',events,self.snap)
        self.assertEqual(result['profiles'][2]['values']['keys'][3],bytes((0,6,0,0)))

    def test_restore_skips_identical_sectors_and_keeps_runtime(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            with patch('providers.gpw_settings.ROOT',Path(folder)):
                path=save_backup('fixture-receiver',self.snap['descriptor'],{0:self.e.memory[0],2:self.e.memory[2]},self.snap['runtime'])
            with self.mocked(),patch('providers.gpw_settings.save_backup',return_value='before.json'):
                self.api.restore_backup(path,self.snap)
            self.assertFalse(self.e.mutations)


if __name__=='__main__':
    unittest.main()
