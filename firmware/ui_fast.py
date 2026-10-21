"""Opt-in One Pro UI experiment; exact rc7 OSD, no panel/EDID changes."""
import hashlib
import json
import re
import struct

OSD_SHA = '0067f65b58d6f708d75163ce89224162f0d62c5ee054579eb65b46b9dc77978e'
# Refresh and animation timers only. Input polling remains 30 ms.
TIMERS = (0x27d35c, 0x27e4dc)
# w2 is lv_scr_load_anim's duration. Delay, type and auto-delete are unchanged.
# Its prologue saves w2 into w25 and stores it at animation.time (+0x4c).
TRANSITIONS = {0x2b3f0: 22, 0xa46cc: 20, 0xa47b4: 28}


def offset(data, va):
    if data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
        raise ValueError('Expected little-endian AArch64 ELF64')
    start = struct.unpack_from('<Q', data, 32)[0]
    size, count = struct.unpack_from('<HH', data, 54)
    for i in range(count):
        kind, _, off, addr, _, length, _, _ = struct.unpack_from('<II6Q', data, start+i*size)
        if kind == 1 and addr <= va and va+4 <= addr+length:
            return off+va-addr
    raise ValueError('Instruction outside file-backed LOAD segment')


def patch_osd(data):
    if hashlib.sha256(data).hexdigest() != OSD_SHA:
        raise ValueError('UI experiment requires the exact rc7 OSD')
    result = bytearray(data)
    patches = {va: (0x528003c1, 0x52800201) for va in TIMERS}
    for va, reg in TRANSITIONS.items():
        # MOV w2,wN -> LSR w2,wN,#1. Zero duration stays zero.
        patches[va] = (0x2a0003e2 | reg << 16, 0x53017c02 | reg << 5)
    for va, (before, after) in patches.items():
        at = offset(data, va)
        if struct.unpack_from('<I', data, at)[0] != before:
            raise ValueError('Unexpected instruction at '+hex(va))
        struct.pack_into('<I', result, at, after)
    return bytes(result)


def apply(files, model):
    if model != 'one-pro':
        raise ValueError('UI experiment has only One Pro runtime evidence')
    osd = 'pilot/bin/libnr_osd.so'
    files[osd] = patch_osd(files[osd])
    path = 'pilot/conf/pilot_default_config.json'
    cfg = json.loads(re.sub(r'//[^\n]*', '', files[path].decode('utf-8')))
    settings = cfg['osd']
    expected = {'enable_action_log': 1, 'enable_interface_log': 1,
                'boot_wait_time1': 1.0, 'boot_wait_time2': 7}
    if any(settings.get(k) != v for k, v in expected.items()):
        raise ValueError('Unexpected baseline OSD configuration')
    settings.update(enable_action_log=0, enable_interface_log=0, boot_wait_time1=0.25)
    files[path] = (json.dumps(cfg, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode()
    removed = {}
    for name in list(files):
        if name in ('pilot/bin/libnr_osd_asus.so', 'pilot/bin/libnr_osd_asus_20th.so') or name.startswith('pilot/resource_asus/'):
            removed[name] = {'bytes': len(files[name]), 'sha256': hashlib.sha256(files[name]).hexdigest()}
            del files[name]
    if not all(name in removed for name in ('pilot/bin/libnr_osd_asus.so', 'pilot/bin/libnr_osd_asus_20th.so')):
        raise ValueError('Missing expected ASUS modules')
    return {'status': 'local candidate; physical performance unmeasured',
            'osd_base_sha256': OSD_SHA, 'refresh_timer_ms': 16, 'animation_timer_ms': 16,
            'screen_transition_duration_ratio': 0.5, 'layout_cache': True,
            'hdr_timing_log_interval_frames': 1800, 'action_interface_logs': False,
            'boot_first_wait_seconds': 0.25, 'video_ready_timeout_seconds': 7,
            'removed': removed,
            'scope': 'One Pro; ASUS libraries absent from running Pilot maps; OTA may retain old files'}
