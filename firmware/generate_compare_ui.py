"""Relocate one hash-pinned vendor LVGL row constructor, not arbitrary code.

The straight-line slice uses x19..x24 for widget storage/styles. Calls and
ADRP instructions are rebased; every other instruction stays unchanged.
"""
import io
from compare_modes import checked, OSD_SHA
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN


def generate(data):
    checked(data, OSD_SHA)
    elf = ELFFile(io.BytesIO(data))
    sec = elf.get_section_by_name('.text')
    begin, end = 0x53c94, 0x540ec
    block = sec.data()[begin-sec['sh_addr']:end-sec['sh_addr']]
    out = ['.text', '.p2align 2', '.global xreal_clone_row',
           '.hidden xreal_clone_row', '.type xreal_clone_row,%function',
           'xreal_clone_row:', 'stp x29,x30,[sp,#-80]!',
           'stp x19,x20,[sp,#16]', 'stp x21,x22,[sp,#32]',
           'stp x23,x24,[sp,#48]', 'str x28,[sp,#64]',
           'mov x19,x0', 'mov x20,x1', 'mov x21,x2', 'mov x22,x3',
           'mov x23,x4', 'mov x24,x5', 'mov x28,x6']
    decoded = list(Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN).disasm(block, begin))
    assert len(decoded)*4 == len(block)
    for ins in decoded:
        if ins.mnemonic in ('bl', 'adrp'):
            args = ins.op_str.split(', ')
            reg = 'x16' if ins.mnemonic == 'bl' else args[0]
            address = int(args[-1].lstrip('#'), 0)
            out += [f'movz {reg},#{address & 65535}',
                    f'movk {reg},#{address >> 16},lsl #16',
                    f'add {reg},x28,{reg}']
            if ins.mnemonic == 'bl':
                out.append('blr x16')
        else:
            assert ins.mnemonic in ('ldr', 'str', 'mov', 'add'), str(ins)
            out.append(f'.inst 0x{int.from_bytes(ins.bytes, "little"):08x}')
    out += ['ldp x19,x20,[sp,#16]', 'ldp x21,x22,[sp,#32]',
            'ldp x23,x24,[sp,#48]', 'ldr x28,[sp,#64]',
            'ldp x29,x30,[sp],#80', 'ret',
            '.size xreal_clone_row,.-xreal_clone_row',
            '.section .note.GNU-stack,"",%progbits']
    return '\n'.join(out)+'\n'
