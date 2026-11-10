"""Vertical displacement smoothing for Real3D Pro+: independent per-pixel oracle.

Run against a native build of work/3d-lab/real3d_cpu.c (the CI rpc library).
"""
import ctypes as C
import random
import sys


def depth_shift(value, ratio, bits):
    return value << (5 - bits - (ratio == 2))


def sample_q5(row, q, width):
    if q <= 0:
        return row[0]
    x, frac = q // 32, q & 31
    if x >= width - 1:
        return row[width - 1]
    return (row[x]*(32-frac) + row[x+1]*frac + 16) >> 5


def oracle(src, depth, width, height, stride, dw, dh, bits, begin, end, fill, bilinear=False):
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
        for x in range(width):
            zx, k = divmod(x, ratio)
            shift = vertical(zx)
            if bilinear:
                hnum = 2*k + 1 - ratio
                side = vertical(max(zx-1, 0) if hnum < 0 else min(zx+1, dw-1))
                hden, hw = 2*ratio, abs(hnum)
                shift = (shift*(hden-hw) + side*hw + ratio) >> (hden.bit_length()-1)
            out[y*stride+x] = sample_q5(row, x*32 + shift, width)
    return bytes(out)


def main(path):
    lib = C.CDLL(path)
    args = [C.c_char_p, C.c_char_p, C.c_char_p] + [C.c_uint]*9
    smooth = lib.real3d_warp_cells_smooth_range_for_test
    smooth.argtypes = args
    plain = lib.real3d_warp_cells_range_for_test
    plain.argtypes = args
    bilinear = lib.real3d_warp_cells_bilinear_range_for_test
    bilinear.argtypes = args
    rng = random.Random(20261110)
    cases = 0
    for ratio in (4, 2):
        dw, dh = 60, 6
        width, height, stride = dw*ratio, dh*ratio, dw*ratio + 24
        for bits in (2, 3, 4):
            for kind in ('random', 'step', 'flat-rows', 'flat'):
                src = bytes(rng.randrange(256) for _ in range(stride*height))
                if kind == 'random':
                    depth = bytes(rng.randrange(256) for _ in range(dw*dh))
                elif kind == 'step':  # a slanted foreground edge: the staircase case
                    depth = bytes(200 if zx > 20 + 3*zy else 10 for zy in range(dh) for zx in range(dw))
                elif kind == 'flat-rows':
                    line = [rng.randrange(256) for _ in range(dw)]
                    depth = bytes(line*dh)
                else:
                    depth = bytes([rng.randrange(256)])*(dw*dh)
                for begin, end in ((0, dh), (0, 1), (2, 5), (5, 6)):
                    dst = C.create_string_buffer(bytes([238])*(stride*height), stride*height)
                    assert smooth(src, dst, depth, width, height, stride, stride, dw, dh,
                                  begin, end, bits) == 0
                    expected = oracle(src, depth, width, height, stride, dw, dh, bits, begin, end, 238)
                    assert dst.raw == expected, (ratio, bits, kind, begin, end)
                    both = C.create_string_buffer(bytes([238])*(stride*height), stride*height)
                    assert bilinear(src, both, depth, width, height, stride, stride, dw, dh,
                                    begin, end, bits) == 0
                    assert both.raw == oracle(src, depth, width, height, stride, dw, dh, bits,
                                              begin, end, 238, True), ('bilinear', ratio, bits, kind, begin, end)
                    if kind == 'flat':
                        assert both.raw == dst.raw, 'bilinear must not change constant depth'
                    if kind in ('flat-rows', 'flat'):
                        old = C.create_string_buffer(bytes([238])*(stride*height), stride*height)
                        assert plain(src, old, depth, width, height, stride, stride, dw, dh,
                                     begin, end, bits) == 0
                        assert old.raw == dst.raw, 'smoothing must not change vertically constant depth'
                    cases += 1
    dst = C.create_string_buffer(64*12)
    assert smooth(b'\0'*64*12, dst, b'\0'*16*4, 48, 12, 64, 64, 16, 4, 0, 4, 3) == -22  # 3 rows per depth row
    assert bilinear(b'\0'*64*12, dst, b'\0'*16*4, 48, 12, 64, 64, 16, 4, 0, 4, 3) == -22
    print(f'Real3D smoothing oracle passed: {cases} cases (vertical and bilinear)')


if __name__ == '__main__':
    main(sys.argv[1])
