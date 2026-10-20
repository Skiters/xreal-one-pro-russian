"""Run the compiled bridge and original mesh together on a full ARM64 frame."""
import io
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'work/elfdeps'))
sys.path.insert(0,str(ROOT/'firmware'))
from compare_modes import checked, MESH_SHA
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (
    UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_X3,
    UC_ARM64_REG_X4,UC_ARM64_REG_X5,UC_ARM64_REG_X6,UC_ARM64_REG_X7,
    UC_ARM64_REG_SP,UC_ARM64_REG_LR,UC_ARM64_REG_PC,UC_ARM64_REG_TPIDR_EL0)


def test(path):
    A,V,D,T,S,STOP,IMPORTS=0x100000,0x1000000,0x4000000,0x8000000,0x9000000,0x9100000,0x9200000
    u=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
    u.mem_map(A,0x400000);u.mem_map(V,0x300000)
    u.mem_map(D,0x4000000);u.mem_map(T,0x100000)
    u.mem_map(S,0x300000)
    vendor=ELFFile(io.BytesIO(checked(
        (ROOT/'work/3d-lab/vendor-compare/onepro-libnr_depth_manager.so').read_bytes(),MESH_SHA)))
    adapter=ELFFile(io.BytesIO(Path(path).read_bytes()))
    for base,elf in ((A,adapter),(V,vendor)):
        for seg in elf.iter_segments():
            if seg['p_type']=='PT_LOAD':u.mem_write(base+seg['p_vaddr'],seg.data())
    sym=adapter.get_section_by_name('.dynsym')
    exports={s.name:A+s['st_value'] for s in sym.iter_symbols() if s['st_value']}
    imports={}
    def external(name):
        if name not in imports:imports[name]=IMPORTS+len(imports)*4
        return imports[name]
    for name in ('.rela.dyn','.rela.plt'):
        section=adapter.get_section_by_name(name)
        if not section:continue
        for r in section.iter_relocations():
            if r['r_info_type']==1027:value=A+r['r_addend']
            elif r['r_info_type'] in (1025,1026,257):
                n=sym.get_symbol(r['r_info_sym']).name
                value=exports.get(n) or external(n)
                value+=r['r_addend']
            else:raise AssertionError(r.entry)
            u.mem_write(A+r['r_offset'],struct.pack('<Q',value))
    resolver1,resolver2=IMPORTS+0x1000,IMPORTS+0x1004
    u.mem_write(V+0x1ed690,struct.pack('<Q',resolver1))
    u.mem_write(V+0x1ed6a0,struct.pack('<Q',resolver2))
    u.mem_write(T+0x100,struct.pack('<Q',1))
    u.mem_write(T+0x200,struct.pack('<QQQ',T+0x400,T+0x400+480,T+0x400+480))
    src=[D+i*0x400000 for i in range(3)]
    dst=[D+(i+3)*0x400000 for i in range(3)]
    widths=(1920,960,960);heights=(1080,540,540);ss=(2048,1024,1024);ds=(1920,1024,1024)
    originals=[]
    for p in range(3):
        plane=bytearray([0xed])*(ss[p]*heights[p])
        for y in range(heights[p]):
            plane[y*ss[p]:y*ss[p]+widths[p]]=bytes((x+17*y+31*p)%251 for x in range(widths[p]))
        originals.append(bytes(plane));u.mem_write(src[p],bytes(plane))
        u.mem_write(dst[p],bytes([0xed])*(ds[p]*heights[p]))
    args=D+0x2000000;depth=args+0x1000
    u.mem_write(args,struct.pack('<6Q6I',*src,*dst,*ss,*ds))
    u.mem_write(depth,bytes([32])*(480*270))  # Q3 = four pixels per eye.
    pending=[];seen=[]
    def hook(uc,address,size,unused):
        x0,x1,x2,x3=[uc.reg_read(r) for r in (UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_X3)]
        if address==exports['xreal_parallel_run']:
            pending.append((x0,x1,x2,x3));uc.emu_stop();return
        if address==resolver1: result=0x100
        elif address==resolver2:result=0x200
        elif address==V+0x1b420:
            uc.mem_write(x0,bytes(uc.mem_read(x1,x2)));result=x0
        else:
            name=next(k for k,v in imports.items() if v==address)
            if name=='dlopen':
                assert bytes(uc.mem_read(x0,100)).split(b'\0')[0]==b'/usrdata/pilot/bin/libxreal_onepro_mesh.so'
                result=123;seen.append(name)
            elif name=='dlsym':
                assert x0==123 and bytes(uc.mem_read(x1,32)).split(b'\0')[0]==b'NRPluginLoad'
                result=V+0x1d9d0
            elif name=='memcmp':
                result=0 if bytes(uc.mem_read(x0,x2))==bytes(uc.mem_read(x1,x2)) else 1
            else:raise AssertionError('Unexpected bridge import '+name)
        uc.reg_write(UC_ARM64_REG_X0,result)
        uc.reg_write(UC_ARM64_REG_PC,uc.reg_read(UC_ARM64_REG_LR))
    for address in [*imports.values(),resolver1,resolver2,V+0x1b420,exports['xreal_parallel_run']]:
        u.hook_add(UC_HOOK_CODE,hook,begin=address,end=address)
    for reg,value in zip((UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,
                          UC_ARM64_REG_X3,UC_ARM64_REG_X4,UC_ARM64_REG_X5),
                         (args,args+24,args+48,args+60,depth,3)):
        u.reg_write(reg,value)
    u.reg_write(UC_ARM64_REG_SP,S+0x80000)
    u.reg_write(UC_ARM64_REG_LR,STOP)
    u.reg_write(UC_ARM64_REG_TPIDR_EL0,T)
    u.emu_start(exports['xreal_onepro_warp'],STOP,count=10000000)
    assert len(pending)==1
    context=u.context_save()
    worker,jobs,stride,count=pending[0]
    assert stride==120 and count==4
    for i in range(count):
        u.reg_write(UC_ARM64_REG_X0,jobs+i*stride)
        u.reg_write(UC_ARM64_REG_SP,S+0x40000)
        u.reg_write(UC_ARM64_REG_LR,STOP)
        u.emu_start(worker,STOP,count=300000000)
        assert u.reg_read(UC_ARM64_REG_PC)==STOP
    u.context_restore(context)
    u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR))
    u.emu_start(u.reg_read(UC_ARM64_REG_PC),STOP,count=1000000)
    assert u.reg_read(UC_ARM64_REG_PC)==STOP and u.reg_read(UC_ARM64_REG_X0)==0
    assert seen==['dlopen']
    for p in range(3):
        left=bytes(u.mem_read(src[p],ss[p]*heights[p]))
        right=bytes(u.mem_read(dst[p],ds[p]*heights[p]))
        shift=4 if p==0 else 2
        for y in range(heights[p]):
            assert left[y*ss[p]+160:y*ss[p]+widths[p]-160]==originals[p][y*ss[p]+160+shift:y*ss[p]+widths[p]-160+shift]
            assert right[y*ds[p]+160:y*ds[p]+widths[p]-160]==originals[p][y*ss[p]+160-shift:y*ss[p]+widths[p]-160-shift]
            assert left[y*ss[p]+widths[p]:(y+1)*ss[p]]==bytes([0xed])*(ss[p]-widths[p])
            assert right[y*ds[p]+widths[p]:(y+1)*ds[p]]==bytes([0xed])*(ds[p]-widths[p])
    print('Full 1920x1080 bridge + original ARM64 kernel: loader fingerprint, Q3 conversion, '
          'four jobs, both eye/chroma oracles and padding passed.')


if __name__=='__main__':
    test(sys.argv[1])
