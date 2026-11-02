"""Execute the shipped ARM64 UI bridge; model only external OSD/LVGL services.

Checks native row-constructor call equivalence, five-item navigation, callbacks,
highlight, ASLR thunk context, strength independence and registration lifetime.
This is not a physical display test.
"""
import io
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'work/elfdeps'))
sys.path.insert(0,str(ROOT/'firmware'))
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import *
from compare_modes import patch_osd, HOOKS


def test(path):
    A,O,D,S,I,STOP=0x100000,0x10000000,0x20000000,0x30000000,0x31000000,0x31010000
    u=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
    for address,size in ((A,0x400000),(O,0x3000000),(D,0x200000),(S,0x20000),(I,0x20000)):
        u.mem_map(address,size)
    raw=(ROOT/'outputs/real3d-dual/base-osd.so').read_bytes()
    vendor=ELFFile(io.BytesIO(patch_osd(raw)))
    adapter=ELFFile(io.BytesIO(Path(path).read_bytes()))
    for base,elf in ((A,adapter),(O,vendor)):
        for seg in elf.iter_segments():
            if seg['p_type']=='PT_LOAD':u.mem_write(base+seg['p_vaddr'],seg.data())
    dyn=adapter.get_section_by_name('.dynsym')
    symbols={s.name:A+s['st_value'] for s in adapter.get_section_by_name('.symtab').iter_symbols()
             if s['st_value']}
    imports={}
    for name in ('.rela.dyn','.rela.plt'):
        sec=adapter.get_section_by_name(name)
        if not sec:continue
        for r in sec.iter_relocations():
            if r['r_info_type']==1027:target=A+r['r_addend']
            else:
                name=dyn.get_symbol(r['r_info_sym']).name
                if name not in symbols:imports.setdefault(name,I+len(imports)*4)
                target=(symbols.get(name) or imports[name])+r['r_addend']
            u.mem_write(A+r['r_offset'],struct.pack('<Q',target))
    reloc=list(vendor.get_section_by_name('.rela.dyn').iter_relocations())[-2]
    u.mem_write(O+reloc['r_offset'],struct.pack('<Q',symbols['xreal_ui_dispatch']))
    regs=[getattr(sys.modules[__name__],f'UC_ARM64_REG_X{i}') for i in range(29)]
    regs += [UC_ARM64_REG_FP,UC_ARM64_REG_LR]
    def readq(p):return struct.unpack('<Q',u.mem_read(p,8))[0]
    def writeq(p,v):u.mem_write(p,struct.pack('<Q',v))
    def readi(p):return struct.unpack('<I',u.mem_read(p,4))[0]
    def text(p):return bytes(u.mem_read(p,200)).split(b'\0')[0]
    heap=D+0x100000
    def alloc(data):
        nonlocal heap
        at=heap;heap+=(len(data)+15)&~15
        u.mem_write(at,data);return at
    ui=D+0x1000; listp=O+0x238bf50; vec=D+0x10000
    writeq(listp+16,vec);writeq(listp+24,vec)
    writeq(ui+0x268,D+0x800)
    tabview=D+0x900;writeq(ui+0x270,tabview)
    u.mem_write(tabview+0x48,struct.pack('<H',4))
    count=0; selected=2; depth=4; active=0; registered=[]; trace=[]; native_open=[]
    positions={}; sizes={}; flags={}; long_modes={}; registration_snapshots=[]
    native_registration=False
    def ret(value=0):
        u.reg_write(UC_ARM64_REG_X0,value)
        u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR))
    textsec=vendor.get_section_by_name('.text')
    block=textsec.data()[0x53c94-textsec['sh_addr']:0x540ec-textsec['sh_addr']]
    calls={int(ins.op_str[1:],0) for ins in Cs(CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN).disasm(block,0x53c94)
           if ins.mnemonic=='bl'}
    calls.discard(0x200)
    calls |= {0x26e2a8,0xadb48,0xf3070,0xb4900,0x10c338,0x10cc28,0x262008,0x261ee8,
              0x10a5f8,0xf5b20}
    calls |= {0x27b688,0x24490,0x24440,0x24630,0x23e10,0x26ffd0,0x264000,0x264130,0x26f8f8,0x2673d8,0xa2908,0x87450}
    def hook(uc,address,size,_):
        nonlocal count,selected,active,registered
        args=[uc.reg_read(r) for r in regs[:4]]
        a,b,c,d=args
        offset=address-O
        if address==symbols['xreal_compare_algorithm']:ret(selected);return
        if address==symbols['xreal_compare_set']:
            assert a in (1,2);selected=a;ret();return
        if address in imports.values():
            name=next(n for n,p in imports.items() if p==address)
            assert name=='memcpy', name
            uc.mem_write(a,bytes(uc.mem_read(b,c)));ret(a);return
        if offset==0x10cc28 and native_registration:return
        if offset==0x24490:ret(alloc(bytes(a)));return
        if offset==0x24440:ret();return
        if offset in (0x24630,0x23e10):
            uc.mem_write(a,bytes(uc.mem_read(b,c)));ret(a);return
        if offset==0x26ffd0:ret(D+0x800);return
        if offset in (0x264000,0x264130):ret(positions[a][offset==0x264130]);return
        if offset==0x87450:raise AssertionError('Native registration rejected a row')
        if offset==0x27b688:
            assert a==tabview and text(b)==b'        '
            count_tabs=struct.unpack('<H',uc.mem_read(a+0x48,2))[0]
            uc.mem_write(a+0x48,struct.pack('<H',count_tabs+1))
            ret(D+0xb00);return
        trace.append((offset,args))
        if offset in (0x261db0,0x28db28,0x28c8b0):
            count+=1;ret(D+0x20000+count*256);return
        if offset in (0xf3070,0xadb48):
            data=text(b)
            writeq(a,alloc(data+b'\0'));writeq(a+8,len(data))
        elif offset==0xb4900:
            assert text(readq(a))==b'Real3DTips'
            ret(alloc(b'Depth settings remain separate\0'));return
        elif offset==0x10c338:
            end=readq(a+24);uc.mem_write(end,bytes(uc.mem_read(b,0x120)))
            writeq(a+24,end+0x120)
        elif offset==0x10cc28:
            begin,end=readq(b),readq(b+8)
            registered=[readq(p) for p in range(begin,end,8)]
            # Native 10cf50..10cf6c snapshots the source positions, then
            # 10d074..10d07c restores them and rebuilds the visible list.
            registration_snapshots.append([positions[readq(item+0x38)] for item in registered])
            visible=[item for item in registered if not flags.get(readq(item+0x38),0)&1]
            for idx,item in enumerate(visible):
                uc.mem_write(vec+idx*0x120,bytes(uc.mem_read(item,0x120)))
            writeq(listp+24,vec+len(visible)*0x120)
        elif offset==0x263880:positions[a]=(b,c)
        elif offset==0x263a30:sizes[a]=(b,c)
        elif offset==0x10a5f8:active=b
        elif offset==0xf5b20:
            assert readi(a)==3;native_open.append(selected)
        elif offset==0x261de8:flags[a]=flags.get(a,0)|b
        elif offset==0x261ee8:flags[a]=flags.get(a,0)&~b
        elif offset==0x28eda0:long_modes[a]=b
        elif offset==0x262008:ret(bool(flags.get(a,0)&b));return
        ret()
    for address in [O+c for c in calls]+list(imports.values())+[
            symbols['xreal_compare_algorithm'],symbols['xreal_compare_set']]:
        u.hook_add(UC_HOOK_CODE,hook,begin=address,end=address)
    def run(entry,args=(),stop=STOP):
        for reg,value in zip(regs,args):u.reg_write(reg,value)
        u.reg_write(UC_ARM64_REG_SP,S+0x10000)
        u.reg_write(UC_ARM64_REG_LR,stop)
        u.emu_start(entry,stop,count=2000000)
        assert u.reg_read(UC_ARM64_REG_PC)==stop
    # Compare the relocated clone with the actual vendor constructor slice.
    styles=[0xffffff,0x44440000,0x55550000,0x333333,0x66660000]
    u.reg_write(regs[19],ui)
    for reg,val in zip(regs[20:25],styles):u.reg_write(reg,val)
    saved=bytes(u.mem_read(O+0x540e8,4))
    u.mem_write(O+0x540e8,raw[0x540e8:0x540ec])
    run(O+0x53c94,stop=O+0x540ec)
    u.mem_write(O+0x540e8,saved)
    u.ctl_remove_cache(O+0x53c94,O+0x540f0)
    original=trace[:]; trace.clear();count=0
    # Invoke the real patched BL + thunk + assembly dispatcher.
    run(O+0x540e8,[0,0,0,0],stop=O+0x540ec)
    clone=trace[1:1+len(original)]
    assert struct.unpack('<H',u.mem_read(tabview+0x48,2))[0]==5
    assert trace[1+len(original):][0][0]==0x28db28
    assert len(original)==len(clone), (len(original),len(clone))
    for left,right in zip(original,clone):
        arity=1 if left[0] in (0x261db0,0x28db28,0x28c8b0) else (2 if left[0] in (0x28eb08,0x28eda0,0x261de8,0x267d90,0x28c8e8,0x28d500) else 3)
        assert left[0]==right[0] and left[1][:arity]==right[1][:arity], (left,right)
    template=D+0x4000
    entries=[]
    for idx,mode in enumerate((0,3,1,2)):
        item=template+idx*0x200;entries.append(item)
        for at in (0,0x10,0x18,0x20,0x38):writeq(item+at,D+0x80000+idx*0x1000+at*4)
        for at in (0x90,0xf8):
            writeq(item+at,item+at+16);u.mem_write(item+at+16,b'\0')
        u.mem_write(item+0xb0,struct.pack('<I',mode))
        u.mem_write(item+0x50,struct.pack('<I',mode))
        run(O+0xf9aa8,[listp,item],stop=O+0xf9aac)
    actual=[readi(p+0xb0) for p in range(vec,readq(listp+24),0x120)]
    assert actual==[0,3,4,1,2],actual
    assert text(readq(vec+0x120+0x90))==b'Real3D One Pro'
    assert text(readq(vec+0x240+0x90))==b'Real3D Pro+'
    assert readq(vec+0x120+0x38)!=readq(vec+0x240+0x38)
    # Independent closure on each item, then native mode-3 action.
    for pos,expected in ((0x120,1),(0x240,2),(0x120,1)):
        item=vec+pos
        run(readq(item+0x68),[item+0x50])
        assert selected==expected and native_open[-1]==expected and depth==4
        run(O+0xf49a4,[listp,3],stop=O+0xf49a8)
        assert active==(3 if expected==1 else 4)
    # Other menus with enum 3 must not be remapped.
    run(O+0xf49a4,[listp+1024,3],stop=O+0xf49a8);assert active==3
    pointers=alloc(struct.pack('<4Q',*entries))
    vector=alloc(struct.pack('<3Q',pointers,pointers+32,pointers+32))
    run(O+0xf9bc0,[D+0x7000,vector,D+0x7100,D+0x7200],stop=O+0xf9bc4)
    assert [readi(p+0xb0) for p in registered]==[0,3,4,1,2]
    assert registered[2]==symbols['extra'], 'Navigation must retain a persistent item'
    rectangles=[(*positions[readq(p+0x38)],*sizes[readq(p+0x38)]) for p in registered]
    assert rectangles==[(0,3+i*82,760,76) for i in range(5)],rectangles
    assert registration_snapshots[-1]==[(0,3+i*82) for i in range(5)]
    assert all(y+h<=410 for x,y,w,h in rectangles), 'Rows must fit native viewport'
    assert all(rectangles[i][1]+rectangles[i][3]<=rectangles[i+1][1] for i in range(4))
    if 'layout_list' in symbols:
        def geometry_calls():
            return sum(call[0] in (0x263880,0x263a30) for call in trace)
        before=geometry_calls()
        run(O+0xf49a4,[listp,3],stop=O+0xf49a8)
        assert geometry_calls()==before, 'Unchanged highlight must not relayout'
        u.mem_write(vec+0xd3,b'\x01')
        run(O+0xf49a4,[listp,3],stop=O+0xf49a8)
        assert geometry_calls()>before, 'Availability change must invalidate layout'
        before=geometry_calls()
        run(O+0xf9bc0,[D+0x7000,vector,D+0x7100,D+0x7200],stop=O+0xf9bc4)
        assert geometry_calls()>before, 'Native registration must force layout'
        assert [positions[readq(p+0x38)] for p in registered]==[(0,3+i*82) for i in range(5)]
    # All visible children have equal padding and stay within their row.
    for item in registered:
        title,icon,mark=(readq(item+at) for at in (0,0x18,0x20))
        assert positions[title]==(142,11) and sizes[title]==(520,54)
        assert long_modes[title]==4
        assert positions[icon]==(52,8) and sizes[icon]==(80,60)
        assert positions[mark]==(26,30) and sizes[mark]==(16,16)
        for widget in (title,icon,mark):
            x,y=positions[widget];w,h=sizes[widget]
            assert y==(76-h)//2 and y+h<=76 and x+w<=760
        assert flags[readq(item+0x10)]&1, 'Subtitle must not overlap the full-height title'
    # Hidden native rows are omitted and reopening must not restore the old
    # cloned y=94 or leave a hole between visible entries.
    for item in entries[2:]:flags[readq(item+0x38)]=1
    for _ in range(3):
        positions[readq(symbols['extra']+0x38)]=(0,94)
        run(O+0xf9bc0,[D+0x7000,vector,D+0x7100,D+0x7200],stop=O+0xf9bc4)
        assert readq(listp+24)==vec+3*0x120
        assert [positions[readq(vec+i*0x120+0x38)] for i in range(3)]==[(0,3),(0,85),(0,167)]
    # A shortened registration vector also receives the additional mode.
    writeq(vector+8,pointers+16)
    run(O+0xf9bc0,[D+0x7000,vector,D+0x7100,D+0x7200],stop=O+0xf9bc4)
    assert [readi(p+0xb0) for p in registered]==[0,3,4]
    # Restore all modes, then change visibility without rebuilding the vector:
    # the UI_FAST cache must notice a hidden row and compact subsequent rows.
    writeq(vector+8,pointers+32)
    for item in entries:flags[readq(item+0x38)]=0
    run(O+0xf9bc0,[D+0x7000,vector,D+0x7100,D+0x7200],stop=O+0xf9bc4)
    flags[readq(vec+0x38)]=1
    run(O+0xf49a4,[listp,3],stop=O+0xf49a8)
    assert positions[readq(vec+0x120+0x38)]==(0,3)
    flags[readq(vec+0x38)]=0
    run(O+0xf49a4,[listp,3],stop=O+0xf49a8)
    assert positions[readq(vec+0x120+0x38)]==(0,85)
    # Native navigation omits hidden SBS modes on some inputs. rc6's exact
    # five-entry guard skipped layout for this valid three-item list.
    writeq(listp+24,vec+3*0x120)
    for p in registered[1:3]:positions[readq(p+0x38)]=(0,94)
    run(O+0xf49a4,[listp,3],stop=O+0xf49a8)
    assert [positions[readq(p+0x38)] for p in registered[:3]]==[(0,3),(0,85),(0,167)]
    # Execute the actual vendor registration (not our external-service model).
    # Stub allocation/LVGL only; native filtering, strings, row copies, ordering
    # and list rebuilding run as ARM64 instructions from the pinned ELF.
    native_registration=True
    registry=D+0x7400; exclusions=D+0x7500
    groups=D+0x7600; group_pointers=D+0x7700; group_header=tabview
    writeq(group_pointers,listp)
    for at,value in ((0,group_pointers),(8,group_pointers+8),(16,group_pointers+8)):
        writeq(groups+at,value)
    writeq(listp,group_header);writeq(listp+8,D+0x800)
    assert struct.unpack('<H',u.mem_read(group_header+0x48,2))[0]==5
    writeq(listp+32,vec+5*0x120)
    # Negative control: the old four-tab selector really truncates the native
    # rebuilt list even when five row objects and five pointers are supplied.
    for item in entries:flags[readq(item+0x38)]=0
    u.mem_write(tabview+0x48,struct.pack('<H',4))
    run(O+0xf9bc0,[registry,vector,exclusions,groups],stop=O+0xf9bc4)
    assert (readq(listp+24)-readq(listp+16))//0x120==4
    u.mem_write(tabview+0x48,struct.pack('<H',5))
    for iteration in range(4):
        for item in entries:flags[readq(item+0x38)]=0
        if iteration==2:flags[readq(entries[2]+0x38)]=1
        run(O+0xf9bc0,[registry,vector,exclusions,groups],stop=O+0xf9bc4)
        count=(readq(listp+24)-readq(listp+16))//0x120
        rows=[readq(listp+16)+i*0x120 for i in range(count)]
        expected_modes=[0,3,4,2] if iteration==2 else [0,3,4,1,2]
        assert [readi(p+0xb0) for p in rows]==expected_modes
        assert [positions[readq(p+0x38)] for p in rows]==[(0,3+i*82) for i in range(count)]
        assert all(flags[readq(p+0x10)]&1 for p in rows)
    assert not any(call[0] in (0x16c558,0x1743f8) for call in trace)
    print('ARM64 UI: identical native row styles, five distinct entries, both callbacks, '
          'separate strength, post-registration non-overlapping rows, correct highlight '
          'and real vendor registration with five navigation cells passed.')


if __name__=='__main__':test(sys.argv[1])
