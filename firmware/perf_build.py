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
                      'installer/profiles.py', 'installer/package_format.py']
# rc7 flags, plus scheduling for the in-order Cortex-A53 (CPU part 0xd03 r0p4)
# and both A53 errata workarounds that apply to that revision.
FLAGS = ['--target=aarch64-linux-gnu', '-fPIC', '-O2', '-shared', '-nostdlib',
         '-fuse-ld=lld', '-Wall', '-Wextra', '-Werror', '-Wl,--no-undefined',
         '-Wl,-soname,libxreal_cpu_adapter.so', '-I', 'firmware',
         '-DXREAL_COMPARE_ONEPRO_WARP=1',
         '-mcpu=cortex-a53', '-mfix-cortex-a53-835769', '-Wl,--fix-cortex-a53-843419']


CONFIG = 'pilot/conf/pilot_default_config.json'


GAME_OSD_SHA = '0067f65b58d6f708d75163ce89224162f0d62c5ee054579eb65b46b9dc77978e'  # rc7 libnr_osd.so
GAME_ITEM_VA = 0xf2048           # display-mode list: the "Vivid" row's mode code
GAME_ITEM_BEFORE = 0x52800047    # mov w7, #2  (Vivid)
GAME_ITEM_AFTER = 0x528000a7     # mov w7, #5  (vendor "game" mode: EDID 1920_1080_144)
OSD = 'pilot/bin/libnr_osd.so'
LANG = 'pilot/resource/osd_language.csv'


def experimental(files):
    """Opt-in experiment (-EXP144), exact and asserted edits.

    1. Display mode list: the "Vivid" row sends the vendor's hidden "game"
       mode (5) instead of Vivid (2). Pilot's game mode requests EDID
       1920_1080_144 and maps it to a 144 Hz OLED timing (dp_oled_map). Its
       separate overclock call is a stub in this Pilot, so this is the only
       route. One Pro panel support is unverified; a vendor fallback screen
       exists for the game mode.
    2. The row is renamed in both Russian columns: "Насыщенный" -> "Игровой".
    3. AI HDR and Reading request 60 Hz instead of 90 Hz from the source,
       because their frames are processed on the CPU.
    """
    import struct
    from package_format import validate_config

    def offset(data, va):
        start = struct.unpack_from('<Q', data, 32)[0]
        size, count = struct.unpack_from('<HH', data, 54)
        for i in range(count):
            kind, _, off, addr, _, length, _, _ = struct.unpack_from('<II6Q', data, start+i*size)
            if kind == 1 and addr <= va and va+4 <= addr+length:
                return off+va-addr
        raise ValueError('Instruction outside file-backed LOAD segment')
    edits = []
    osd = bytearray(files[OSD])
    if sha(bytes(osd)) != GAME_OSD_SHA:
        raise ValueError('EXP144 requires the exact rc7 OSD')
    at = offset(bytes(osd), GAME_ITEM_VA)
    if struct.unpack_from('<I', osd, at)[0] != GAME_ITEM_BEFORE:
        raise ValueError('Unexpected instruction at the Vivid display-mode row')
    struct.pack_into('<I', osd, at, GAME_ITEM_AFTER)
    files[OSD] = bytes(osd)
    edits.append(f'{OSD} @{GAME_ITEM_VA:#x}: mov w7,#2 -> mov w7,#5 (Vivid row -> game 144 Hz)')

    text = files[LANG].decode('utf-8')
    lines = text.splitlines(keepends=True)
    rows = [i for i, l in enumerate(lines) if l.startswith(',Vivid,')]
    if len(rows) != 1 or '"' in lines[rows[0]]:
        raise ValueError('Vivid translation row not found exactly once')
    cells = lines[rows[0]].rstrip('\n').split(',')
    if cells[13] != 'Насыщенный' or cells[17] != 'Насыщенный':
        raise ValueError('Unexpected Russian Vivid label')
    cells[13] = cells[17] = 'Игровой'
    lines[rows[0]] = ','.join(cells) + '\n'
    files[LANG] = ''.join(lines).encode('utf-8')
    edits.append(f'{LANG}: Vivid (ru) Насыщенный -> Игровой')

    config = files[CONFIG].decode('utf-8')
    for section, block in (('"color_mode" :', '"reading" :'), ('"dynamic_range" :', '"ai hdr" :')):
        s = config.index(section)
        b = config.index(block, s)
        e = config.index('}', b)
        old, new = '"dp" : "1920_1080_90_DEFAULT",', '"dp" : "1920_1080_60",'
        if config[b:e].count(old) != 1:
            raise ValueError(f'Config anchor not unique in {block}')
        at = b + config[b:e].index(old)
        config = config[:at] + new + config[at+len(old):]
        edits.append(f'{CONFIG} {block.strip(": ")}: dp 1920_1080_90_DEFAULT -> 1920_1080_60')
    s = config.index('"color_mode" :')
    game = config.index('"game" :', s)
    if '"dp" : "1920_1080_144"' not in config[game:config.index('}', game)]:
        raise ValueError('Vendor game mode no longer requests 1920_1080_144')
    files[CONFIG] = config.encode('utf-8')
    validate_config(files[CONFIG], CONFIG)
    return [OSD, LANG, CONFIG], edits


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
              'aihdr': 'dense 4.25 MiB and compact 289 KiB kernels; on-device autotune picks the faster',
              'real3d_proplus': 'vectorized exact-output warp (upstream 2689834)',
              'menu_and_osd': 'unchanged rc7 (compare_ui.c and patched libnr_osd.so)',
              'abi': abi_report})
    changed_expected = [ADAPTER, MANIFEST, CHECKSUMS]
    if args.experimental:
        changed, edits = experimental(files)
        changed_expected += changed
        manifest['perf']['experimental'] = {
            'edits': edits,
            'note': 'Display-mode "Игровой" (vendor game mode, 144 Hz) replaces Vivid; 144 Hz on the One Pro panel is unverified'}
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
    name = f'XREAL-one-pro-Pilot2-RU-0.7.0-{args.tag}{"-EXP144" if args.experimental else ""}.bin'
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
    p.add_argument('--experimental', action='store_true',
                   help='Also apply experimental(): Vivid row -> game 144 Hz, 60 Hz for AI HDR/Reading')
    build(p.parse_args())
