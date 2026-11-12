"""perf23 Real3D: independent per-pixel oracle for the production warp with
depth reprojection (displacement read at x + D(x), per cell), for luma
(vertical + horizontal smoothing) and the joint U/V pass (vertical only)."""
import ctypes as C
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_real3d_smooth import depth_shift, sample_q5


def oracle(src, depth, width, height, stride, dw, dh, bits, begin, end, fill, horizontal):
    ratio, rows = width // dw, height // dh
    out = bytearray([fill]) * (stride*height)
    den = 2*rows
    for y in range(begin*rows, end*rows):
        zy, ry = divmod(y, rows)
        num = 2*ry + 1 - rows
        other = max(zy-1, 0) if num < 0 else min(zy+1, dh-1)
        w = abs(num)
        vb = []
        for zx in range(dw):
            a = depth_shift(depth[zy*dw+zx], ratio, bits)
            b = depth_shift(depth[other*dw+zx], ratio, bits)
            vb.append((a*(den-w) + b*w + rows) >> (den.bit_length()-1))
        mv = [vb[min(zx + (vb[zx] + 16*ratio)//(32*ratio), dw-1)] for zx in range(dw)]
        row = src[y*stride:y*stride+width]
        for x in range(width):
            zx, k = divmod(x, ratio)
            shift = mv[zx]
            if horizontal:
                left, right = mv[max(zx-1, 0)], mv[min(zx+1, dw-1)]
                if abs(left-shift) > 4 or abs(right-shift) > 4:
                    hnum = 2*k + 1 - ratio
                    side = left if hnum < 0 else right
                    hden, hw = 2*ratio, abs(hnum)
                    shift = (shift*(hden-hw) + side*hw + ratio) >> (hden.bit_length()-1)
            out[y*stride+x] = sample_q5(row, x*32 + shift, width)
    return bytes(out)


def main(path):
    lib = C.CDLL(path)
    luma = lib.real3d_warp_luma_reproject_range_for_test
    luma.argtypes = [C.c_char_p, C.c_char_p, C.c_char_p] + [C.c_uint]*9
    pair = lib.real3d_warp_chroma_pair_range_for_test
    pair.argtypes = [C.c_char_p]*5 + [C.c_uint]*8
    rng = random.Random(23)
    dw, dh = 60, 6
    cases = 0
    for bits in (2, 3, 4):
        for kind in ('random', 'step', 'ramp', 'flat', 'object'):
            if kind == 'random':
                depth = bytes(rng.randrange(256) for _ in range(dw*dh))
            elif kind == 'step':
                depth = bytes(200 if zx > 20 + 3*zy else 10 for zy in range(dh) for zx in range(dw))
            elif kind == 'ramp':
                depth = bytes((zx*rng.randrange(4) + zy) % 256 for zy in range(dh) for zx in range(dw))
            elif kind == 'object':
                depth = bytes(180 if 20 <= zx < 35 else 20 for zy in range(dh) for zx in range(dw))
            else:
                depth = bytes([rng.randrange(256)])*(dw*dh)
            width, height, stride = dw*4, dh*4, dw*4 + 24
            src = bytes(rng.randrange(256) for _ in range(stride*height))
            for begin, end in ((0, dh), (0, 1), (2, 5), (5, 6)):
                dst = C.create_string_buffer(bytes([238])*(stride*height), stride*height)
                assert luma(src, dst, depth, width, height, stride, stride, dw, dh, begin, end, bits) == 0
                assert dst.raw == oracle(src, depth, width, height, stride, dw, dh, bits, begin, end, 238, True), \
                    ('luma', bits, kind, begin, end)
                cases += 1
            cw, ch, cs = dw*2, dh*2, dw*2 + 16
            u = bytes(rng.randrange(256) for _ in range(cs*ch))
            v = bytes(rng.randrange(256) for _ in range(cs*ch))
            for begin, end in ((0, dh), (1, 4)):
                du = C.create_string_buffer(bytes([7])*(cs*ch), cs*ch)
                dv = C.create_string_buffer(bytes([7])*(cs*ch), cs*ch)
                assert pair(u, du, v, dv, depth, cw, ch, cs, dw, dh, begin, end, bits) == 0
                assert du.raw == oracle(u, depth, cw, ch, cs, dw, dh, bits, begin, end, 7, False), ('U', bits, kind)
                assert dv.raw == oracle(v, depth, cw, ch, cs, dw, dh, bits, begin, end, 7, False), ('V', bits, kind)
                cases += 1
    print(f'Real3D perf23 reprojection oracle passed: {cases} cases (luma, joint U/V)')


if __name__ == '__main__':
    main(sys.argv[1])
