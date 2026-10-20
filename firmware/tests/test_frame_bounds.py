import struct
from arm64_helper import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, BASE, SRC, STACK, STOP,
                          elf, symbols, UC_ARM64_REG_X0, UC_ARM64_REG_SP,
                          UC_ARM64_REG_LR)

# One captured descriptor from the previously working physical One Pro.
base=[1920,1080,2048,1024,1920,1024,480,2,
      0x39244000,0x39462000,0x394eb000,
      0x3cd50000,0x3cf4a400,0x3cfd1400,0x3d66b000,0x3ca13000]


def validate(values):
    machine=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
    machine.mem_map(BASE,0x200000)
    for segment in elf.iter_segments():
        if segment['p_type']=='PT_LOAD':
            machine.mem_write(BASE+segment['p_vaddr'],segment.data())
    machine.mem_map(SRC,0x400000)
    descriptor=struct.pack('<8I8Q',*values)
    machine.mem_write(SRC,descriptor)
    machine.reg_write(UC_ARM64_REG_X0,SRC)
    machine.reg_write(UC_ARM64_REG_SP,STACK)
    machine.reg_write(UC_ARM64_REG_LR,STOP)
    machine.emu_start(BASE+symbols['real3d_validate_frame_for_test'],STOP,count=100000)
    assert bytes(machine.mem_read(SRC,len(descriptor)))==descriptor
    result=machine.reg_read(UC_ARM64_REG_X0)&0xffffffff
    return result if result<0x80000000 else result-0x100000000


assert validate(base)==0
cases=0
for i in range(8):
    changed=list(base);changed[i]=0
    assert validate(changed)==-22
    cases+=1
for i in range(8,16):
    for address in (0,0x32ffffff,0x3fffffff,0xffffffffffffffff):
        changed=list(base);changed[i]=address
        assert validate(changed)==-22
        cases+=1
    for j in range(8,16):
        if i==j:continue
        changed=list(base);changed[i]=changed[j]+1
        assert validate(changed)==-22,(i,j)
        cases+=1
print(f'PASS ARM64 frame bounds: captured valid descriptor and {cases} malformed/overlapping descriptors')
