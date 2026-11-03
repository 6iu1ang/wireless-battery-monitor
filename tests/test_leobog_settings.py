import contextlib
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

from providers.leobog_settings import (Alice75Settings,AliceSettingsError,Session,MODEL,
    frame,parse_frame,decode_settings,patch_settings,keyboard_value,macro_value,
    encode_macros,save_backup)

def blocks():
    raw=bytearray(128);raw[10]=1;raw[24]=4;raw[58]=3;raw[59]=0x27;raw[-2:]=b'\x5a\xa5'
    palette=bytearray(512);palette[21:24]=bytes((12,34,56));palette[-2:]=b'\x5a\xa5'
    return bytes(raw),bytes(palette)

def reply(command,total,index,layer,data):
    raw=bytes((0x13,command,total,index,(layer<<4)|len(data)))+bytes(data).ljust(14,b'\0')
    return raw+bytes((sum(raw)&255,))

class AliceSettingsTests(unittest.TestCase):
    def test_native_read_request_and_checksum(self):
        self.assertEqual(frame(0x44),bytes.fromhex('13 44 01 00 00')+bytes(14)+bytes((0x58,)))
        self.assertEqual(frame(0x41,layer=2)[4],0x20)
        with self.assertRaises(AliceSettingsError):frame(6)
        with self.assertRaises(AliceSettingsError):frame(1,128,0,0,b'A')
        with self.assertRaises(AliceSettingsError):frame(1,1,0,0,bytes(15))

    def test_read_reassembles_out_of_order_and_ignores_duplicate(self):
        channel=Mock();data=bytes(range(30))
        packets=[reply(0x44,3,i,0,data[i*14:i*14+14]) for i in range(3)]
        channel.read.side_effect=[TimeoutError(),packets[1],packets[1],packets[0],packets[2]]
        self.assertEqual(Session(channel).read(0x44,30),data)
        channel.write.assert_called_once_with(frame(0x44),timeout=.25)

    def test_checksum_error_and_missing_chunks_are_not_accepted(self):
        packet=reply(0x44,1,0,0,bytes(14))
        with self.assertRaises(AliceSettingsError):parse_frame(packet[:-1]+bytes((packet[-1]^1,)),0x44,0)
        channel=Mock();channel.read.side_effect=[TimeoutError(),reply(0x44,2,0,0,bytes(14)),TimeoutError()]
        with self.assertRaises(TimeoutError):Session(channel).read(0x44,28)

    def test_wrong_layer_and_packet_count_abort_read(self):
        for value in [reply(0x41,1,0,1,bytes(14)),reply(0x41,2,0,0,bytes(14))]:
            channel=Mock();channel.read.side_effect=[TimeoutError(),value]
            with self.assertRaises(AliceSettingsError):Session(channel).read(0x41,14)

    def test_write_waits_for_each_matching_ack_without_blind_retry(self):
        channel=Mock();channel.read.side_effect=[TimeoutError(),reply(4,1,0,0,b''),reply(4,1,0,0,b''),reply(4,1,1,0,b'')]
        Session(channel).write(4,bytes(16))
        self.assertEqual(channel.write.call_count,2)
        self.assertEqual([c.args[0][3] for c in channel.write.call_args_list],[0,1])

    def test_unknown_bytes_and_per_mode_nibbles_preserved(self):
        raw,palette=blocks();raw=bytearray(raw);raw[10]=2;raw[61]=0x27;raw=bytes(raw)
        updated,colors=patch_settings(raw,palette,{'speed':4,'sleep':600,'red':99})
        self.assertEqual(updated[61],0x47)
        self.assertEqual(updated[24],20)
        self.assertEqual(colors[42],99)
        self.assertEqual([i for i,(a,b) in enumerate(zip(raw,updated)) if a!=b],[24,61])
        self.assertEqual([i for i,(a,b) in enumerate(zip(palette,colors)) if a!=b],[42])

    def test_custom_mode_and_invalid_parameters_fail_before_open(self):
        raw,palette=blocks();snapshot={'raw':raw,'palette':palette}
        for changes in [{'brightness':5},{'sleep':7},{'mode':99},{'firmware':1},{'sleep':True},{'mode':0,'speed':1}]:
            with patch('providers.leobog_settings.session') as opened:
                with self.assertRaises(AliceSettingsError):Alice75Settings().apply_settings(changes,snapshot)
                opened.assert_not_called()
        config,_=patch_settings(raw,palette,{'mode':21})
        self.assertEqual(config[9:11],bytes((1,21)))

    def test_no_signature_no_write(self):
        raw,palette=blocks();bad=bytes(128)
        with self.assertRaisesRegex(AliceSettingsError,'校验标记'):decode_settings(bad,palette)

    def test_shared_pid_requires_original_identified_receiver(self):
        from providers.leobog_settings import session
        fields=dict(vid=0x3554,pid=0xfa09,page=0xff02,usage=2,input_length=20,output_length=20,feature_length=0,
                    manufacturer='Compx',product='2.4G Wireless Receiver',reports={'input':[19],'output':[19]},token='wrong')
        with patch('hid.windows_hid.enumerate_interfaces',return_value=[types.SimpleNamespace(**fields)]),patch('hid.windows_hid.HidChannel') as opened:
            with self.assertRaisesRegex(AliceSettingsError,'尚未确认为'):
                with session():pass
            opened.assert_not_called()

    def test_stale_state_cannot_write_and_fresh_state_is_backed_up(self):
        raw,palette=blocks();expected={'raw':raw,'palette':palette,'receiver':'receiver-a'}
        dev=Mock();dev.read.side_effect=[raw,palette]
        with patch('providers.leobog_settings.session',return_value=contextlib.nullcontext((dev,'wrong'))):
            with self.assertRaises(AliceSettingsError):Alice75Settings().apply_settings({'sleep':600},expected)
        dev.write.assert_not_called()
        target,_=patch_settings(raw,palette,{'sleep':600});dev=Mock();dev.read.side_effect=[raw,palette,target]
        with patch('providers.leobog_settings.session',return_value=contextlib.nullcontext((dev,'receiver-a'))),patch('providers.leobog_settings.save_backup',return_value=Path('backup.json')) as backup:
            dev.write.side_effect=lambda *args:self.assertTrue(backup.called)
            result=Alice75Settings().apply_settings({'sleep':600},expected)
        self.assertEqual(result['raw'],target);dev.write.assert_called_once_with(4,target)

    def test_key_edit_is_confined_to_selected_physical_slot(self):
        raw=bytearray(512);raw[-2:]=b'\x5a\xa5';raw=bytes(raw)
        expected={'raw':raw,'receiver':'a','layer':1}
        target=bytearray(raw);target[9*4:9*4+4]=keyboard_value(6,1)
        dev=Mock();dev.read.side_effect=[raw,bytes(target)]
        with patch('providers.leobog_settings.session',return_value=contextlib.nullcontext((dev,'a'))),patch('providers.leobog_settings.save_backup',return_value=Path('backup.json')):
            result=Alice75Settings().apply_key(9,keyboard_value(6,1),expected)
        self.assertEqual(result['raw'],bytes(target));dev.write.assert_called_once_with(1,target,1)
        self.assertEqual(macro_value(2),bytes((3,1,1,2)))

    def test_native_macro_directory_and_release_bit(self):
        macro={'name':'A','events':[{'type':10,'key':4,'action':'down','delay':50},{'type':10,'key':4,'action':'up','delay':256}]}
        self.assertEqual(encode_macros([macro]),bytes.fromhex('04 00 0b 00 02 41 00 00 00 32 04 80 01 00 04'))
        with self.assertRaises(AliceSettingsError):encode_macros([macro|{'events':macro['events'][:1]}])
        with patch('providers.leobog_settings.session') as opened:
            with self.assertRaises(AliceSettingsError):Alice75Settings().install_macros([macro])
            opened.assert_not_called()

    def test_tampered_backup_does_not_open_device(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            with patch('providers.leobog_settings.ROOT',Path(folder)):
                path=save_backup('a',{'settings':blocks()[0]})
            obj=json.loads(path.read_text(encoding='utf-8'));obj['sections']['settings']='00'*128
            path.write_text(json.dumps(obj),encoding='utf-8')
            with patch('providers.leobog_settings.session') as opened:
                with self.assertRaises(AliceSettingsError):Alice75Settings().restore_backup(path)
                opened.assert_not_called()

if __name__=='__main__':unittest.main()
