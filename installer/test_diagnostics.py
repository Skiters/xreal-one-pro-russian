import types
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock

import backend
import diagnostics
from test_backend import FakeProbe, one_fixture


class DiagnosticsTests(unittest.TestCase):
    def test_comparison_profile_checks_libdl_read_only(self):
        import hashlib
        import profiles
        from unittest.mock import patch
        memory = b'ZONE: PHYS(0x33000000, 0x3fffffff)'
        def exported(probe, path, *args):
            return b'libdl' if path.endswith('libdl-2.25.so') else memory
        with patch.object(profiles, 'ABI_LIBRARIES', {}), patch.object(profiles, 'export_file', side_effect=exported) as read:
            with self.assertRaisesRegex(RuntimeError, 'libdl'):
                profiles.check_abi(object(), {'comparison': True, 'profile': 'one-pro-603'})
            with patch.object(profiles, 'DL_SHA', hashlib.sha256(b'libdl').hexdigest()):
                result = profiles.check_abi(object(), {'comparison': True, 'profile': 'one-pro-603'})
                self.assertIn('/lib/libdl-2.25.so', result['libraries'])
                self.assertEqual(read.call_args.args[1], '/proc/media-mem')

    def test_1s_bin_identification_uses_target_not_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'arbitrary-name.bin'
            path.write_bytes(one_fixture(backend.ONE_S_STANDARD, backend.ONE_S_TARGET))
            self.assertIs(backend.identify_package(path), backend.ONE_S_STANDARD)
            with self.assertRaises(RuntimeError):
                backend.load_package(path, backend.STANDARD)

    def test_diagnostic_preflight_failure_cannot_write(self):
        probe = FakeProbe()
        device = {'product_id': 0x0438, 'path': b'private'}
        api = types.SimpleNamespace(devices=lambda: [device], SelectedProbe=lambda _: probe,
            checked=backend.checked, identify_package=lambda *a, **k: (backend.ONE_EXPERIMENTAL, {'build_id': 'abc'}),
            load_package=lambda *a: (b'', types.SimpleNamespace(package_sha256='def')),
            identity=lambda *a: None, check_abi=Mock(side_effect=RuntimeError('ABI mismatch')))
        result = diagnostics.collect(device, 'anything.bin', backend=api)
        self.assertEqual(result['code'], 'XR-DIAGNOSTIC-ABI')
        self.assertEqual([c for c, _ in probe.calls], [38, 4627, 204])

    def test_identity_uses_only_read_commands_and_omits_serial(self):
        probe = FakeProbe()
        device = {'product_id': 0x043e, 'path': b'private-path', 'serial_number': 'SECRET'}
        api = types.SimpleNamespace(devices=lambda: [device], SelectedProbe=lambda _: probe,
                                    checked=backend.checked)
        result = diagnostics.collect(device, backend=api)
        self.assertTrue(result['ok'])
        self.assertEqual([c for c, _ in probe.calls], [38, 4627, 204])
        self.assertEqual(result['pilot'], backend.PILOT)
        self.assertNotIn('SECRET', diagnostics.render(result))
        self.assertNotIn('private-path', diagnostics.render(result))

    def test_no_device_and_selection_required(self):
        api = types.SimpleNamespace(devices=lambda: [])
        self.assertEqual(diagnostics.collect(backend=api)['code'], 'XR-DIAGNOSTIC-NO-DEVICE')
        api.devices = lambda: [{}, {}]
        self.assertEqual(diagnostics.collect(backend=api)['code'], 'XR-DIAGNOSTIC-SELECT-DEVICE')

    def test_failures_are_reports_not_writes(self):
        api = types.SimpleNamespace(devices=Mock(side_effect=OSError('USB failed')))
        result = diagnostics.collect(backend=api)
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'XR-DIAGNOSTIC-USB')
        self.assertEqual(result['exception'], 'OSError')

    def test_report_removes_personal_paths_and_unknown_fields(self):
        value = diagnostics.report('bin', 'validation', error=OSError(r'Cannot open C:\Users\secret\bad.bin'),
                                   serial='SECRET', username='SECRET', package_sha256='abcd')
        text = diagnostics.render(value)
        self.assertNotIn('secret', text)
        self.assertNotIn('SECRET', text)
        self.assertEqual(value['package_sha256'], 'abcd')

    def test_1s_model_cannot_accept_other_firmware_or_pilot2(self):
        for firmware in backend.FIRMWARES.values():
            probe = FakeProbe(sku='1S')
            probe.selected = {'product_id': 0x043e}
            if firmware is backend.ONE_S_STANDARD:
                backend.identity(probe, firmware)
            else:
                with self.assertRaises(RuntimeError):
                    backend.identity(probe, firmware)
            self.assertFalse(any(c in (4628, 4629, 4630) for c, _ in probe.calls))
        for pid, pilot in [(0x0436, backend.PILOT), (0x0438, backend.PILOT), (0x043e, backend.PILOT_2)]:
            probe = FakeProbe(pilot=pilot)
            probe.selected = {'product_id': pid}
            with self.assertRaises(RuntimeError):
                backend.identity(probe, backend.ONE_S_STANDARD)

    def test_1s_enumeration(self):
        from unittest.mock import patch
        with patch.object(backend.hid, 'enumerate', side_effect=lambda vid, pid: [
                {'product_id': pid, 'interface_number': 0, 'usage_page': 0xff00}]):
            self.assertEqual({d['product_id'] for d in backend.devices()}, {0x0436, 0x0438, 0x043e})


if __name__ == '__main__':
    unittest.main()
