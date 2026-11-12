"""perf11 repeated-frame reuse through the full native adapter (fake HAL):
a repeated input frame must produce exactly the output of a fresh conversion,
including after a different frame in between and when only one row changes."""
import ctypes as C
from pathlib import Path
import random
import sys

lib = C.CDLL(str(Path(sys.argv[1]).resolve()))
lib.test_memory.argtypes = [C.c_uint64]
lib.test_memory.restype = C.c_void_p


class RPC(C.Structure):
    _fields_ = [('name', C.c_char*32), ('reserved', C.c_uint64), ('args', C.c_void_p), ('length', C.c_uint32)]


class Init(C.Structure):
    _fields_ = [('lut', C.c_uint64), ('result', C.c_uint64), ('a', C.c_uint32), ('b', C.c_uint32)]


class Frame(C.Structure):
    _fields_ = [('planes', C.c_uint64*3), ('result', C.c_uint64), ('strides', C.c_uint32*3),
                ('width', C.c_uint32), ('height', C.c_uint32)]


lib.xreal_cpu_rpc.argtypes = [C.c_uint32, C.POINTER(RPC), C.c_uint32]


def call(name, args, length=None):
    rpc = RPC(name.encode(), 0, C.addressof(args), length or C.sizeof(args))
    return lib.xreal_cpu_rpc(0, C.byref(rpc), 1)


rng = random.Random(11)
W, H, S = 320, 120, 384
LUT, RES = 0x33000000, 0x33010000
PLANES = (0x33100000, 0x33200000, 0x33300000)
# A smooth non-identity cube (gamma-ish), 3 planes of 17^3 bytes.
cube = bytes(min(255, int(255*((i % 17)/16)**0.8)) for i in range(4913)) + \
       bytes(min(255, int(255*(((i//17) % 17)/16)**1.1)) for i in range(4913)) + \
       bytes(min(255, int(255*((i//289)/16)**0.9)) for i in range(4913))
C.memmove(lib.test_memory(LUT), cube, len(cube))
assert call('ArArmlut3dInit', Init(LUT, RES, 0, 0), 24) == 0

frame = Frame((C.c_uint64*3)(*PLANES), RES, (C.c_uint32*3)(S, S, S), W, H)


def run(rows):
    for c in range(3):
        C.memmove(lib.test_memory(PLANES[c]), rows[c], S*H)
    assert call('ArArmlut3dProcessRgbToYuv', frame, 52) == 0
    # Visible pixels only: the adapter never writes the stride padding.
    return [b''.join(C.string_at(lib.test_memory(PLANES[c]) + y*S, W) for y in range(H)) for c in range(3)]


def make():
    return [bytes(rng.randrange(256) for _ in range(S*H)) for _ in range(3)]


a, b = make(), make()
b_one_row = [bytearray(x) for x in a]
b_one_row[1][(H//2)*S + 7] ^= 0x40           # one pixel in one row differs
b_one_row = [bytes(x) for x in b_one_row]
fresh_a = run(a)
for _ in range(3):                           # repeats: served from the kept copy
    assert run(a) == fresh_a
fresh_b = run(b)
assert fresh_b != fresh_a
assert run(b) == fresh_b
assert run(a) == fresh_a                      # back to A: recomputed, same output
# Dither-like noise (+-1 on every pixel) is a repeat: served from the copy.
dithered = [bytes(max(0, min(255, v + rng.choice((-1, 0, 1)))) for v in plane) for plane in a]
run(a)
out_d = run(dithered)  # perf13: reuse disabled, so this is a fresh conversion
assert out_d == run(dithered)
changed = run(b_one_row)
assert changed != fresh_a, 'a single changed pixel must not be served from the copy'
assert run(b_one_row) == changed
print('repeat path: repeated frames identical to fresh conversion; single-pixel change converted')
