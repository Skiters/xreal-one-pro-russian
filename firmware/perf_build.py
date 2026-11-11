"""Perf candidate for One Pro: rebuild only libxreal_cpu_adapter.so and repack
the user-tested rc7 BIN around it. Never opens a USB device.

Inputs are public and hash-pinned: the published rc7 One Pro BIN and the base
container already stored in this repository. System libraries are replaced by
link-time stubs generated from firmware/abi_imports.json (symbol names and
versions only); the resulting imports are checked against rc7's adapter.
"""
import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer'))
sys.path.insert(0, str(ROOT / 'firmware'))
from package_format import container_crc, read_container, validate_elf  # noqa: E402
from profiles import validate_build  # noqa: E402

RC7_SHA = '721e08d0828e116ad18d26168d43a5fc9788ba3ce52e0b34a7c234159b5bde43'
RC7_URL = ('https://github.com/Skiters/xreal-one-pro-russian/releases/download/'
           'v0.7.0/XREAL-one-pro-Pilot2-RU-0.7.0.bin')
BASE_SHA = '1b72fc6acd90fc9b8995644cca659907d571292f19a19a33cdbddd59da5decd0'
PILOT = '2.0.0.20260920102250'
XOR = bytes(i ^ 0x66 for i in range(256))
ADAPTER = 'pilot/bin/libxreal_cpu_adapter.so'
MANIFEST = 'pilot/xreal-build.json'
CHECKSUMS = 'pilot/checksums.sha256'
COMPILED = ['firmware/cpu_adapter.c', 'firmware/lut_fast.c', 'firmware/runtime.c',
            'work/3d-lab/real3d_cpu.c', 'firmware/compare_ui.c', 'firmware/ui_dispatch.S',
            'firmware/onepro_warp.c']
SOURCES = COMPILED + ['firmware/runtime.h', 'firmware/onepro_warp.h',
                      'firmware/generate_compare_ui.py', 'firmware/compare_modes.py',
                      'firmware/abi_imports.json', 'firmware/perf_build.py',
                      'installer/profiles.py', 'installer/package_format.py',
                      'firmware/ui_fast.py']
# rc7 flags, plus scheduling for the in-order Cortex-A53 (CPU part 0xd03 r0p4)
# and both A53 errata workarounds that apply to that revision.
# -nostdlibinc: arm_neon.h needs only clang's own stdint.h; the build host has
# no aarch64 libc headers and the sources include no system headers.
FLAGS = ['--target=aarch64-linux-gnu', '-fPIC', '-O2', '-shared', '-nostdlib', '-nostdlibinc',
         '-fuse-ld=lld', '-Wall', '-Wextra', '-Werror', '-Wl,--no-undefined',
         '-Wl,-soname,libxreal_cpu_adapter.so', '-I', 'firmware',
         '-DXREAL_COMPARE_ONEPRO_WARP=1',
         '-mcpu=cortex-a53', '-mfix-cortex-a53-835769', '-Wl,--fix-cortex-a53-843419']


CONFIG = 'pilot/conf/pilot_default_config.json'


AIHDR_CUBE = 'pilot/resource/lut_3d_ai_hdr.cube'


def _smooth(edge0, edge1, x):
    t = min(1.0, max(0.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3 - 2*t)


def soften_cube(text, strength, neutral_greys=True, tone=None, skin=0.5):
    """Re-grade the vendor AI HDR cube (owner found it over-bright).
    Each node's vendor change is split into luma (Rec.709) and chroma:
      tone   share of the luma change kept (None: same as strength),
      strength share of the chroma change kept,
      skin   extra factor on the chroma change for skin-like hues (soft mask
             over hue/saturation/lightness, no hard edges).
    neutral_greys: the vendor cube tints dark greys violet (grey 16 -> RGB
    10/8/12); remove each grey node's tint, scaled by how neutral a node is.
    Keeps the header, size and R-fastest order."""
    import colorsys
    tone = strength if tone is None else tone
    lines, rows, slots = text.splitlines(), [], []
    for n, line in enumerate(lines):
        parts = line.split()
        try:
            values = [float(v) for v in parts] if len(parts) == 3 else None
        except ValueError:
            values = None
        if values is not None:
            slots.append(n)
            rows.append(values)
    if len(rows) != 4913:
        raise ValueError(f'Expected 4913 cube rows, found {len(rows)}')
    for index, v in enumerate(rows):
        i = (index % 17 / 16, index // 17 % 17 / 16, index // 289 / 16)
        yi = 0.2126*i[0] + 0.7152*i[1] + 0.0722*i[2]
        yv = 0.2126*v[0] + 0.7152*v[1] + 0.0722*v[2]
        h, l, sat = colorsys.rgb_to_hls(*i)
        hue = h * 360
        hue = hue - 360 if hue > 180 else hue          # -180..180, skin ~ 0..50
        mask = (_smooth(-25, -5, hue) * (1 - _smooth(45, 65, hue)) *
                _smooth(0.05, 0.15, sat) * (1 - _smooth(0.7, 0.9, sat)) *
                _smooth(0.1, 0.2, l) * (1 - _smooth(0.85, 0.95, l)))
        k = strength * (1 - (1 - skin) * mask)
        y = yi + tone*(yv - yi)
        rows[index] = [y + (i[c] - yi) + k*((v[c] - yv) - (i[c] - yi)) for c in range(3)]
    if neutral_greys:
        tint = []
        for k in range(17):
            grey = rows[k*(1 + 17 + 289)]
            mean = sum(grey) / 3
            tint.append([c - mean for c in grey])
        for index, values in enumerate(rows):
            node = (index % 17, index // 17 % 17, index // 289)
            hi, lo = max(node), min(node)
            weight = 1.0 - (hi - lo) / hi if hi else 1.0
            k = round(sum(node) / 3)
            rows[index] = [v - weight*t for v, t in zip(values, tint[k])]
    rows = [[min(1.0, max(0.0, v)) for v in r] for r in rows]
    # Sanity: greys stay neutral and monotonic, black stays black.
    greys = [rows[k*307] for k in range(17)]
    if any(max(g) - min(g) > 1/255 for g in greys) or any(
            sum(b) < sum(a) for a, b in zip(greys, greys[1:])) or max(greys[0]) > 0:
        raise ValueError('Re-graded cube: grey axis not neutral/monotonic')
    for n, values in zip(slots, rows):
        lines[n] = ' '.join(f'{v:.6f}' for v in values)
    return '\n'.join(lines) + '\n'


def experimental(files, cinema=False, hdr_strength=100, hdr_tone=None):
    """Opt-in variant (-HDR60-UI), One Pro only:
    1. AI HDR and Reading request 60 Hz instead of 90 Hz from the source,
       because their frames are processed on the CPU.
    2. Upstream's UI experiment (firmware/ui_fast.py, 9d5a173/2689834): exact
       rc7 OSD refresh/animation timers 30 -> 16 ms, OSD action/interface logs
       off, first boot wait 1.0 -> 0.25 s, unused ASUS OSD modules removed.
       The adapter is NOT built with XREAL_UI_FAST, so HDR timing stays logged
       every 30 frames for measurements.
    The display-mode menu rows are rc7 (Vivid/«Насыщенный» stays).

    cinema=True (-CINEMA30-UI): AI HDR and 2D->3D (synth stereo, both 3D
    algorithms share it) request EDID 1920_1080_30 instead; Reading keeps
    60 Hz and Movie keeps rc7. Pilot's own dp_oled_map shows
    a 30 Hz input on the 90 Hz OLED timing, every frame exactly three times.
    The CPU path (~18 ms AI HDR, ~15 ms Real3D) then never misses a source
    frame, so the cadence is even instead of ~50 of 60 frames at random.
    """
    from package_format import validate_config
    edits = []
    config = files[CONFIG].decode('utf-8')
    blocks = [('"color_mode" :', '"reading" :', '1920_1080_60'),
              ('"dynamic_range" :', '"ai hdr" :', '1920_1080_30' if cinema else '1920_1080_60')]
    if '"1920_1080_30": "1920_1080_90"' not in config:
        raise ValueError('dp_oled_map no longer maps 30 Hz input to the 90 Hz OLED timing')
    for section, block, target in blocks:
        s = config.index(section)
        b = config.index(block, s)
        e = config.index('}', b)
        old, new = '"dp" : "1920_1080_90_DEFAULT",', f'"dp" : "{target}",'
        if config[b:e].count(old) != 1:
            raise ValueError(f'Config anchor not unique in {block}')
        at = b + config[b:e].index(old)
        config = config[:at] + new + config[at+len(old):]
        edits.append(f'{CONFIG} {block.strip(": ")}: dp 1920_1080_90_DEFAULT -> {target}')
    if cinema:
        old, new = '"synth_stereo" : "1920_1080_60",', '"synth_stereo" : "1920_1080_30",'
        if config.count(old) != 1:
            raise ValueError('synth_stereo anchor not unique')
        config = config.replace(old, new)
        edits.append(f'{CONFIG} dp.synth_stereo (2D->3D): 1920_1080_60 -> 1920_1080_30')
    files[CONFIG] = config.encode('utf-8')
    changed_extra = []
    if hdr_strength != 100:
        if not 10 <= hdr_strength < 100:
            raise ValueError('hdr_strength must be 10..99 percent')
        tone = hdr_strength if hdr_tone is None else hdr_tone
        files[AIHDR_CUBE] = soften_cube(files[AIHDR_CUBE].decode('utf-8'), hdr_strength/100,
                                        tone=tone/100).encode()
        changed_extra.append(AIHDR_CUBE)
        edits.append(f'{AIHDR_CUBE}: re-graded, colour {hdr_strength}%, tone {tone}%, '
                     'skin colour boost halved, dark-grey tint removed')
    from ui_fast import apply as ui_fast_apply
    before = set(files)
    ui = ui_fast_apply(files, 'one-pro')  # rewrites CONFIG after the 60 Hz edit
    removed = sorted(n for n in before - set(files) if not n.endswith('/'))  # files, not directory entries
    edits.append('ui_fast (upstream): OSD timers 30 -> 16 ms, OSD logs off, '
                 f'boot_wait_time1 0.25 s, removed {len(removed)} ASUS files')
    validate_config(files[CONFIG], CONFIG)
    if 'refresh_timer_ms' not in ui or ui['refresh_timer_ms'] != 16:
        raise ValueError('Unexpected ui_fast result')
    return [CONFIG, 'pilot/bin/libnr_osd.so'] + changed_extra + removed, edits


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked(path, expected):
    data = Path(path).read_bytes()
    if sha(data) != expected:
        raise ValueError(f'Input hash mismatch: {path}')
    return data


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode()


def unpack(container):
    with zipfile.ZipFile(io.BytesIO(container[64:].translate(XOR))) as z:
        return ({i.filename: z.read(i) for i in z.infolist()},
                {i.filename: copy.copy(i) for i in z.infolist()})


def checksums(files):
    return ''.join(sha(data)+'  ./'+name[6:]+'\n' for name, data in sorted(files.items())
                   if not name.endswith('/') and name != CHECKSUMS).encode()


def build_stubs(clang, directory):
    """Empty functions with the device's sonames and symbol versions."""
    directory.mkdir(parents=True, exist_ok=True)
    abi = json.loads((ROOT/'firmware/abi_imports.json').read_text())
    for lib in abi['libraries']:
        stem = directory/lib['link_name'].replace('.', '_')
        stem.with_suffix('.c').write_text(''.join(f'void {s}(void) {{}}\n' for s in lib['symbols']))
        command = [str(clang), '--target=aarch64-linux-gnu', '-shared', '-nostdlib', '-fPIC',
                   '-fno-builtin', '-w', '-fuse-ld=lld', str(stem.with_suffix('.c')), '-o', str(directory/lib['link_name'])]
        if lib['soname']:
            command.append('-Wl,-soname,'+lib['soname'])
        if lib['version']:
            script = stem.with_suffix('.map')
            script.write_text(lib['version']+' { global: '+' '.join(s+';' for s in lib['symbols'])+
                              ' local: *; };\n')
            command.append('-Wl,--version-script,'+str(script))
        subprocess.run(command, check=True)
    return abi


def dynamic(blob):
    from elftools.elf.elffile import ELFFile
    elf = ELFFile(io.BytesIO(blob))
    dyn = elf.get_section_by_name('.dynamic')
    needed = [t.needed for t in dyn.iter_tags() if t.entry.d_tag == 'DT_NEEDED']
    versions = {}
    section = elf.get_section_by_name('.gnu.version_r')
    if section:
        for _, auxiliary in section.iter_versions():
            for entry in auxiliary:
                versions[entry.entry.vna_other] = entry.name
    symbol_versions = elf.get_section_by_name('.gnu.version')
    imports, exports = {}, set()
    for index, symbol in enumerate(elf.get_section_by_name('.dynsym').iter_symbols()):
        if not symbol.name:
            continue
        if symbol['st_shndx'] == 'SHN_UNDEF':
            ndx = symbol_versions.get_symbol(index)['ndx'] if symbol_versions else 0
            imports[symbol.name] = versions.get(ndx) if isinstance(ndx, int) else None
        elif symbol['st_info']['bind'] in ('STB_GLOBAL', 'STB_WEAK'):
            exports.add(symbol.name)
    return needed, imports, exports


LINKER_MARKERS = {'__bss_start', '_edata', '_end'}


def check_abi(new, old, abi, osd):
    new_needed, new_imports, new_exports = dynamic(new)
    old_needed, old_imports, old_exports = dynamic(old)
    _, osd_imports, _ = dynamic(osd)
    used_by_osd = set(osd_imports) & old_exports
    allowed = {}
    for lib in abi['libraries']:
        for symbol in lib['symbols']:
            allowed.setdefault(symbol, lib['version'])
    if new_needed != old_needed:
        raise ValueError(f'DT_NEEDED changed: {new_needed} != {old_needed}')
    for name, version in new_imports.items():
        if name not in allowed or allowed[name] != version:
            raise ValueError(f'Import outside the device ABI: {name}@{version}')
        if name in old_imports and old_imports[name] != version:
            raise ValueError(f'Import version differs from rc7: {name}')
    # The patched OSD imports adapter symbols; every one must still resolve.
    # Linker-generated section markers are not part of the interface.
    missing = (old_exports - LINKER_MARKERS) - new_exports
    if not used_by_osd or missing or not used_by_osd <= new_exports:
        raise ValueError(f'rc7 exports missing: {sorted(missing | (used_by_osd - new_exports))}')
    return {'needed': new_needed, 'imports': len(new_imports),
            'new_imports': sorted(set(new_imports) - set(old_imports)),
            'osd_imports_from_adapter': sorted(used_by_osd),
            'rc7_exports_kept': True}


def build(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    rc7 = checked(args.rc7, RC7_SHA)
    base = checked(args.base, BASE_SHA)
    files, attrs = unpack(rc7)
    # Prove that this repacker reproduces rc7's own metadata before changing it.
    original = json.loads(files[MANIFEST])
    unsigned = {k: v for k, v in original.items() if k != 'build_id'}
    if checksums(files) != files[CHECKSUMS] or sha(encode(unsigned)) != original['build_id']:
        raise RuntimeError('rc7 metadata format not reproduced; refusing to repack.')

    from generate_compare_ui import generate
    base_files, _ = unpack(base)
    generated = out/'compare_row.S'
    generated.write_bytes(generate(base_files['pilot/bin/libnr_osd.so']).encode())
    abi = build_stubs(args.clang, out/'stubs')
    compiler = subprocess.check_output([str(args.clang), '--version'], text=True).splitlines()[0]
    source_hashes = {s: sha((ROOT/s).read_bytes()) for s in SOURCES}
    library = out/'libxreal_cpu_adapter.so'
    flags = list(FLAGS)
    subprocess.run([str(args.clang), *flags, *COMPILED, str(generated),
                    '-L', str(out/'stubs'),
                    *['-l:'+lib['link_name'] for lib in abi['libraries']],
                    '-o', str(library)], cwd=ROOT, check=True)
    if source_hashes != {s: sha((ROOT/s).read_bytes()) for s in SOURCES}:
        raise RuntimeError('Sources changed during compilation.')
    adapter = library.read_bytes()
    validate_elf(adapter, ADAPTER)
    abi_report = check_abi(adapter, files[ADAPTER], abi, files['pilot/bin/libnr_osd.so'])

    manifest = dict(unsigned)
    manifest.update(
        release=original['release']+'-'+args.tag, compiler=compiler, flags=flags,
        sources=source_hashes, python=sys.version.split()[0], zlib=zlib.ZLIB_RUNTIME_VERSION,
        adapter_sha256=sha(adapter),
        verification='perf candidate built from rc7; physical mode/performance tests required',
        perf={'base_bin_sha256': RC7_SHA, 'base_release': original['release'],
              'changed_files': [ADAPTER, MANIFEST, CHECKSUMS],
              'aihdr': 'compact vs compact16 (16-bit NEON) short autotune, 289 KiB table; dynamic row scheduling; dense only as fallback',
              'real3d_proplus': 'vectorized warp, luma bilinear displacement (NEON TBL gather), chroma vertical blend, dynamic row scheduling',
              'menu_and_osd': 'unchanged rc7 (compare_ui.c and patched libnr_osd.so)',
              'abi': abi_report})
    changed_expected = [ADAPTER, MANIFEST, CHECKSUMS]
    if args.experimental:
        changed, edits = experimental(files, cinema=args.cinema, hdr_strength=args.hdr_strength,
                                      hdr_tone=args.hdr_tone)
        changed_expected += changed
        manifest['perf']['experimental'] = {
            'edits': edits,
            'note': 'AI HDR and Reading request 60 Hz from the source; upstream ui_fast OSD timers/logs/ASUS pruning'}
        manifest['perf']['changed_files'] = changed_expected
    manifest['build_id'] = sha(encode(manifest))
    validate_build(manifest, 'experimental', PILOT)
    files[ADAPTER] = adapter
    files[MANIFEST] = encode(manifest)
    files[CHECKSUMS] = checksums(files)

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(files.items()):
            info = copy.copy(attrs[name])
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.comment = b''
            info.extra = b''
            z.writestr(info, data)
    packed = bytearray(rc7[:64] + stream.getvalue().translate(XOR))
    packed[4:8] = (len(packed)-8).to_bytes(4, 'little')
    packed[:4] = container_crc(packed).to_bytes(4, 'little')
    name = f'XREAL-one-pro-Pilot2-RU-0.7.0-{args.tag}{("-CINEMA30-UI" if args.cinema else "-HDR60-UI") + (f"-HDR{args.hdr_strength}" if args.hdr_strength != 100 else "") if args.experimental else ""}.bin'
    target = out/name
    target.write_bytes(packed)

    _, verified, device_target = read_container(target, PILOT, 0x66)
    if device_target is not None:
        raise RuntimeError('One Pro package must not carry a target descriptor.')
    old_files, _ = unpack(rc7)
    changed = sorted(n for n in old_files if not n.endswith('/') and
                     old_files[n] != unpack(bytes(packed))[0].get(n))
    if changed != sorted(changed_expected):
        raise RuntimeError(f'Unexpected changed files: {changed}')
    try:
        import backend
        firmware = backend.identify_package(target)
        if firmware is not backend.EXPERIMENTAL:
            raise RuntimeError('Installer does not classify the BIN as One Pro Pilot 2.')
        installer_check = 'identify_package: experimental (One Pro Pilot 2)'
    except ImportError:
        installer_check = 'skipped: hidapi not installed'
    report = {name: {'sha256': sha(packed), 'bytes': len(packed),
                     'readback_files': len(verified), 'changed_files': changed,
                     'installer_check': installer_check, 'build': manifest}}
    (out/'build-report.json').write_bytes(encode(report))
    (out/'SHA256SUMS.txt').write_text(f'{sha(packed)}  {name}\n')
    print(json.dumps({name: {k: report[name][k] for k in
                             ('sha256', 'bytes', 'readback_files', 'changed_files', 'installer_check')}},
                     indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--clang', type=Path, required=True,
                   help='clang with lld able to target aarch64-linux-gnu')
    p.add_argument('--rc7', type=Path, required=True, help=f'Published rc7 BIN ({RC7_URL})')
    p.add_argument('--base', type=Path,
                   default=ROOT/'outputs/xreal-pilot2-real3d-cpu/XREAL-Pilot2-RU-Hebe-Real3D-CPU.bin')
    p.add_argument('--output', type=Path, default=ROOT/'outputs/perf')
    p.add_argument('--tag', default='perf1')
    p.add_argument('--hdr-strength', type=int, default=100,
                   help='With --experimental: AI HDR cube strength in percent (blend with identity)')
    p.add_argument('--hdr-tone', type=int, default=None,
                   help='With --hdr-strength: share of the AI HDR brightness change kept, percent')
    p.add_argument('--cinema', action='store_true',
                   help='With --experimental: 30 Hz source (90 Hz OLED, 3x) for AI HDR/Reading/Movie/2D->3D')
    p.add_argument('--experimental', action='store_true',
                   help='Also apply experimental(): 60 Hz source for AI HDR/Reading + upstream ui_fast')
    build(p.parse_args())
