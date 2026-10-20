"""Localize official XREAL 1S Pilot; preserve every non-language vendor file."""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer'))
from backend import ONE_S_TARGET, FILES, FIRMWARE_SHA, PILOT
from package_format import container_crc, read_container

STOCK_SHA = 'bc467c9c361a131ffe3dfd9b42f139ff9f675de40bd88b41b58c2d30d8d7e621'
REFERENCE_SHA = 'c6dbdcdfcda3e45574c914fd3e1d4ade425f82a993e9697a53f3b9b2f88caaf5'
XOR = bytes(i ^ 0x69 for i in range(256))


def pinned(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Unexpected input: ' + str(path))
    return data


def build(args):
    stock = pinned(args.stock, STOCK_SHA)
    reference = pinned(args.reference, REFERENCE_SHA)
    donor = pinned(args.translation, FIRMWARE_SHA)
    with zipfile.ZipFile(io.BytesIO(stock[64:].translate(XOR))) as z:
        files = {i.filename: z.read(i) for i in z.infolist()}
        attrs = {i.filename: copy.copy(i) for i in z.infolist()}
    original = dict(files)
    with zipfile.ZipFile(io.BytesIO(reference)) as z:
        if files != {i.filename: z.read(i) for i in z.infolist()}:
            raise ValueError('1S stock differs from the original font-patch reference')
    with zipfile.ZipFile(io.BytesIO(donor[64:].translate(XOR))) as z:
        for name in FILES:
            if name != 'pilot/checksums.sha256':
                files[name] = z.read(name)
    files['pilot/xreal-target.json'] = (json.dumps(ONE_S_TARGET, sort_keys=True)+'\n').encode()
    files['pilot/checksums.sha256'] = ''.join(
        hashlib.sha256(data).hexdigest()+'  ./'+name[6:]+'\n' for name, data in sorted(files.items())
        if not name.endswith('/') and name != 'pilot/checksums.sha256').encode()
    changed = [n for n in original if original[n] != files[n]]
    if not set(changed) <= set(FILES):
        raise ValueError('Non-language file changed')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(files.items()):
            info = copy.copy(attrs.get(name, attrs['pilot/pilot_version.txt']))
            info.filename = name
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.extra = b''
            z.writestr(info, data)
    packed = bytearray(stock[:64] + stream.getvalue().translate(XOR))
    packed[4:8] = (len(packed)-8).to_bytes(4, 'little')
    packed[:4] = container_crc(packed).to_bytes(4, 'little')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(packed)
    _, verified, target = read_container(args.output, PILOT, 0x69)
    assert target == ONE_S_TARGET
    report = {'sha256': hashlib.sha256(packed).hexdigest(), 'bytes': len(packed),
              'readback_files': len(verified), 'stock_sha256': STOCK_SHA,
              'translation_sha256': FIRMWARE_SHA, 'changed_vendor_files': changed,
              'added': sorted(set(files)-set(original)), 'physical_test': 'pending'}
    args.output.with_suffix('.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock', type=Path, default=ROOT/'work/models-release/pilot-9.bin')
    p.add_argument('--reference', type=Path, default=ROOT/'work/update-analysis/pilot-original.zip')
    p.add_argument('--translation', type=Path, default=ROOT/'outputs/xreal-russian-firmware/XREAL_One_Pro_Pilot_RU.bin')
    p.add_argument('--output', type=Path, default=ROOT/'outputs/v0.7.0/XREAL-1S-RU-Pilot1.9.1.bin')
    build(p.parse_args())
