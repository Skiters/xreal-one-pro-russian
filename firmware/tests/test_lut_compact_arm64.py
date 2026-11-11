"""Run the shipped ARM64 compact AI HDR kernel; compare with the oracle and the
dense kernel, and report executed instructions per pixel for both kernels.

Instruction counts are not cycles: the dense kernel's cost on the glasses is
dominated by cache/TLB misses on its 4.25 MiB table, which emulation cannot
show. The adapter's on-device autotune makes the actual choice.
"""
import argparse
import io
import json
from pathlib import Path
import random
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                 UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5,
                                 UC_ARM64_REG_X6, UC_ARM64_REG_X7, UC_ARM64_REG_SP,
                                 UC_ARM64_REG_LR, UC_ARM64_REG_PC)
from test_lut import reference, cubes_from_bin

BASE, DATA, STACK, STOP = 0x100000, 0x2000000, 0x3000000, 0x3010000
REGS = [UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
        UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7]


class Machine:
    def __init__(self, path):
        self.u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.u.mem_map(BASE, 0x400000)
        self.u.mem_map(DATA, 0xa00000)
        self.u.mem_map(STACK, 0x20000)
        elf = ELFFile(io.BytesIO(Path(path).read_bytes()))
        for seg in elf.iter_segments():
            if seg['p_type'] == 'PT_LOAD':
                self.u.mem_write(BASE + seg['p_vaddr'], seg.data())
        self.symbols = {s.name: BASE + s['st_value']
                        for s in elf.get_section_by_name('.symtab').iter_symbols()}
        self.count = 0
        self.u.hook_add(UC_HOOK_CODE, self._tick)

    def _tick(self, *_):
        self.count += 1

    def call(self, name, *args):
        sp = STACK + 0x8000
        for reg, value in zip(REGS, args):
            self.u.reg_write(reg, value)
        for i, value in enumerate(args[8:]):
            self.u.mem_write(sp + i*8, struct.pack('<Q', value))
        self.u.reg_write(UC_ARM64_REG_SP, sp)
        self.u.reg_write(UC_ARM64_REG_LR, STOP)
        self.count = 0
        self.u.emu_start(self.symbols[name], STOP, count=2_000_000_000)
        assert self.u.reg_read(UC_ARM64_REG_PC) == STOP, name
        return self.u.reg_read(UC_ARM64_REG_X0), self.count


def frames(rng, width, height):
    noise = [tuple(rng.randrange(256) for _ in range(3)) for _ in range(width*height)]
    gradient = []
    for y in range(height):
        for x in range(width):
            base = (x*255//(width-1), (x+3*y)*200//(width-1) % 256, 255-x*255//(width-1))
            gradient.append(tuple(min(255, max(0, v + rng.randrange(-3, 4))) for v in base))
    # Letterbox: black bars at both ends of each row, picture in the middle.
    letterbox = [(0, 0, 0) if x < width//5 or x >= width-width//5 else gradient[y*width+x]
                 for y in range(height) for x in range(width)]
    return {'noise': noise, 'gradient': gradient, 'letterbox': letterbox}


def test(path, bin_path):
    m = Machine(path)
    cube_ptr, dense, compact, compact2 = DATA, DATA + 0x10000, DATA + 0x500000, DATA + 0x5c0000
    tetra = DATA + 0x640000
    dense_size, _ = m.call('xreal_rgb_table_bytes')
    compact_size, _ = m.call('xreal_compact_table_bytes')
    assert compact_size == (17*17*256 + 512)*4 and compact_size < 0x500000 - 0x10000
    rng = random.Random(20261110)
    cubes = {'random': [rng.randrange(256)*256 for _ in range(14739)]}
    if bin_path:
        cubes.update(cubes_from_bin(bin_path))
    guard = bytes([213])*64
    width, height = 1920, 2
    pitches = [2048, 2048, 2048]
    planes = [DATA + 0x700000 + i*0x10000 for i in range(3)]
    report = {}
    for name, cube in cubes.items():
        m.u.mem_write(cube_ptr, struct.pack('<14739H', *cube))
        m.u.mem_write(compact - 64, guard)
        m.u.mem_write(compact + compact_size, guard)
        for i in range(4):
            assert m.call('xreal_build_compact_table_range', cube_ptr, compact,
                          17*i//4, 17*(i+1)//4)[0] == 0
            assert m.call('xreal_build_rgb_table_range', cube_ptr, dense,
                          17*i//4, 17*(i+1)//4)[0] == 0
            assert m.call('xreal_build_compact2_table_range', cube_ptr, compact2,
                          17*i//4, 17*(i+1)//4)[0] == 0
        assert m.call('xreal_build_tetra_table', cube_ptr, tetra)[0] == 0
        assert bytes(m.u.mem_read(compact - 64, 64)) == guard
        assert bytes(m.u.mem_read(compact + compact_size, 64)) == guard
        for kind, pixels in frames(rng, width, height).items():
            raw = [bytearray([171])*(s*height) for s in pitches]
            for i, rgb in enumerate(pixels):
                y, x = divmod(i, width)
                for c in range(3):
                    raw[c][y*pitches[c]+x] = rgb[c]
            outputs, counts = [], []
            for kernel, table in (('xreal_full_rgb_lut', dense), ('xreal_compact_rgb_lut', compact),
                                  ('xreal_compact2_rgb_lut', compact2),
                                  ('xreal_compact_pf_rgb_lut', compact),
                                  ('xreal_tetra_rgb_lut', tetra),
                                  ('xreal_compact16_rgb_lut', compact),
                                  ('xreal_blocks_rgb_lut', compact)):
                for ptr, data in zip(planes, raw):
                    m.u.mem_write(ptr, bytes(data))
                rc, count = m.call(kernel, table, *planes, *pitches, width, height)
                assert rc == 0, kernel
                outputs.append([bytes(m.u.mem_read(ptr, s*height)) for ptr, s in zip(planes, pitches)])
                counts.append(count)
            error = diff = 0
            for i, rgb in enumerate(pixels):
                y, x = divmod(i, width)
                small = [outputs[1][c][y*pitches[c]+x] for c in range(3)]
                big = [outputs[0][c][y*pitches[c]+x] for c in range(3)]
                error = max(error, max(abs(a-b) for a, b in zip(small, reference(cube, rgb))))
                diff = max(diff, max(abs(a-b) for a, b in zip(small, big)))
            for c, pitch in enumerate(pitches):
                for y in range(height):
                    assert outputs[1][c][y*pitch+width:(y+1)*pitch] == bytes([171])*(pitch-width)
            assert error <= 1, (name, kind, error)
            assert outputs[2] == outputs[1], (name, kind, 'compact2 differs from compact')
            assert outputs[3] == outputs[1], (name, kind, 'compact_pf differs from compact')
            tetra_error = 0
            for i, rgb in enumerate(pixels):
                y, x = divmod(i, width)
                out = [outputs[4][c][y*pitches[c]+x] for c in range(3)]
                tetra_error = max(tetra_error, max(abs(a-b) for a, b in zip(out, reference(cube, rgb))))
            if name.endswith('.cube'):
                assert tetra_error <= 3, (name, kind, tetra_error)
            c16_error = 0
            for i, rgb in enumerate(pixels):
                y, x = divmod(i, width)
                out = [outputs[5][c][y*pitches[c]+x] for c in range(3)]
                c16_error = max(c16_error, max(abs(a-b) for a, b in zip(out, reference(cube, rgb))))
            assert c16_error <= 1, (name, kind, 'compact16', c16_error)
            blk_error = 0
            for i, rgb in enumerate(pixels):
                y, x = divmod(i, width)
                out = [outputs[6][c][y*pitches[c]+x] for c in range(3)]
                blk_error = max(blk_error, max(abs(a-b) for a, b in zip(out, reference(cube, rgb))))
            # blocks assume a smooth cube (local slope ~1); the random cube is
            # only a stress input, so the bound applies to the vendor cubes.
            if name.endswith('.cube'):
                assert blk_error <= 2, (name, kind, 'blocks', blk_error)
            report.setdefault(name, {})[kind] = {
                'max_error_vs_float': error, 'max_diff_vs_dense': diff,
                'dense_instructions_per_pixel': round(counts[0]/(width*height), 2),
                'compact_instructions_per_pixel': round(counts[1]/(width*height), 2),
                'compact2_instructions_per_pixel': round(counts[2]/(width*height), 2),
                'compact_pf_instructions_per_pixel': round(counts[3]/(width*height), 2),
                'tetra_instructions_per_pixel': round(counts[4]/(width*height), 2),
                'tetra_max_error_vs_float': tetra_error,
                'compact16_instructions_per_pixel': round(counts[5]/(width*height), 2),
                'compact16_max_error_vs_float': c16_error,
                'blocks_instructions_per_pixel': round(counts[6]/(width*height), 2),
                'blocks_max_error_vs_float': blk_error}
    result = {'library': str(path), 'table_bytes': {'dense': dense_size, 'compact': compact_size},
              'cubes': report,
              'limitation': 'Executed ARM64 instructions in Unicorn, not A53 cycles; cache and '
                            'TLB misses of the dense table are not modelled.'}
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('library')
    p.add_argument('--bin', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    r = test(a.library, a.bin)
    if a.output:
        a.output.write_text(json.dumps(r, indent=2) + '\n')
