"""Hash-pinned OSD bridge for a local One Pro mesh / Pro+ port comparison.

The existing 3D Effect slider becomes a two-position algorithm selector.
Four UI reads are redirected; state-machine reads keep the real strength.
The native setter is called with Natural (2) for either algorithm.
"""
import csv
import hashlib
import io
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'work/elfdeps'))
from elftools.elf.elffile import ELFFile

OSD_SHA = '8cebc5bc451833eb4dceadf602ba3a3d3cdbbde1e5d2646b43be29674e60726e'
MESH_SHA = 'ce310b124ab82b909ef4b3a8264a6ba3c0b05a724a21bf6ebd8492f022d99e3d'
DL_SHA = 'd9c4fd4bbf21b3f5db5b8ed61eb2f7aaf98a06dba15e881e1058e64755d9f64f'
GET_CALLS = (0xf2f5c, 0xf443c, 0xf6788, 0xf6aac)
SET_CALL = 0xf2f2c
NATIVE_GET, NATIVE_SET = 0x16c558, 0x1743f8


def checked(data, expected):
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Unexpected comparison input hash')
    return data


def branch(pc, target, link=True):
    delta = target - pc
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError('ARM64 branch out of range')
    return (0x94000000 if link else 0x14000000) | ((delta // 4) & 0x3ffffff)


def adrp(reg, pc, target):
    delta = ((target & ~4095) - (pc & ~4095)) // 4096
    if not -(1 << 20) <= delta < (1 << 20):
        raise ValueError('ADRP out of range')
    return 0x90000000 | ((delta & 3) << 29) | (((delta >> 2) & 0x7ffff) << 5) | reg


def patch_osd(data):
    checked(data, OSD_SHA)
    elf = ELFFile(io.BytesIO(data))
    result = bytearray(data)
    loads = [s for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
    notes = [(i, s) for i, s in enumerate(elf.iter_segments())
             if s['p_type'] == 'PT_NOTE']
    if len(notes) != 1 or notes[0][1]['p_vaddr'] != 0x200 or notes[0][1]['p_filesz'] != 36:
        raise ValueError('Expected disposable GNU build-ID note')
    # Replace only the build-ID note with two branch stubs (12 + 20 bytes).
    # Reuse its PHDR for an appended RW metadata segment. Unlike a generic ELF
    # rewrite this preserves EVERY vendor/font PT_LOAD address and avoids RWX.
    offset = (len(data) + 4095) & ~4095
    va = (max(s['p_vaddr'] + s['p_memsz'] for s in loads) + 4095) & ~4095
    payload = bytearray(16)  # resolved external-function pointers
    moved = {}

    def append(name, blob):
        payload.extend(bytes((-len(payload)) % 8))
        at = len(payload)
        payload.extend(blob)
        moved[name] = (va + at, offset + at, len(blob))
        return va + at

    def section(name):
        item = elf.get_section_by_name(name)
        if item is None:
            raise ValueError('Missing ELF section: ' + name)
        return item.data()

    strings = bytearray(section('.dynstr'))
    def string(text):
        at = len(strings)
        strings.extend(text.encode() + b'\0')
        return at
    library_name = string('libxreal_cpu_adapter.so')
    symbols = bytearray(section('.dynsym'))
    count = len(symbols) // 24
    for name in ('xreal_compare_get', 'xreal_compare_set'):
        symbols.extend(struct.pack('<IBBHQQ', string(name), 0x12, 0, 0, 0, 0))
    symbol_va = append('.dynsym', symbols)
    string_va = append('.dynstr', strings)
    versions = section('.gnu.version')
    if len(versions) != count * 2:
        raise ValueError('Unexpected symbol versions')
    version_va = append('.gnu.version', versions + struct.pack('<HH', 1, 1))
    # SysV hash includes all symbols; no GNU hash is used by this pinned OSD.
    bucket_count = struct.unpack_from('<I', section('.hash'))[0]
    buckets, chains = [0]*bucket_count, [0]*(count+2)
    for index in range(1, count+2):
        pos = struct.unpack_from('<I', symbols, index*24)[0]
        name = strings[pos:strings.index(0, pos)]
        value = 0
        for byte in name:
            value = (value << 4) + byte
            high = value & 0xf0000000
            if high: value ^= high >> 24
            value &= ~high
        bucket = value % bucket_count
        chains[index] = buckets[bucket]
        buckets[bucket] = index
    hash_va = append('.hash', struct.pack('<'+'I'*(2+len(buckets)+len(chains)),
                                         bucket_count, count+2, *buckets, *chains))
    relocations = section('.rela.dyn')
    for index in range(2):
        relocations += struct.pack('<QQq', va + index*8,
                                   ((count+index) << 32) | 257, 0)
    rela_va = append('.rela.dyn', relocations)
    dynamic = []
    for tag, value in struct.iter_unpack('<QQ', section('.dynamic')):
        if tag == 0: break
        dynamic.append((tag, value))
    replacements = {4: hash_va, 5: string_va, 6: symbol_va, 7: rela_va,
                    8: len(relocations), 10: len(strings), 0x6ffffff0: version_va}
    if not set(replacements) <= {t for t,v in dynamic} or any(t == 0x6ffffef5 for t,v in dynamic):
        raise ValueError('Unexpected dynamic metadata')
    dynamic = [(t, replacements.get(t, v)) for t,v in dynamic]
    dynamic += [(1, library_name), (0, 0)]
    dynamic_va = append('.dynamic', b''.join(struct.pack('<QQ', *x) for x in dynamic))

    def put(pc, words):
        mapping = next(s for s in loads if s['p_vaddr'] <= pc < s['p_vaddr']+s['p_filesz'])
        at = mapping['p_offset'] + pc - mapping['p_vaddr']
        result[at:at+4*len(words)] = struct.pack('<'+'I'*len(words), *words)

    def replace(pc, expected, value):
        mapping = next(s for s in loads if s['p_vaddr'] <= pc < s['p_vaddr']+s['p_filesz'])
        at = mapping['p_offset'] + pc - mapping['p_vaddr']
        if struct.unpack_from('<I', result, at)[0] != expected:
            raise ValueError(f'Unexpected OSD instruction at {pc:x}')
        put(pc, [value])

    start, setter, got = 0x200, 0x210, va
    put(start, [adrp(16, start, got), 0xf9400210, 0xd61f0200, 0xd503201f])
    put(setter, [adrp(1, setter, NATIVE_SET),
                 0x91000021 | ((NATIVE_SET & 4095) << 10),
                 adrp(16, setter+8, got+8), 0xf9400610, 0xd61f0200])
    for pc in GET_CALLS:
        replace(pc, branch(pc, NATIVE_GET), branch(pc, start))
    replace(SET_CALL, branch(SET_CALL, NATIVE_SET), branch(SET_CALL, setter))
    replace(0xf67c0, 0x52800083, 0x52800043)
    replace(0xf42dc, 0x911ee021, 0x911f8021)
    replace(0xf432c, 0x911f0021, 0x911fc021)
    # Relocate section headers for tooling as well as the loader's dynamic tags.
    for index, sec in enumerate(elf.iter_sections()):
        at = elf['e_shoff'] + index*elf['e_shentsize']
        if sec.name in moved:
            addr, off, size = moved[sec.name]
            struct.pack_into('<QQQ', result, at+16, addr, off, size)
        elif sec.name == '.note.gnu.build-id':
            struct.pack_into('<I', result, at+4, 1)  # PROGBITS, no longer a note
            struct.pack_into('<Q', result, at+8, 6)  # ALLOC | EXECINSTR
    for index, seg in enumerate(elf.iter_segments()):
        at = elf['e_phoff'] + index*elf['e_phentsize']
        if index == notes[0][0]:
            struct.pack_into('<IIQQQQQQ', result, at, 1, 6, offset, va, va,
                             len(payload), len(payload), 4096)
        elif seg['p_type'] == 'PT_DYNAMIC':
            addr, off, size = moved['.dynamic']
            struct.pack_into('<IIQQQQQQ', result, at, 2, 6, off, addr, addr, size, size, 8)
    result.extend(bytes(offset-len(result)))
    result.extend(payload)
    verified = ELFFile(io.BytesIO(result))
    for original in loads:
        if not any(s.header == original.header for s in verified.iter_segments()):
            raise ValueError('Original OSD load mapping changed')
    return bytes(result)


def patch_labels(data):
    rows = list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))
    labels = {
        'Mode3dEffectSetting': ('Real3D algorithm', 'Алгоритм Real3D'),
        'SoftDepth': ('One Pro (mesh)', 'One Pro (mesh)'),
        'BalancedDepth': ('Pro+ (CPU port)', 'Pro+ (CPU-порт)'),
    }
    found = set()
    for row in rows[4:]:
        if len(row) > 17 and row[1] in labels:
            english, russian = labels[row[1]]
            # Existing RU package uses Polish column 13, plus draft RU column 17.
            row[4] = english
            row[13] = row[17] = russian
            found.add(row[1])
    if found != set(labels):
        raise ValueError('Missing OSD comparison labels')
    stream = io.StringIO(newline='')
    csv.writer(stream, lineterminator='\n').writerows(rows)
    return b'\xef\xbb\xbf' + stream.getvalue().encode()
