"""Verify OSD loader metadata, ARM64 hooks, labels and untouched code."""
import csv
import io
from pathlib import Path
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'firmware'))
from compare_modes import patch_osd, patch_labels, GET_CALLS, SET_CALL, NATIVE_SET
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (
    UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X19, UC_ARM64_REG_LR, UC_ARM64_REG_PC)


def test():
    base = (ROOT/'outputs/xreal-pilot2-real3d-cpu/XREAL-Pilot2-RU-Hebe-Real3D-CPU.bin').read_bytes()
    with zipfile.ZipFile(io.BytesIO(base[64:].translate(bytes(i^0x66 for i in range(256))))) as z:
        original = z.read('pilot/bin/libnr_osd.so')
        labels = z.read('pilot/resource/osd_language.csv')
    patched = patch_osd(original)
    assert patched == patch_osd(original), 'OSD patch must be deterministic'
    try:
        patch_osd(original[:-1])
        raise AssertionError('Changed vendor input accepted')
    except ValueError:
        pass
    before,after = [ELFFile(io.BytesIO(b)) for b in (original,patched)]
    text = bytearray(before.get_section_by_name('.text').data())
    actual = after.get_section_by_name('.text').data()
    text_va = before.get_section_by_name('.text')['sh_addr']
    for pc in (*GET_CALLS,SET_CALL,0xf67c0,0xf42dc,0xf432c):
        pos = pc-text_va
        text[pos:pos+4] = actual[pos:pos+4]
    assert bytes(text) == actual
    assert after.get_section_by_name('.rela.dyn').data()[:-48] == before.get_section_by_name('.rela.dyn').data()
    dyn = after.get_section_by_name('.dynamic')
    expected_sections = {
        'DT_HASH':'.hash', 'DT_SYMTAB':'.dynsym', 'DT_STRTAB':'.dynstr',
        'DT_RELA':'.rela.dyn', 'DT_VERSYM':'.gnu.version',
    }
    tags = {t.entry.d_tag:t for t in dyn.iter_tags()}
    for tag,name in expected_sections.items():
        assert tags[tag].entry.d_ptr == after.get_section_by_name(name)['sh_addr']
    assert tags['DT_RELASZ'].entry.d_val == after.get_section_by_name('.rela.dyn')['sh_size']
    assert tags['DT_STRSZ'].entry.d_val == after.get_section_by_name('.dynstr')['sh_size']
    assert any(t.entry.d_tag == 'DT_NEEDED' and t.needed == 'libxreal_cpu_adapter.so'
               for t in dyn.iter_tags())
    symbols = after.get_section_by_name('.dynsym')
    count = symbols.num_symbols()
    assert count == before.get_section_by_name('.dynsym').num_symbols()+2
    raw_hash = after.get_section_by_name('.hash').data()
    words = struct.unpack('<'+'I'*(len(raw_hash)//4),raw_hash)
    bucket_count,chain_count = words[:2]
    buckets,chains = words[2:2+bucket_count],words[2+bucket_count:]
    assert chain_count == count
    for symbol in symbols.iter_symbols():
        if not symbol.name:
            continue
        value = 0
        for byte in symbol.name.encode():
            value = ((value << 4)+byte) & 0xffffffff
            high = value & 0xf0000000
            value ^= high >> 24
            value &= ~high
        index = buckets[value % bucket_count]
        for _ in range(chain_count):
            assert index, 'Dynamic symbol missing from SysV lookup'
            if symbols.get_symbol(index).name == symbol.name:
                break
            index = chains[index]
        else:
            raise AssertionError('Cyclic symbol hash chain')
    relocs = list(after.get_section_by_name('.rela.dyn').iter_relocations())[-2:]
    for index,r in enumerate(relocs):
        assert r['r_info_type'] == 257 and r['r_addend'] == 0
        assert r['r_info_sym'] == count-2+index
        assert symbols.get_symbol(r['r_info_sym']).name == ('xreal_compare_get','xreal_compare_set')[index]
    # Every original LOAD remains byte-for-byte at the same VA, except the
    # note/code hooks and PHDR/section-header metadata required above.
    for seg in before.iter_segments():
        if seg['p_type'] == 'PT_LOAD':
            assert any(seg.header == candidate.header for candidate in after.iter_segments())
    for seg in after.iter_segments():
        assert seg['p_flags'] & 3 != 3, 'No writable/executable segment'
    # Execute the real call-site BL and the new stub at two ASLR bases.
    for load_base in (0x10000000,0x70000000):
        for pc in (*GET_CALLS,SET_CALL):
            u = Uc(UC_ARCH_ARM64,UC_MODE_ARM)
            highest = max(s['p_vaddr']+s['p_memsz'] for s in after.iter_segments()
                          if s['p_type']=='PT_LOAD')
            u.mem_map(load_base,(highest+4095)&~4095)
            for seg in after.iter_segments():
                if seg['p_type']=='PT_LOAD':
                    u.mem_write(load_base+seg['p_vaddr'],seg.data())
            target = load_base+highest-4
            u.mem_write(load_base+relocs[0]['r_offset'],struct.pack('<Q',target))
            u.mem_write(load_base+relocs[1]['r_offset'],struct.pack('<Q',target))
            u.reg_write(UC_ARM64_REG_X0,0x12340000)
            u.reg_write(UC_ARM64_REG_X19,0x12345678)
            observed = []
            def called(uc,address,size,unused):
                observed.append((uc.reg_read(UC_ARM64_REG_X0),uc.reg_read(UC_ARM64_REG_X1)))
                uc.reg_write(UC_ARM64_REG_PC,uc.reg_read(UC_ARM64_REG_LR))
            u.hook_add(UC_HOOK_CODE,called,begin=target,end=target)
            u.emu_start(load_base+pc,load_base+pc+4,count=30)
            assert len(observed)==1 and observed[0][0]==0x12340000
            if pc == SET_CALL:
                assert observed[0][1] == load_base+NATIVE_SET
            assert u.reg_read(UC_ARM64_REG_X19)==0x12345678
    rows = list(csv.reader(io.StringIO(patch_labels(labels).decode('utf-8-sig'))))
    selected = {r[1]:r for r in rows[4:] if len(r)>17}
    assert selected['SoftDepth'][13]=='One Pro (mesh)'
    assert selected['BalancedDepth'][13]=='Pro+ (CPU-порт)'
    assert selected['Mode3dEffectSetting'][4]=='Real3D algorithm'
    print('OSD: deterministic ELF, preserved mappings/relocations, five ARM64 hooks '
          'at two ASLR bases, two choices and RU/EN labels passed.')


if __name__ == '__main__':
    test()
