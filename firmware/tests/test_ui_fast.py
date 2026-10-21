"""Execute patched timer/duration instructions and check package scope."""
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'firmware'), str(ROOT/'installer'), str(ROOT/'work/elfdeps')]
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_W0, UC_ARM64_REG_W1, UC_ARM64_REG_W2
from ui_fast import patch_osd, offset, TIMERS, TRANSITIONS, apply
from package_format import read_container
from profiles import validate_build
from build import config


def test(path):
    baseline = (ROOT/'outputs/ui-performance-audit/libnr_osd.so').read_bytes()
    patched = patch_osd(baseline)
    allowed = {offset(baseline, va)+i for va in (*TIMERS, *TRANSITIONS) for i in range(4)}
    assert len(patched) == len(baseline)
    assert all(a == b or i in allowed for i, (a, b) in enumerate(zip(baseline, patched)))
    u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    u.mem_map(0x1000, 0x1000)
    for va in TIMERS:
        at = offset(patched, va)
        u.mem_write(0x1000, patched[at:at+4]); u.ctl_remove_cache(0x1000, 0x1004)
        u.reg_write(UC_ARM64_REG_W1, 99)
        u.emu_start(0x1000, 0x1004)
        assert u.reg_read(UC_ARM64_REG_W1) == 16
    for va, register in TRANSITIONS.items():
        at = offset(patched, va)
        u.mem_write(0x1000, patched[at:at+4]); u.ctl_remove_cache(0x1000, 0x1004)
        for duration in (0, 1, 80, 150, 200, 300, 1000, 0xffffffff):
            u.reg_write(UC_ARM64_REG_W0+register, duration)
            u.emu_start(0x1000, 0x1004)
            assert u.reg_read(UC_ARM64_REG_W2) == duration//2
    try:
        patch_osd(patched)
    except ValueError:
        pass
    else:
        raise AssertionError('Reapplying to an unknown ELF must fail')
    try:
        apply({}, 'one')
    except ValueError:
        pass
    else:
        raise AssertionError('Untested One profile must fail')
    packed, expected, _ = read_container(Path(path), '2.0.0.20260920102250', 0x66)
    with zipfile.ZipFile(io.BytesIO(packed[64:].translate(bytes(i ^ 0x66 for i in range(256))))) as z:
        files = {n:z.read(n) for n in z.namelist()}
    assert files['pilot/bin/libnr_osd.so'] == patched
    assert not any('libnr_osd_asus' in n or n.startswith('pilot/resource_asus/') for n in files)
    before = config((ROOT/'outputs/ui-fast-test/device-before/usrdata_pilot_conf_pilot_default_config.json').read_bytes())
    after = config(files['pilot/conf/pilot_default_config.json'])
    before['osd'].update(enable_action_log=0, enable_interface_log=0, boot_wait_time1=0.25)
    assert before == after, 'Only the three approved config fields may change'
    manifest = json.loads(files['pilot/xreal-build.json'])
    validate_build(manifest, 'experimental', '2.0.0.20260920102250')
    assert '-DXREAL_UI_FAST=1' in manifest['flags']
    assert manifest['ui_fast']['video_ready_timeout_seconds'] == 7
    print('UI fast: ARM64 instructions, exact patch boundary, baseline/hash rejection, model guard, config scope and package checksums passed:',len(expected),'files')


if __name__ == '__main__':
    test(sys.argv[1])
