"""perf15 Real3D: independent per-pixel oracle for the 4-tap Catmull-Rom luma
warp (with anti-ringing clamp), and byte equality of the joint U+V pass with
two single-plane passes."""
import ctypes as C
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_real3d_smooth import depth_shift

CR = []
for f in range(32):
    t = f/32
    w = [(-t**3+2*t*t-t)/2, (3*t**3-5*t*t+2)/2, (-3*t**3+4*t*t+t)/2, (t**3-t*t)/2]
    q = [round(256*x) for x in w]
    q[1 if f < 16 else 2] += 256 - sum(q)
    CR.append(q)


def cubic(row, q, width):
    if q <= 0:
        return row[0]
    x, f = q >> 5, q & 31
    if x >= width - 1:
        return row[width-1]
    if not f:
        return row[x]
    a, b, c, d = row[max(x-1, 0)], row[x], row[x+1], row[min(x+2, width-1)]
    v = (CR[f][0]*a + CR[f][1]*b + CR[f][2]*c + CR[f][3]*d + 128) >> 8
    return max(min(b, c), min(max(b, c), v))


import math
JBU = [max(1, round(256*math.exp(-(d/12.0)**2))) for d in range(256)]


def oracle(src, depth, width, height, stride, dw, dh, bits, begin, end, fill, jbu=False):
    ratio, rows = width // dw, height // dh
    out = bytearray([fill]) * (stride*height)
    den = 2*rows
    for y in range(begin*rows, end*rows):
        zy, ry = divmod(y, rows)
        num = 2*ry + 1 - rows
        other = max(zy-1, 0) if num < 0 else min(zy+1, dh-1)
        w = abs(num)
        row = src[y*stride:y*stride+width]

        def vertical(zx):
            a = depth_shift(depth[zy*dw+zx], ratio, bits)
            b = depth_shift(depth[other*dw+zx], ratio, bits)
            return (a*(den-w) + b*w + rows) >> (den.bit_length()-1)
        def raw(r, c):
            return depth_shift(depth[r*dw+c], ratio, bits)

        def centre(r, c):
            r1, r2 = (r*4+1)*stride, (r*4+2)*stride
            cc = c*4+1
            return (src[r1+cc] + src[r1+cc+1] + src[r2+cc] + src[r2+cc+1] + 2) >> 2
        for x in range(width):
            zx, k = divmod(x, ratio)
            if jbu:
                l, r = max(zx-1, 0), min(zx+1, dw-1)
                vals = [raw(zy, zx), raw(zy, l), raw(zy, r), raw(other, zx), raw(other, l), raw(other, r)]
                if max(vals) - min(vals) > 16:
                    hnum = 2*k + 1 - 4
                    hw, side = abs(hnum), (l if hnum < 0 else r)
                    cells = [(zy, zx, (8-hw)*(8-w)), (zy, side, hw*(8-w)), (other, zx, (8-hw)*w), (other, side, hw*w)]
                    g = src[y*stride+x]
                    ws = acc = 0
                    for rr, cc, sw in cells:
                        wt = sw*JBU[abs(g-centre(rr, cc))]
                        ws += wt
                        acc += wt*raw(rr, cc)
                    out[y*stride+x] = cubic(row, x*32 + (acc + ws//2)//ws, width)
                    continue
            shift = vertical(zx)
            left, right = vertical(max(zx-1, 0)), vertical(min(zx+1, dw-1))
            if abs(left-shift) > 4 or abs(right-shift) > 4:
                hnum = 2*k + 1 - ratio
                side = left if hnum < 0 else right
                hden, hw = 2*ratio, abs(hnum)
                shift = (shift*(hden-hw) + side*hw + ratio) >> (hden.bit_length()-1)
            out[y*stride+x] = cubic(row, x*32 + shift, width)
    return bytes(out)


def main(path):
    lib = C.CDLL(path)
    luma = lib.real3d_warp_luma_cubic_range_for_test
    luma.argtypes = [C.c_char_p, C.c_char_p, C.c_char_p] + [C.c_uint]*9
    pair = lib.real3d_warp_chroma_pair_range_for_test
    pair.argtypes = [C.c_char_p]*5 + [C.c_uint]*8
    single = lib.real3d_warp_cells_smooth_range_for_test
    single.argtypes = [C.c_char_p, C.c_char_p, C.c_char_p] + [C.c_uint]*9
    jbu = lib.real3d_warp_luma_jbu_range_for_test
    jbu.argtypes = [C.c_char_p, C.c_char_p, C.c_char_p] + [C.c_uint]*9
    rng = random.Random(15)
    cases = 0
    dw, dh = 60, 6
    for bits in (2, 3, 4):
        for kind in ('random', 'step', 'ramp', 'flat', 'texture'):
            width, height, stride = dw*4, dh*4, dw*4 + 24
            if kind == 'object':    # bright object (near) on a dark background (far)
                src = bytes((200 if (x-120)**2 + (yy-12)**2 < 140 else 30) + rng.randrange(-3, 4)
                            if x < stride else 0 for yy in range(height) for x in range(stride))
            elif kind == 'texture':   # sharp periodic detail, the case cubic helps
                src = bytes((255 if (x // 2) % 2 else 0) if x < stride else 0
                            for _ in range(height) for x in range(stride))
            else:
                src = bytes(rng.randrange(256) for _ in range(stride*height))
            if kind == 'random':
                depth = bytes(rng.randrange(256) for _ in range(dw*dh))
            elif kind == 'step':
                depth = bytes(200 if zx > 20 + 3*zy else 10 for zy in range(dh) for zx in range(dw))
            elif kind == 'object':
                depth = bytes(180 if (zx*4+2-120)**2 + (zy*4+2-12)**2 < 140 else 20
                              for zy in range(dh) for zx in range(dw))
            elif kind == 'ramp':
                depth = bytes((zx*rng.randrange(4) + zy) % 256 for zy in range(dh) for zx in range(dw))
            else:
                depth = bytes([rng.randrange(1, 256)])*(dw*dh)
            for begin, end in ((0, dh), (0, 1), (2, 5), (5, 6)):
                dst = C.create_string_buffer(bytes([238])*(stride*height), stride*height)
                assert luma(src, dst, depth, width, height, stride, stride, dw, dh, begin, end, bits) == 0
                assert dst.raw == oracle(src, depth, width, height, stride, dw, dh, bits, begin, end, 238), \
                    ('luma cubic', bits, kind, begin, end)
                cases += 1
            for begin, end in ((0, dh), (1, 4), (5, 6)):
                dst = C.create_string_buffer(bytes([238])*(stride*height), stride*height)
                assert jbu(src, dst, depth, width, height, stride, stride, dw, dh, begin, end, bits) == 0
                assert dst.raw == oracle(src, depth, width, height, stride, dw, dh, bits, begin, end, 238, True), \
                    ('luma jbu', bits, kind, begin, end)
                cases += 1
            # Chroma pair: identical to two single-plane passes.
            cw, chh, cs = dw*2, dh*2, dw*2 + 16
            u = bytes(rng.randrange(256) for _ in range(cs*chh))
            v = bytes(rng.randrange(256) for _ in range(cs*chh))
            for begin, end in ((0, dh), (1, 4)):
                du = C.create_string_buffer(bytes([7])*(cs*chh), cs*chh)
                dv = C.create_string_buffer(bytes([7])*(cs*chh), cs*chh)
                assert pair(u, du, v, dv, depth, cw, chh, cs, dw, dh, begin, end, bits) == 0
                su = C.create_string_buffer(bytes([7])*(cs*chh), cs*chh)
                sv = C.create_string_buffer(bytes([7])*(cs*chh), cs*chh)
                assert single(u, su, depth, cw, chh, cs, cs, dw, dh, begin, end, bits) == 0
                assert single(v, sv, depth, cw, chh, cs, cs, dw, dh, begin, end, bits) == 0
                assert du.raw == su.raw and dv.raw == sv.raw, ('chroma pair', bits, kind, begin, end)
                cases += 1
    print(f'Real3D perf15/16 oracle passed: {cases} cases (cubic luma, edge-aware depth, joint U/V)')


if __name__ == '__main__':
    main(sys.argv[1])
