"""Build an unverified One-only Pilot 2 experiment from the published CPU port.

Requires the locally retained vendor Pilot 2 ZIP and v0.5.0 Real3D BIN.
Does not access a device, change system partitions, or enable overclocking.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/xreal-one-real3d-hdr-test'
SOURCE = ROOT / 'outputs/xreal-pilot2-real3d-cpu/XREAL-Pilot2-RU-Hebe-Real3D-CPU.bin'
SOURCE_SHA = '1b72fc6acd90fc9b8995644cca659907d571292f19a19a33cdbddd59da5decd0'
TARGET = {'schema': 1, 'model': 'one', 'usb_pid': 0x0438,
          'soc': '15.1.03.603_20260914', 'experimental': True}


def config(data):
    return json.loads(re.sub(r'//[^\n]*', '', data.decode('utf-8')))


def crc(data):
    table = []
    for index in range(256):
        value = index << 24
        for _ in range(8):
            value = ((value << 1) ^ (0xf4acfb13 if value & 0x80000000 else 0)) & 0xffffffff
        table.append(value)
    value = 0
    for byte in memoryview(data)[8:]:
        value = ((value << 8) ^ table[(value >> 24) ^ byte]) & 0xffffffff
    return value


def main():
    source = SOURCE.read_bytes()
    assert hashlib.sha256(source).hexdigest() == SOURCE_SHA
    assert source[16:36] == b'2.0.0.20260920102250'
    with zipfile.ZipFile(io.BytesIO(source[64:].translate(bytes(i ^ 0x66 for i in range(256))))) as base:
        files = {name: base.read(name) for name in base.namelist()}
        original = dict(files)
        infos = {info.filename: copy.copy(info) for info in base.infolist()}

    with zipfile.ZipFile(ROOT / 'work/pro-plus-ota/pilot-pro-plus.zip') as stock:
        # Remove the Pro-only Hebe brightness transplant; preserve vendor model
        # selection rather than disguising a One as a One Pro / Pro+.
        for name in files:
            if name.startswith('pilot/conf/pilot_default_config_') and name.endswith('.json'):
                files[name] = stock.read(name)
        gf_name = 'pilot/conf/pilot_default_config_gf.json'
        gf = config(stock.read(gf_name))

    settings = gf['display']['brightness']['setting']
    for row in settings:
        if row['hdr_type'] == 'ai hdr':
            sdr = next(item for item in settings if item['hdr_type'] == 'sdr' and item['mode'] == row['mode'])
            for key in ('luminance_data', 'duty_data', 'calculate_luminance_data', 'base_color_temperature_data'):
                row[key] = copy.deepcopy(sdr[key])
    files[gf_name] = (json.dumps(gf, ensure_ascii=False, indent=2) + '\n').encode()

    general = config(files['pilot/conf/pilot_default_config.json'])
    assert general['state_machine']['space_screen']['frame_rate_boost_enable'] is False
    assert general['state_machine']['space_screen']['over_clocking_mode'] == 0
    assert general['display']['dp']['mono'] == '1920_1080_90_DEFAULT'
    assert general['display']['oled']['default_resolution'] == '1920_1080_90'
    assert general['display']['dp']['synth_stereo'] == '1920_1080_60'
    marker = 'pilot/xreal-target.json'
    files[marker] = (json.dumps(TARGET, indent=2) + '\n').encode()
    infos[marker] = copy.copy(infos['pilot/pilot_version.txt'])
    infos[marker].filename = marker
    files['pilot/checksums.sha256'] = ''.join(
        hashlib.sha256(data).hexdigest() + '  ./' + name[6:] + '\n'
        for name, data in files.items()
        if not name.endswith('/') and name != 'pilot/checksums.sha256'
    ).encode()

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, data in files.items():
            archive.writestr(infos[name], data)
    archive_data = stream.getvalue()
    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        assert archive.testzip() is None
        assert all(archive.read(n) == data for n, data in files.items())
        assert archive.getinfo('pilot/bin/pilot').external_attr >> 16 & 0o111
    packed = bytearray(source[:64] + archive_data.translate(bytes(i ^ 0x66 for i in range(256))))
    packed[4:8] = (len(packed) - 8).to_bytes(4, 'little')
    packed[:4] = crc(packed).to_bytes(4, 'little')
    assert packed[8:64] == source[8:64]
    OUT.mkdir(parents=True, exist_ok=True)
    binary = OUT / 'XREAL-One-Pilot2-RU-Real3D-AIHDR-TEST.bin'
    binary.write_bytes(packed)
    report = dict(TARGET, source_sha256=SOURCE_SHA,
                  firmware_sha256=hashlib.sha256(packed).hexdigest(), bytes=len(packed),
                  changed_files=[n for n in files if original.get(n) != files[n]],
                  physical_tested_on_one=False, overclocking_enabled=False,
                  gf_ai_hdr_panel_drive_equals_sdr=True,
                  vendor_pilot_elf_unchanged_from_published_port=True)
    (OUT / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
