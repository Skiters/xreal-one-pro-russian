import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

import backend as b


def archive(name, content):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        z.writestr(name, content)
    return stream.getvalue()


class FakeProbe:
    def __init__(self, fail=None, incompatible=False, corrupt=False, sku='X1112010', soc=None,
                 pilot=None, target_pilot=None):
        self.calls = []
        self.fail = fail
        self.incompatible = incompatible
        self.corrupt = corrupt
        self.sku = sku
        self.soc = b.SOC if soc is None else soc
        self.pilot = b.PILOT if pilot is None else pilot
        self.target_pilot = target_pilot

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def query(self, command, payload=b'', timeout=16):
        self.calls.append((command, payload))
        if command == 4630 and self.target_pilot and self.fail != command:
            self.pilot = self.target_pilot
        versions = {38: self.soc, 4627: self.pilot, 204: 'other' if self.incompatible else self.sku}
        return {'status': 1 if self.fail == command else 0,
                'payload': versions.get(command, '').encode()}

    def export(self, name, max_bytes):
        return {'start_status': 0, 'finish_status': 0,
                'data': archive(name, b'bad' if self.corrupt else b'expected')}


class BackendTests(unittest.TestCase):
    def test_second_one_pro_sku_uses_same_update_flow(self):
        probe = FakeProbe(sku='X1112011')
        _, events = self.run_update(probe)
        self.assertEqual(events[-1][1], 100)
        self.assertEqual(sum(c == 4628 for c, _ in probe.calls), 1)

    def test_allowlist_does_not_accept_other_skus_or_versions(self):
        for args in ({'sku':'X1112012'}, {'sku':'X1112011','soc':'wrong'},
                     {'sku':'X1112011','pilot':'wrong'}, {'sku':'X1112010','pilot':'wrong'}):
            with self.subTest(args=args):
                probe = FakeProbe(**args)
                with self.assertRaises(RuntimeError): self.run_update(probe)
                self.assertFalse(any(c in (4628,4629,4630) for c,_ in probe.calls))

    def run_update(self, probe, firmware=b.STANDARD):
        data = bytes(range(256))*5
        expected = {name: hashlib.sha256(b'expected').hexdigest() for name in firmware.files}
        events = []
        b.update({'path': b'chosen'}, lambda *args: events.append(args),
                 probe_factory=lambda selected: probe, package_loader=lambda: (data, expected),
                 sleep=lambda _: None, firmware=firmware)
        return data, events

    def test_experimental_only_allows_verified_sku(self):
        probe = FakeProbe(sku='X1112010', target_pilot=b.PILOT_2)
        self.run_update(probe, b.EXPERIMENTAL)
        self.assertEqual(probe.pilot, b.PILOT_2)
        for sku in ('X1112011', 'X1112012'):
            probe = FakeProbe(sku=sku, target_pilot=b.PILOT_2)
            with self.assertRaises(RuntimeError):
                self.run_update(probe, b.EXPERIMENTAL)
            self.assertFalse(any(cmd in (4628, 4629, 4630) for cmd, _ in probe.calls))

    def test_standard_can_replace_experimental(self):
        probe = FakeProbe(pilot=b.PILOT_2, target_pilot=b.PILOT)
        self.run_update(probe, b.STANDARD)
        self.assertEqual(probe.pilot, b.PILOT)

    def test_unknown_pilot2_second_sku_is_rejected(self):
        probe = FakeProbe(sku='X1112011', pilot=b.PILOT_2, target_pilot=b.PILOT)
        with self.assertRaises(RuntimeError):
            self.run_update(probe, b.STANDARD)
        self.assertFalse(any(cmd in (4628, 4629, 4630) for cmd, _ in probe.calls))

    def test_all_embedded_firmwares_validate(self):
        for firmware in b.FIRMWARES.values():
            with self.subTest(firmware=firmware.key):
                data, expected = b.load_package(firmware=firmware)
                self.assertGreater(len(data), 80_000_000)
                self.assertEqual(set(expected), set(firmware.files))

    def test_full_flow_chunks_finish_readback(self):
        probe = FakeProbe()
        data, events = self.run_update(probe)
        writes = [(cmd, payload) for cmd, payload in probe.calls if cmd in (4628, 4629, 4630)]
        self.assertEqual(writes[0], (4628, data[:64]))
        self.assertEqual(b''.join(p for c, p in writes if c == 4629), data[64:])
        self.assertTrue(all(len(p) <= 490 for c, p in writes if c == 4629))
        self.assertEqual(writes[-1], (4630, b''))
        self.assertEqual(events[-1][1], 100)

    def test_wrong_device_never_writes(self):
        probe = FakeProbe(incompatible=True)
        with self.assertRaises(RuntimeError):
            self.run_update(probe)
        self.assertFalse(any(c in (4628, 4629, 4630) for c, _ in probe.calls))

    def test_rejected_chunk_never_retries_or_finishes(self):
        probe = FakeProbe(fail=4629)
        with self.assertRaises(RuntimeError):
            self.run_update(probe)
        self.assertEqual(sum(c == 4629 for c, _ in probe.calls), 1)
        self.assertFalse(any(c == 4630 for c, _ in probe.calls))

    def test_rejected_finish_is_failure(self):
        with self.assertRaises(RuntimeError):
            self.run_update(FakeProbe(fail=4630))

    def test_readback_mismatch_is_failure(self):
        with self.assertRaises(RuntimeError):
            self.run_update(FakeProbe(corrupt=True))

    def test_readback_disconnect_reopens_without_reflashing(self):
        probe = FakeProbe()
        original = probe.export
        attempts = []
        def export(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError('device restarting')
            return original(*args, **kwargs)
        probe.export = export
        _, events = self.run_update(probe)
        self.assertEqual(sum(c == 4628 for c, _ in probe.calls), 1)
        self.assertEqual(sum(c == 4630 for c, _ in probe.calls), 1)
        self.assertEqual(len(attempts), 6)
        self.assertEqual(events[-1][1], 100)

    def test_persistent_readback_disconnect_is_bounded(self):
        from unittest.mock import Mock
        probe = FakeProbe()
        probe.export = Mock(side_effect=OSError('disconnected'))
        with self.assertRaisesRegex(RuntimeError, 'Проверить без записи'):
            self.run_update(probe)
        self.assertEqual(probe.export.call_count, 3)
        self.assertEqual(sum(c == 4628 for c, _ in probe.calls), 1)

    def test_export_preserves_original_failure(self):
        probe = b.Probe()
        def query(command, *args, **kwargs):
            if command == 207:
                return {'status': 0, 'payload': b''}
            raise OSError('read failed' if command == 208 else 'cleanup failed')
        probe.query = query
        with self.assertRaisesRegex(OSError, 'read failed'):
            probe.export('/test')

    def test_corrupt_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'bad.bin'
            path.write_bytes(b'bad')
            with self.assertRaises(RuntimeError):
                b.load_package(path)

    def test_selected_path_never_falls_back(self):
        with patch.object(b, 'devices', return_value=[{'path': b'other'}]), patch.object(b.hid, 'device') as device:
            with self.assertRaises(RuntimeError):
                with b.SelectedProbe({'path': b'chosen'}):
                    pass
            device.assert_not_called()

    def test_changed_serial_is_rejected(self):
        with patch.object(b, 'devices', return_value=[{'path': b'chosen', 'serial_number': 'B'}]):
            with self.assertRaises(RuntimeError):
                with b.SelectedProbe({'path': b'chosen', 'serial_number': 'A'}):
                    pass

    def test_actual_transport_frame_crc_and_report_id(self):
        class Device:
            def write(self, data):
                self.written = data
                return len(data)

            def read(self, size, timeout):
                response = bytearray(512)
                response[0] = 0xfd
                struct.pack_into('<H', response, 5, 18)
                struct.pack_into('<H', response, 15, 4629)
                struct.pack_into('<I', response, 1, zlib.crc32(response[5:23]))
                return b'\0'+response
        probe = b.Probe()
        probe.device = Device()
        self.assertEqual(probe.query(4629, b'chunk')['status'], 0)
        frame = probe.device.written[1:]
        self.assertEqual(len(probe.device.written), 513)
        self.assertEqual(frame[22:27], b'chunk')
        self.assertEqual(struct.unpack_from('<I', frame, 1)[0], zlib.crc32(frame[5:27]))

    def test_partial_hid_write_is_rejected(self):
        from unittest.mock import Mock
        probe = b.Probe()
        probe.device = Mock()
        probe.device.write.return_value = 100
        with self.assertRaises(IOError):
            probe.query(4629, b'chunk')
        probe.device.read.assert_not_called()

    def test_windows_padded_write_requires_valid_reply(self):
        from unittest.mock import Mock
        probe = b.Probe()
        probe.device = Mock()
        probe.device.write.return_value = 1025
        response = bytearray(512)
        response[0] = 0xfd
        struct.pack_into('<H', response, 5, 18)
        struct.pack_into('<H', response, 15, 38)
        struct.pack_into('<I', response, 1, zlib.crc32(response[5:23]))
        probe.device.read.return_value = response
        self.assertEqual(probe.query(38)['status'], 0)
        response[1] ^= 1
        with self.assertRaisesRegex(ValueError, 'CRC32'):
            probe.query(38)

    def test_ui_requires_selection_and_explicit_click(self):
        import tkinter as tk
        from app import App
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(b, 'devices', return_value=[{'path': b'chosen'}]), patch.object(b, 'update') as update:
                app = App(root)
                app.refresh()
                self.assertEqual(str(app.install_button['state']), 'disabled')
                app.combo.current(0)
                app.selected()
                self.assertEqual(str(app.install_button['state']), 'normal')
                self.assertEqual(app.firmware_key.get(), 'standard')
                app.firmware_key.set('experimental')
                app.firmware_changed()
                self.assertIn('AI HDR', app.description['text'])
                self.assertEqual(str(app.install_button['state']), 'normal')
                update.assert_not_called()
                app.busy = True
                with patch('app.messagebox.showinfo') as notice, patch.object(root, 'destroy') as destroy:
                    app.close()
                    notice.assert_called_once()
                    destroy.assert_not_called()
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
