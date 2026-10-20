"""Run the shipped ARM64 table builder and vector/tail loops against an oracle."""
import io
from pathlib import Path
import random
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "work/elfdeps"))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import *
from test_lut import reference


def test(path):
    base, data, stack, stop = 0x100000, 0x2000000, 0x3000000, 0x3010000
    u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    u.mem_map(base, 0x400000)
    u.mem_map(data, 0x800000)
    u.mem_map(stack, 0x20000)
    elf = ELFFile(io.BytesIO(Path(path).read_bytes()))
    for seg in elf.iter_segments():
        if seg["p_type"] == "PT_LOAD":
            u.mem_write(base + seg["p_vaddr"], seg.data())
    symbols = {s.name: base + s["st_value"]
               for s in elf.get_section_by_name(".symtab").iter_symbols()}
    regs = [UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
            UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5,
            UC_ARM64_REG_X6, UC_ARM64_REG_X7]
    sp = stack + 0x8000

    def call(name, *args):
        for reg, value in zip(regs, args):
            u.reg_write(reg, value)
        for i, value in enumerate(args[8:]):
            u.mem_write(sp + i * 8, struct.pack("<Q", value))
        u.reg_write(UC_ARM64_REG_SP, sp)
        u.reg_write(UC_ARM64_REG_LR, stop)
        u.emu_start(symbols[name], stop, count=400000000)
        assert u.reg_read(UC_ARM64_REG_PC) == stop, name
        return u.reg_read(UC_ARM64_REG_X0)

    cube_ptr, table = data, data + 0x10000
    size = call("xreal_rgb_table_bytes")
    assert size == (17 * 256 * 256 + 256) * 4
    guard = bytes([213]) * 64
    rng = random.Random(20261021)
    cubes = {
        "identity": [round(v*65280/16) for b in range(17) for g in range(17)
                     for r in range(17) for v in (r,g,b)],
        "random": [rng.randrange(256)*256 for _ in range(14739)],
    }
    errors = {}
    for name, cube in cubes.items():
        u.mem_write(cube_ptr, struct.pack("<14739H", *cube))
        u.mem_write(table-64, guard)
        u.mem_write(table+size, guard)
        # Match the real four-worker partition, including mapping-table setup.
        for i in range(4):
            assert call("xreal_build_rgb_table_range", cube_ptr, table,
                        17*i//4, 17*(i+1)//4) == 0
        assert bytes(u.mem_read(table-64, 64)) == guard
        assert bytes(u.mem_read(table+size, 64)) == guard
        maximum = 0
        for width in (1, 3, 4, 5, 17, 257):
            height = 3
            pitches = [width+7, width+13, width+21]
            planes = [data+0x600000+i*0x10000 for i in range(3)]
            originals = [bytearray([171])*(s*height) for s in pitches]
            pixels = [tuple(rng.randrange(256) for _ in range(3))
                      for _ in range(width*height)]
            pixels[0] = (255, 255, 255)
            pixels[-1] = (0, 0, 0)
            for i, rgb in enumerate(pixels):
                y, x = divmod(i, width)
                for c in range(3):
                    originals[c][y*pitches[c]+x] = rgb[c]
            for ptr, raw in zip(planes, originals):
                u.mem_write(ptr, bytes(raw))
            assert call("xreal_full_rgb_lut", table, *planes, *pitches,
                        width, height) == 0
            actual = [bytes(u.mem_read(ptr, s*height))
                      for ptr, s in zip(planes, pitches)]
            for i, rgb in enumerate(pixels):
                y, x = divmod(i, width)
                out = [actual[c][y*pitches[c]+x] for c in range(3)]
                maximum = max(maximum, max(abs(a-b) for a,b in
                                           zip(out, reference(cube, rgb))))
                if name == "identity":
                    assert out == list(rgb), (rgb, out)
            for c, pitch in enumerate(pitches):
                for y in range(height):
                    assert actual[c][y*pitch+width:(y+1)*pitch] == bytes([171])*(pitch-width)
        assert maximum <= 1, (name, maximum)
        errors[name] = maximum
    print("Shipped ARM64 LUT: four build partitions, guards, vector/scalar tails, "
          "unequal pitches and independent oracle passed:", errors)


if __name__ == "__main__":
    test(sys.argv[1])
