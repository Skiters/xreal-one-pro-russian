import unittest
from unittest.mock import patch

import backend
import dump_core
from test_backend import FakeProbe


class Variants(unittest.TestCase):
    def test_encoding_is_not_a_new_sku(self):
        for sku in ('X1112010', 'X1112011', 'X1112099'):
            for encoding in ('ascii', 'utf-16', 'utf-16-le', 'utf-16-be'):
                raw = (sku+'\0').encode(encoding)
                self.assertEqual(backend.sku_text(raw), sku)
        for raw in (b'x1112011', b'X1112011-extra', b'X1112011\0X', b'X1112\xff011'):
            self.assertNotIn(backend.sku_text(raw), backend.SUPPORTED_SKUS)

    def test_unknown_sku_denied_before_transfer(self):
        probe = FakeProbe(sku='X1112099')
        with self.assertRaises(RuntimeError):
            backend.identity(probe)
        self.assertFalse(any(c in (4628,4629,4630) for c,_ in probe.calls))

    def test_unknown_pid_reachable_only_in_dump_list(self):
        device = dict(product_id=0xABCD,interface_number=0,usage_page=0xff00)
        with patch('hid.enumerate',side_effect=lambda vid,pid: [device] if pid == 0 else []):
            self.assertEqual(dump_core.devices(), [device])
            self.assertEqual(backend.devices(), [])

    def test_evidence_handles_missing_and_different_libraries(self):
        path, expected = next(iter(dump_core.ABI_LIBRARIES.items()))
        for observed, match in [(expected,True),('other',False)]:
            result = dump_core.evidence({'identity':{},'requests':[{'remote':path,'files':[
                {'name':path.split('/')[-1],'sha256':observed}]}]})
            self.assertEqual(result['libraries'][path]['matches'],match)
            self.assertIsNone(result['soc_matches'])
            self.assertTrue(any(x['matches'] is None for x in result['libraries'].values()))

    def test_unknown_pid_diagnostic_uses_dump_reader(self):
        import diagnostics
        device = dict(product_id=0xabcd,path=b'test')
        reader = FakeProbe(sku='X1112099')
        with patch.object(backend,'SelectedProbe',side_effect=AssertionError('unexpected write-capable reader')):
            result = diagnostics.collect(device, enumerate_devices=lambda:[device], identity_reader=lambda d:reader)
        self.assertTrue(result['ok'])
        self.assertEqual(result['sku'],'X1112099')
        self.assertEqual([cmd for cmd,_ in reader.calls],[38,4627,204])
