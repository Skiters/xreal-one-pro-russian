"""Full native adapter + fake HAL; tests completion/error semantics, not real hardware."""
import ctypes as C
from pathlib import Path
import sys

lib=C.CDLL(str(Path(sys.argv[1]).resolve()))
lib.test_memory.argtypes=[C.c_uint64]
lib.test_memory.restype=C.c_void_p
lib.test_faults.argtypes=[C.c_uint64,C.c_uint64]


class RPC(C.Structure):
    _fields_=[('name',C.c_char*32),('reserved',C.c_uint64),
              ('args',C.c_void_p),('length',C.c_uint32)]


class Frame(C.Structure):
    _fields_=[('width',C.c_uint32),('height',C.c_uint32),
              ('sy',C.c_uint32),('suv',C.c_uint32),('dy',C.c_uint32),('duv',C.c_uint32),
              ('dw',C.c_uint32),('mode',C.c_uint32),
              ('src',C.c_uint64*3),('dst',C.c_uint64*3),
              ('depth',C.c_uint64),('result',C.c_uint64)]


lib.xreal_cpu_rpc.argtypes=[C.c_uint32,C.POINTER(RPC),C.c_uint32]


def call(name,args):
    rpc=RPC(name.encode(),0,C.addressof(args),C.sizeof(args))
    return lib.xreal_cpu_rpc(0,C.byref(rpc),1)


def status(pa=0x33000000):
    return C.c_int.from_address(lib.test_memory(pa))


result=C.c_uint64(0x33000000)
assert call('ArArmWarp2DTo3DInit',result)==0
f=Frame(1920,1080,1920,960,1920,960,480,2,
        (C.c_uint64*3)(0x33300000,0x33600000,0x33700000),
        (C.c_uint64*3)(0x33800000,0x33b00000,0x33c00000),
        0x33d00000,result.value)
C.memset(lib.test_memory(f.depth),24,480*270)
for channel,size in enumerate((1920*1080,960*540,960*540)):
    C.memset(lib.test_memory(f.src[channel]),80+channel*50,size)
assert call('ArArmWarp2DTo3DProcess',f)==0 and status().value==0
for channel,size in enumerate((1920*1080,960*540,960*540)):
    assert C.string_at(lib.test_memory(f.dst[channel]),size)==bytes([80+channel*50])*size
# Runtime failures must replace a previous successful completion.
for mapping,flushing in ((f.depth,0),(f.src[1],0),(0,f.dst[0])):
    status().value=0
    lib.test_faults(mapping,flushing)
    assert call('ArArmWarp2DTo3DProcess',f)==-5
    assert status().value==-5
# Invalid result/plane alias must cause no HAL mapping or modification.
lib.test_faults(0,0)
f.result=f.dst[0]
status(f.result).value=12345
assert call('ArArmWarp2DTo3DProcess',f)==-22
assert status(f.result).value==12345 and lib.test_mappings()==0
assert call('ArArmWarp2DTo3DClose',result)==0
print('PASS native full RPC: valid planar frame, map/flush faults, no stale success, no write through aliased result')
