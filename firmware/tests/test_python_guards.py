"""Host-only regression tests for firmware Python helpers; no device, USB or BIN inputs.

Covers JSON // comment stripping (URLs, escapes, line numbers), explicit guard
exceptions that survive ``python -O``, the 90 Hz/60 Hz stereo display guard in
build.py and separate HDR/Real3D streams in measure.py.

Run: python firmware/tests/test_python_guards.py
(the module re-runs itself under ``python -O``).
"""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT/'work/elfdeps', ROOT/'installer', ROOT/'firmware'):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import build  # noqa: E402
import measure  # noqa: E402
import ui_fast  # noqa: E402

COMMENTED = r'''{
  "url": "https://example.invalid/a//b", // trailing comment
  "esc": "say \"//not a comment\"",
  "backslash": "C:\\path\\", // comment after escaped backslash
  // full-line comment with "quotes" and http://example.invalid
  "n": 1
}
'''
EXPECTED = {'url': 'https://example.invalid/a//b', 'esc': 'say "//not a comment"',
            'backslash': 'C:\\path\\', 'n': 1}


class JsonComments(unittest.TestCase):
    def test_build_config_keeps_strings(self):
        self.assertEqual(build.config(COMMENTED.encode()), EXPECTED)

    def test_build_config_preserves_line_numbers(self):
        with self.assertRaises(json.JSONDecodeError) as caught:
            build.config(b'{\n  "u": "http://x//y", // c\n  // c2\n  "a": \n}')
        self.assertEqual(caught.exception.lineno, 5)

    def test_ui_fast_apply_keeps_strings(self):
        path = 'pilot/conf/pilot_default_config.json'
        text = ('{\n  "osd": {"enable_action_log": 1, "enable_interface_log": 1, // c\n'
                '          "boot_wait_time1": 1.0, "boot_wait_time2": 7},\n'
                '  "update_url": "http://updates.invalid//pilot", // c\n'
                '  "esc": "a\\"//b"\n}\n')
        files = {'pilot/bin/libnr_osd.so': b'osd', path: text.encode(),
                 'pilot/bin/libnr_osd_asus.so': b'a', 'pilot/bin/libnr_osd_asus_20th.so': b'b'}
        with mock.patch.object(ui_fast, 'patch_osd', side_effect=lambda data: data+b'!'):
            ui_fast.apply(files, 'one-pro')
        cfg = json.loads(files[path])
        self.assertEqual(cfg['update_url'], 'http://updates.invalid//pilot')
        self.assertEqual(cfg['esc'], 'a"//b')
        self.assertEqual(cfg['osd'], {'enable_action_log': 0, 'enable_interface_log': 0,
                                      'boot_wait_time1': 0.25, 'boot_wait_time2': 7})


def general_config():
    # Values asserted for the same base BIN (BASE_SHA) in work/build_one_features.py.
    return {'state_machine': {'space_screen': {'frame_rate_boost_enable': False,
                                               'over_clocking_mode': 0}},
            'display': {'dp': {'synth_stereo': '1920_1080_60', 'mono': '1920_1080_90_DEFAULT'},
                        'oled': {'default_resolution': '1920_1080_90'}}}


class DisplayDefaults(unittest.TestCase):
    def test_accepts_90hz_default_and_60hz_stereo(self):
        general = general_config()
        build.check_display_defaults(general)
        self.assertEqual(general, general_config(), 'Guard must not change configuration')

    def test_rejects_each_changed_value(self):
        changes = [
            (('state_machine', 'space_screen', 'frame_rate_boost_enable'), True),
            (('state_machine', 'space_screen', 'frame_rate_boost_enable'), 0),
            (('state_machine', 'space_screen', 'over_clocking_mode'), 1),
            (('display', 'dp', 'synth_stereo'), '1920_1080_90'),
            (('display', 'dp', 'mono'), '1920_1080_60_DEFAULT'),
            (('display', 'dp', 'mono'), '1920_1080_133_DEFAULT'),
            (('display', 'oled', 'default_resolution'), '1920_1080_60'),
            (('display', 'oled', 'default_resolution'), '1920_1080_240'),
        ]
        for keys, value in changes:
            general = general_config()
            target = general
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            with self.subTest(keys=keys, value=value), self.assertRaises(ValueError):
                build.check_display_defaults(general)


class Build1sTarget(unittest.TestCase):
    def test_wrong_readback_target_raises(self):
        xor = bytes(i ^ 0x69 for i in range(256))
        target = {'schema': 1, 'model': '1s', 'usb_pid': 0x043e, 'soc': 'test', 'experimental': False}
        fake_backend = types.SimpleNamespace(
            ONE_S_TARGET=target, FIRMWARE_SHA='0'*64, PILOT='1.9.1.test',
            FILES=['pilot/resource/osd_language.csv', 'pilot/checksums.sha256'])

        def archive(entries):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as z:
                for name, data in entries.items():
                    z.writestr(name, data)
            return stream.getvalue()

        original = {'pilot/pilot_version.txt': b'1.9.1', 'pilot/resource/osd_language.csv': b'en',
                    'pilot/checksums.sha256': b''}
        inputs = {'stock': bytes(64)+archive(original).translate(xor), 'reference': archive(original),
                  'translation': bytes(64)+archive({'pilot/resource/osd_language.csv': b'ru'}).translate(xor)}
        previous = sys.modules.get('build_1s')
        def restore_module():
            if previous is None:
                sys.modules.pop('build_1s', None)
            else:
                sys.modules['build_1s'] = previous
        self.addCleanup(restore_module)
        with mock.patch.dict(sys.modules, {'backend': fake_backend}):
            sys.modules.pop('build_1s', None)
            import build_1s
        with tempfile.TemporaryDirectory() as tmp:
            args = types.SimpleNamespace(stock='stock', reference='reference', translation='translation',
                                         output=Path(tmp)/'out.bin')
            with mock.patch.object(build_1s, 'pinned', side_effect=lambda path, _: inputs[path]), \
                 mock.patch('sys.stdout', io.StringIO()):
                with mock.patch.object(build_1s, 'read_container', return_value=(b'', {}, target)):
                    build_1s.build(args)
                wrong = dict(target, model='one')
                with mock.patch.object(build_1s, 'read_container', return_value=(b'', {}, wrong)):
                    with self.assertRaises(ValueError):
                        build_1s.build(args)


class CompareUiGenerator(unittest.TestCase):
    BEGIN, END = 0x53c94, 0x540ec

    def generate(self, instructions):
        # The guard tests use synthetic instructions, not real ELF disassembly.
        # Stub imports so the checks also run without the native capstone DLL.
        capstone = types.ModuleType('capstone')
        capstone.Cs = mock.Mock()
        capstone.CS_ARCH_ARM64 = 0
        capstone.CS_MODE_LITTLE_ENDIAN = 0
        elf_module = types.ModuleType('elftools.elf.elffile')
        elf_module.ELFFile = mock.Mock()
        with mock.patch.dict(sys.modules, {'capstone': capstone,
                                           'elftools.elf.elffile': elf_module}):
            import generate_compare_ui
        size = self.END-self.BEGIN
        section = mock.MagicMock()
        section.data.return_value = bytes(size)
        section.__getitem__.return_value = self.BEGIN
        elf = mock.Mock()
        elf.get_section_by_name.return_value = section
        disassembler = mock.Mock()
        disassembler.disasm.return_value = instructions(size//4)
        with mock.patch.object(generate_compare_ui, 'checked'), \
             mock.patch.object(generate_compare_ui, 'ELFFile', return_value=elf), \
             mock.patch.object(generate_compare_ui, 'Cs', return_value=disassembler):
            return generate_compare_ui.generate(b'')

    @staticmethod
    def ins(mnemonic):
        return types.SimpleNamespace(mnemonic=mnemonic, op_str='x0, x1', bytes=b'\xe0\x03\x01\xaa')

    def test_accepts_allowed_straight_line_slice(self):
        self.assertIn('xreal_clone_row:', self.generate(lambda n: [self.ins('mov')]*n))

    def test_rejects_partial_disassembly(self):
        with self.assertRaises(ValueError):
            self.generate(lambda n: [self.ins('mov')]*(n-1))

    def test_rejects_unexpected_instruction(self):
        with self.assertRaises(ValueError):
            self.generate(lambda n: [self.ins('mov')]*(n-1)+[self.ins('b')])


def r3d(frame, mapping, compute, flush, stamp):
    return (f'Real3D CPU frame rc=0 frame={frame} 1920x1080 strides=2048/1024 -> 1920/1024 '
            f'depth_width=480 fractional_bits=2 src=1,2,3 dst=4,5,6 map=7 result=8 '
            f'depth_range=0..9 shifted=1 time_us={mapping}/{compute}/{flush} stamp_us={stamp} workers=4')


def hdr(frame, stamp):
    return f'CPU diagnostic: frame timestamp microseconds phys=0x0 size={frame} value={stamp}'


class MeasureStreams(unittest.TestCase):
    def test_mixed_log_keeps_streams_separate(self):
        log = '\n'.join([
            'CPU v7: LUT initialized rc=0 frames=0',
            'Real3D CPU init rc=0',
            hdr(0, 1000000), r3d(10, 1000, 20000, 500, 5000000),
            hdr(30, 1500000), r3d(40, 1200, 22000, 700, 6000000),
            'CPU v7: LUT initialized rc=0 frames=0',  # HDR re-init must not split Real3D
            hdr(0, 9000000), r3d(70, 1100, 21000, 600, 7000000),
            hdr(30, 10000000)])
        result = measure.summarize(log)
        streams = {}
        for session in result['sessions']:
            streams.setdefault(session['stream'], []).append(session)
        self.assertEqual(sorted(streams), ['hdr', 'real3d'])
        self.assertEqual([s['interval_callback_rates'] for s in streams['hdr']], [[60.0], [30.0]])
        self.assertTrue(all('compute_us' not in s for s in streams['hdr']))
        [real3d] = streams['real3d']
        self.assertEqual(real3d['samples'], 3)
        self.assertEqual(real3d['interval_callback_rates'], [30.0, 30.0])
        self.assertEqual(real3d['compute_us'], {'median': 21000, 'max': 22000})
        self.assertEqual(real3d['map_us'], {'median': 1100, 'max': 1200})
        self.assertIs(result['stable_60_fps_confirmed'], False)


class OptimizedInterpreter(unittest.TestCase):
    def test_guards_hold_under_python_O(self):
        if sys.flags.optimize:
            self.skipTest('already running under -O')
        run = subprocess.run([sys.executable, '-O', str(Path(__file__).resolve())],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
