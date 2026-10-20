"""Execute the unchanged vendor ARM64 mesh, with independent stereo oracles.

This tests the actual closed kernel, not a Python reimplementation of it.
TLS and memcpy imports are supplied by the emulator; no device writes.
"""
import io
from pathlib import Path
import random
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'work/elfdeps'))
sys.path.insert(0, str(ROOT/'firmware'))
from compare_modes import checked, MESH_SHA
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm64_const import (
    UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
    UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_LR, UC_ARM64_REG_TPIDR_EL0)

B, DATA, TLS, STACK, STOP = 0x100000, 0x400000, 0x800000, 0x900000, 0xaffffc
VENDOR = ROOT/'work/3d-lab/vendor-compare/onepro-libnr_depth_manager.so'


def run(depth, width=1920, height=12, ss=(2048,1024,1024),
        ds=(1920,1024,1024), initialize_tls=False, boundaries=None):
    blob = checked(VENDOR.read_bytes(), MESH_SHA)
    elf = ELFFile(io.BytesIO(blob))
    machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    machine.mem_map(B, 0x200000)
    machine.mem_map(DATA, 0x700000)
    for seg in elf.iter_segments():
        if seg['p_type'] == 'PT_LOAD':
            machine.mem_write(B+seg['p_vaddr'], seg.data())
    src = [DATA+i*0x10000 for i in range(3)]
    dst = [DATA+(i+3)*0x10000 for i in range(3)]
    originals, ranges = [], []
    for p in range(3):
        w, h = (width, height) if p == 0 else (width//2,height//2)
        data = bytearray([0xed]) * (ss[p]*h)
        for y in range(h):
            data[y*ss[p]:y*ss[p]+w] = bytes((x+17*y+31*p)%251 for x in range(w))
        originals.append(bytes(data))
        machine.mem_write(src[p], bytes(data))
        machine.mem_write(dst[p], bytes([0xed])*(ds[p]*h))
        ranges.extend((ptr+y*stride, ptr+y*stride+w)
                      for ptr,stride in ((src[p],ss[p]),(dst[p],ds[p]))
                      for y in range(h))
    mat, z, arg = DATA+0x70000, DATA+0x80000, DATA+0x90000
    assert len(depth) == width//4*height//4
    machine.mem_write(z, depth)
    machine.mem_write(mat, struct.pack('<IIII',0x42ff4000,2,height//4,width//4))
    machine.mem_write(mat+16, struct.pack('<Q',z))
    machine.mem_write(mat+72, struct.pack('<QQQ',mat+80,width//4,1))
    a = bytearray(120)
    struct.pack_into('<Qii',a,0,mat,0,height)
    for pos,ptr,stride in [(16,src[0],ss[0]),(32,src[1],ss[1]),(48,src[2],ss[2]),
                           (64,dst[0],ds[0]),(80,dst[1],ds[1]),(96,dst[2],ds[2])]:
        struct.pack_into('<Qi',a,pos,ptr,stride)
    struct.pack_into('<fi',a,108,float(max(depth)),width)
    machine.mem_write(TLS+0x100,struct.pack('<Q',0 if initialize_tls else 1))
    machine.mem_write(TLS+0x200,struct.pack('<QQQ',TLS+0x400,TLS+0x400+width//4,TLS+0x400+width//4))
    machine.mem_write(B+0x1ed690,struct.pack('<Q',STOP-16))
    machine.mem_write(B+0x1ed6a0,struct.pack('<Q',STOP-12))
    machine.mem_write(B+0x1ee2c0,struct.pack('<256I',0,*[262144//i for i in range(1,256)]))
    calls = []
    def imports(uc, address, size, unused):
        x0,x1,x2 = [uc.reg_read(r) for r in (UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2)]
        if address in (STOP-16,STOP-12):
            uc.reg_write(UC_ARM64_REG_X0,0x100 if address == STOP-16 else 0x200)
        elif address == B+0x1b420:
            assert any(a <= x0 and x0+x2 <= b for a,b in ranges), (
                'memcpy writes beyond visible plane',hex(x0),x2)
            uc.mem_write(x0,bytes(uc.mem_read(x1,x2)))
        elif address == B+0x1ba40:
            assert x0 == width//4
            uc.reg_write(UC_ARM64_REG_X0,TLS+0x400)
            calls.append('allocate')
        elif address == B+0x1b6d0:
            uc.mem_write(x0,bytes([x1 & 255])*x2)
        elif address == B+0x1bfc0:
            uc.reg_write(UC_ARM64_REG_X0,0)
        uc.reg_write(UC_ARM64_REG_PC,uc.reg_read(UC_ARM64_REG_LR))
    for address in (STOP-16,STOP-12,B+0x1b420,B+0x1ba40,B+0x1b6d0,B+0x1bfc0):
        machine.hook_add(UC_HOOK_CODE,imports,begin=address,end=address)
    def bounded_write(uc, access, address, size, value, unused):
        if DATA <= address < DATA+0x70000:
            assert any(a <= address and address+size <= b for a,b in ranges), (
                hex(uc.reg_read(UC_ARM64_REG_PC)),hex(address),size)
    machine.hook_add(UC_HOOK_MEM_WRITE,bounded_write,begin=DATA,end=DATA+0x70000-1)
    for begin,end in boundaries or [(0,height)]:
        struct.pack_into('<ii',a,8,begin,end)
        machine.mem_write(arg,bytes(a))
        machine.reg_write(UC_ARM64_REG_TPIDR_EL0,TLS)
        machine.reg_write(UC_ARM64_REG_SP,STACK)
        machine.reg_write(UC_ARM64_REG_LR,STOP)
        machine.reg_write(UC_ARM64_REG_X0,arg)
        machine.emu_start(B+0x38150,STOP,count=30000000)
        assert machine.reg_read(UC_ARM64_REG_PC) == STOP
    if initialize_tls:
        assert calls == ['allocate']
    assert bytes(machine.mem_read(z,len(depth))) == depth
    return originals, [bytes(machine.mem_read(src[p],ss[p]*(height if p==0 else height//2))) for p in range(3)], [
        bytes(machine.mem_read(dst[p],ds[p]*(height if p==0 else height//2))) for p in range(3)]


def test():
    width,height = 1920,12
    for displacement in (0,4,16,63):
        original,left,right = run(bytes([displacement])*(480*3), initialize_tls=True)
        for p in range(3):
            w,h = (width,height) if p==0 else (width//2,height//2)
            ss,ds = (2048,1920) if p==0 else (1024,1024)
            # Even displacements have an exact independent interior oracle.
            if displacement % 2 == 0:
                shift = displacement if p==0 else displacement//2
                for y in range(h):
                    for x in range(160,w-160):
                        assert left[p][y*ss+x] == original[p][y*ss+x+shift]
                        assert right[p][y*ds+x] == original[p][y*ss+x-shift]
        if displacement:
            assert left[0] != original[0]
    rng = random.Random(184)
    maps = [
        bytes((i%480)*63//479 for i in range(1440)),
        bytes(63 if (i//16)%2 else 0 for i in range(1440)),
        bytes(rng.randrange(64) for _ in range(1440)),
    ]
    for depth in maps:
        sequential = run(depth)
        segmented = run(depth,boundaries=[(0,4),(4,8),(8,12)])
        assert sequential == segmented, 'Segment boundary changes vendor output'
    print('Vendor mesh: independent left/right oracles, TLS initialization, '
          'write bounds, chroma and segmented execution passed (10 calls).')


if __name__ == '__main__':
    test()
