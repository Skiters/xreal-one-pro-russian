"""Reproducible CPU adapter and model-specific Pilot packages; never uses USB."""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer'))
from package_format import container_crc, read_container
from profiles import ABI_LIBRARIES, MMZ_START, MMZ_END, SOC
from version import VERSION

PILOT = '2.0.0.20260920102250'
BASE_SHA = '1b72fc6acd90fc9b8995644cca659907d571292f19a19a33cdbddd59da5decd0'
STOCK_SHA = 'c052575b989c9811c07563bf61ff3360c32a275bc3bb4dd426e211e94525759b'
SOURCES = ['firmware/cpu_adapter.c', 'firmware/lut_fast.c', 'firmware/runtime.c',
           'firmware/runtime.h', 'work/3d-lab/real3d_cpu.c', 'firmware/build.py',
           'installer/profiles.py', 'installer/package_format.py', 'installer/version.py']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked(path, expected):
    data = Path(path).read_bytes()
    if sha(data) != expected:
        raise ValueError('Input hash mismatch: ' + str(path))
    return data


def config(data):
    return json.loads(re.sub(r'//[^\n]*', '', data.decode('utf-8')))


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode()


def build(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    base = checked(args.base, BASE_SHA)
    stock = checked(args.stock, STOCK_SHA)
    compiler = subprocess.check_output([str(args.clang), '--version'], text=True).splitlines()[0]
    if not re.search(r'\b22\.1\.3\b', compiler):
        raise ValueError('Use the pinned Clang 22.1.3 toolchain.')
    link_names = {'libhal_sys.so': '/usr/lib/libhal_sys.so',
                  'libpthread.so.0': '/lib/libpthread-2.25.so',
                  'libc.so.6': '/lib/libc-2.25.so'}
    for name, target in link_names.items():
        checked(args.sysroot / name, ABI_LIBRARIES[target])
    source_hashes = {s: sha((ROOT/s).read_bytes()) for s in SOURCES}
    library = out / 'libxreal_cpu_adapter.so'
    flags = ['--target=aarch64-linux-gnu', '-fPIC', '-O2', '-shared', '-nostdlib',
             '-fuse-ld=lld', '-Wall', '-Wextra', '-Werror', '-Wl,--no-undefined', '-Wl,-soname,libxreal_cpu_adapter.so',
             '-I', 'firmware']
    subprocess.run([str(args.clang), *flags, *[s for s in SOURCES if s.endswith('.c')],
                    '-L', str(args.sysroot.resolve()),
                    *['-l:'+n for n in link_names], '-o', str(library)],
                   cwd=ROOT, check=True)
    if source_hashes != {s: sha((ROOT/s).read_bytes()) for s in SOURCES}:
        raise RuntimeError('Sources changed during compilation; rebuild from a fixed revision.')
    provenance = {
        'release': VERSION, 'compiler': compiler, 'flags': flags,
        'sources': source_hashes, 'python': sys.version.split()[0],
        'zlib': zlib.ZLIB_RUNTIME_VERSION,
        'base_sha256': BASE_SHA, 'stock_sha256': STOCK_SHA,
        'adapter_sha256': sha(library.read_bytes()),
    }
    with zipfile.ZipFile(io.BytesIO(base[64:].translate(bytes(i ^ 0x66 for i in range(256))))) as z:
        contents = {i.filename: z.read(i) for i in z.infolist()}
        attrs = {i.filename: copy.copy(i) for i in z.infolist()}
    results = {}
    for model in ('one-pro', 'one'):
        files = dict(contents)
        files['pilot/bin/libxreal_cpu_adapter.so'] = library.read_bytes()
        if model == 'one':
            with zipfile.ZipFile(io.BytesIO(stock)) as z:
                for name in files:
                    if name.startswith('pilot/conf/pilot_default_config_') and name.endswith('.json'):
                        files[name] = z.read(name)
            gf_name = 'pilot/conf/pilot_default_config_gf.json'
            gf = config(files[gf_name])
            for row in gf['display']['brightness']['setting']:
                if row['hdr_type'] == 'ai hdr':
                    sdr = next(r for r in gf['display']['brightness']['setting']
                               if r['hdr_type'] == 'sdr' and r['mode'] == row['mode'])
                    for key in ('luminance_data', 'duty_data', 'calculate_luminance_data',
                                'base_color_temperature_data'):
                        row[key] = copy.deepcopy(sdr[key])
            files[gf_name] = encode(gf)
            files['pilot/xreal-target.json'] = encode({
                'schema': 1, 'model': 'one', 'usb_pid': 0x0438,
                'soc': SOC, 'experimental': True})
        general = config(files['pilot/conf/pilot_default_config.json'])
        assert general['state_machine']['space_screen']['frame_rate_boost_enable'] is False
        assert general['state_machine']['space_screen']['over_clocking_mode'] == 0
        assert general['display']['dp']['synth_stereo'] == '1920_1080_60'
        manifest = dict(provenance, schema=2, profile=model+'-603',
                        soc=SOC, pilot=PILOT, abi_libraries=ABI_LIBRARIES,
                        mmz=[MMZ_START, MMZ_END],
                        verification='candidate; physical mode/performance tests required')
        manifest['build_id'] = sha(encode(manifest))
        files['pilot/xreal-build.json'] = encode(manifest)
        files['pilot/checksums.sha256'] = ''.join(
            sha(data)+'  ./'+name[6:]+'\n' for name, data in sorted(files.items())
            if not name.endswith('/') and name != 'pilot/checksums.sha256').encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for name, data in sorted(files.items()):
                info = copy.copy(attrs.get(name, attrs['pilot/pilot_version.txt']))
                info.filename = name
                info.date_time = (2026, 1, 1, 0, 0, 0)
                info.comment = b''
                info.extra = b''
                z.writestr(info, data)
        packed = bytearray(base[:64] + stream.getvalue().translate(bytes(i ^ 0x66 for i in range(256))))
        packed[4:8] = (len(packed)-8).to_bytes(4, 'little')
        packed[:4] = container_crc(packed).to_bytes(4, 'little')
        name = f'XREAL-{model}-Pilot2-RU-{VERSION}.bin'
        (out/name).write_bytes(packed)
        _, verified, _ = read_container(out/name, PILOT, 0x66)
        results[name] = {'sha256': sha(packed), 'bytes': len(packed),
                         'readback_files': len(verified), 'build': manifest}
    (out/'build-report.json').write_bytes(encode(results))
    print(json.dumps({k: {x: v[x] for x in ('sha256', 'bytes', 'readback_files')}
                      for k, v in results.items()}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--clang', type=Path, required=True)
    p.add_argument('--sysroot', type=Path, required=True,
                   help='Directory containing the three hash-pinned target ELF libraries')
    p.add_argument('--stock', type=Path, required=True, help='Original vendor Pilot 2 ZIP')
    p.add_argument('--base', type=Path, default=ROOT/'outputs/xreal-pilot2-real3d-cpu/XREAL-Pilot2-RU-Hebe-Real3D-CPU.bin')
    p.add_argument('--output', type=Path, default=ROOT/'outputs/v0.7.0-rc1')
    build(p.parse_args())
