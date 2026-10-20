"""Compare the optimized ARM64 warp with the original pixel loop."""

import io
import os
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "work/elfdeps"))
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import (
    UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0,
    UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
    UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6,
    UC_ARM64_REG_X7,
)

blob = Path(os.environ.get("REAL3D_LIB", Path(__file__).resolve().parents[2] / "outputs/v0.7.0-rc1/libxreal_cpu_adapter.so")).read_bytes()
elf = ELFFile(io.BytesIO(blob))
symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".dynsym").iter_symbols()}
BASE, SRC, DST, DEPTH, STACK, STOP = 0x100000, 0x400000, 0x500000, 0x600000, 0x700000, 0x7FFFFC


def run(symbol, source, depth, width, height, stride, depth_width, depth_height,
        begin=0, end=0):
    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    machine.mem_map(BASE, 0x200000)
    for segment in elf.iter_segments():
        if segment["p_type"] == "PT_LOAD":
            machine.mem_write(BASE + segment["p_vaddr"], segment.data())
    machine.mem_map(SRC, 0x400000)
    machine.mem_write(SRC, source)
    machine.mem_write(DST, bytes([0xEE]) * (stride * height))
    machine.mem_write(DEPTH, depth)
    if symbol == "real3d_warp_plane_for_test":
        machine.mem_write(STACK, struct.pack("<QQQ", depth_width, depth_height, 0))
    elif symbol == "real3d_warp_cells_range_for_test":
        machine.mem_write(STACK, struct.pack("<QQQ", depth_height, begin, end))
    else:
        machine.mem_write(STACK, struct.pack("<Q", depth_height))
    for reg, value in zip(
        (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
         UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7),
        (SRC, DST, DEPTH, width, height, stride, stride, depth_width),
    ):
        machine.reg_write(reg, value)
    machine.reg_write(UC_ARM64_REG_SP, STACK)
    machine.reg_write(UC_ARM64_REG_LR, STOP)
    try:
        machine.emu_start(BASE + symbols[symbol], STOP, count=10000000)
    except UcError as error:
        raise AssertionError(f"{symbol}: PC={machine.reg_read(UC_ARM64_REG_PC):x}") from error
    assert machine.reg_read(UC_ARM64_REG_X0) == 0
    return bytes(machine.mem_read(DST, stride * height))
