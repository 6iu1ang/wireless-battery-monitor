import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from providers.s99_settings import (packet, patch_settings, decode_settings, encode_macros,
    Session, SettingsError, S99Settings, save_backup, MATRIX_SIZE)

class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.api=S99Settings()
        self.raw=bytes(range(48))
        self.expected={'raw':self.raw,'receiver':'receiver-a'}
        self.dev=Mock()
        self.dev.read.return_value=self.raw

    @contextlib.contextmanager
    def mocked(self,token='receiver-a'):
        with patch('providers.s99_settings.session',return_value=contextlib.nullcontext((self.dev,token))),patch('providers.s99_settings.save_backup',return_value=Path('backup.json')) as backup:
            yield backup

    def test_changes_preserve_every_unrelated_byte(self):
        updated=patch_settings(self.raw,{'brightness':4,'sleep':600})
        self.assertEqual(updated[2],4)
        self.assertEqual(updated[13:15],b'\x58\x02')
        self.assertEqual([i for i,(a,b) in enumerate(zip(self.raw,updated)) if a!=b],[2,13,14])

    def test_unknown_fields_and_bad_values_cannot_reach_hardware(self):
        for changes in [{'firmware':1},{'brightness':255},{'sleep':0},{'polling':True},{'six_key':1}]:
            with self.subTest(changes=changes),patch('providers.s99_settings.session') as opened:
                with self.assertRaises(SettingsError):self.api.apply_settings(changes,self.expected)
                opened.assert_not_called()

    def test_stale_snapshot_and_other_receiver_never_write(self):
        for token,raw in [('receiver-b',self.raw),('receiver-a',bytes(48))]:
            self.dev.read.return_value=raw
            with self.mocked(token):
                with self.assertRaises(SettingsError):self.api.apply_settings({'brightness':4},self.expected)
            self.dev.write.assert_not_called()

    def test_success_requires_backup_before_write_and_exact_readback(self):
        desired=patch_settings(self.raw,{'brightness':4})
        self.dev.read.side_effect=[self.raw,desired]
        with self.mocked() as backup:
            self.dev.write.side_effect=lambda *args:self.assertTrue(backup.called)
            result=self.api.apply_settings({'brightness':4},self.expected)
        self.assertEqual(result['raw'],desired)
        self.assertEqual(result['backup'],'backup.json')

    def test_readback_mismatch_is_not_success_and_no_blind_retry(self):
        with self.mocked():
            with self.assertRaisesRegex(SettingsError,'可能已部分修改'):
                self.api.apply_settings({'brightness':4},self.expected)
        self.assertEqual(self.dev.write.call_count,1)

    def test_noop_does_not_write_or_backup(self):
        with self.mocked() as backup:self.api.apply_settings({'brightness':2},self.expected)
        self.dev.write.assert_not_called();backup.assert_not_called()

    def test_reply_must_match_offset_and_length(self):
        req=packet(5,24,24)
        channel=Mock()
        wrong=packet(5,24,0)
        good=req[:8]+bytes(range(24))+bytes(32)
        channel.read.side_effect=[TimeoutError(),wrong,good]
        self.assertEqual(Session(channel).exchange(req),bytes(range(24)))

    def test_dangerous_commands_and_oversized_packets_rejected(self):
        for command,size,offset,data in [(13,24,0,b''),(6,24,0,bytes(23)),(5,56,0,b''),(5,24,-1,b'')]:
            with self.assertRaises(SettingsError):packet(command,size,offset,data)

    def test_key_edit_preserves_other_keys_and_reads_same_layer(self):
        raw=bytes([32,0,4])*112
        expected={'receiver':'receiver-a','normal':raw,'fn':bytes(336),'default':raw}
        desired=bytearray(raw);desired[15:18]=bytes((32,0,5))
        self.dev.read.side_effect=[raw,bytes(desired)]
        with self.mocked():result=self.api.apply_key(5,(32,0,5),'normal',expected)
        self.assertEqual(result['normal'],bytes(desired))
        self.dev.write.assert_called_once_with(9,desired)
        self.assertEqual(self.dev.read.call_args_list[0].args,(8,MATRIX_SIZE))

    def test_macros_have_documented_header_offsets_and_balanced_keys(self):
        macro={'name':'A','events':[{'type':10,'key':4,'action':'down','delay':50},{'type':10,'key':4,'action':'up','delay':20}]}
        encoded=encode_macros([macro])
        self.assertEqual(encoded[:6],bytes.fromhex('55 aa 1e 00 01 00'))
        self.assertEqual(encoded[16:],bytes.fromhex('12 00 02 00 00 00 32 00 8a 04 14 00 0a 04'))
        with self.assertRaises(SettingsError):encode_macros([macro | {'events':macro['events'][:1]}])
        with patch('providers.s99_settings.session') as opened:
            with self.assertRaises(SettingsError):self.api.install_macros([macro])
            opened.assert_not_called()

    def test_tampered_backup_never_opens_device(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            with patch('providers.s99_settings.ROOT',Path(folder)):
                backup=save_backup('receiver-a',{'settings':bytes(48)})
            obj=json.loads(backup.read_text(encoding='utf-8'))
            obj['sections']['settings']='ff'*48
            backup.write_text(json.dumps(obj),encoding='utf-8')
            with patch('providers.s99_settings.session') as opened:
                with self.assertRaises(SettingsError):self.api.restore_backup(backup)
                opened.assert_not_called()

    def test_restore_backs_up_current_state_and_checks_each_section(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            with patch('providers.s99_settings.ROOT',Path(folder)):
                backup=save_backup('receiver-a',{'settings':bytes(48)})
            self.dev.read.side_effect=[self.raw,bytes(48)]
            with self.mocked() as save:
                self.api.restore_backup(backup)
                save.assert_called_once_with('receiver-a',{'settings':self.raw})
            self.dev.write.assert_called_once_with(6,bytes(48))

    def test_macro_record_cannot_masquerade_as_old_macro_backup(self):
        with tempfile.TemporaryDirectory(dir=Path('runtime/temp')) as folder:
            with patch('providers.s99_settings.ROOT',Path(folder)):
                backup=save_backup('receiver-a',{'new_macro_library':bytes(24)})
            with patch('providers.s99_settings.session') as opened:
                with self.assertRaisesRegex(SettingsError,'不能作为完整旧配置恢复'):
                    self.api.restore_backup(backup)
                opened.assert_not_called()

if __name__=='__main__':unittest.main()
