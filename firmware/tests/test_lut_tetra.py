"""Tetrahedral AI HDR kernel: exact integer tetrahedral oracle, and the
distance from the trilinear reference on the vendor cubes."""
import argparse
import ctypes as C
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lut import reference, cubes_from_bin


def tetra(cube, rgb):
    lo, f = [], []
    for v in rgb:
        i, r = v*16 // 255, v*16 % 255
        if i == 16:
            i, r = 15, 255
        lo.append(i); f.append(r)
    order = sorted(range(3), key=lambda c: -f[c])  # ties carry zero weight
    step = (1, 17, 289)
    base = lo[0] + 17*lo[1] + 289*lo[2]
    a = base + step[order[0]]
    b = a + step[order[1]]
    nodes = (base, a, b, base + 307)
    fs = sorted(f, reverse=True)
    weights = (255-fs[0], fs[0]-fs[1], fs[1]-fs[2], fs[2])
    return [(sum(w*min((cube[n*3+c] + 128) // 256, 255) for w, n in zip(weights, nodes)) + 127) // 255
            for c in range(3)]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('library')
    p.add_argument('--bin', type=Path)
    args = p.parse_args()
    lib = C.CDLL(str(Path(args.library).resolve()))
    lib.xreal_tetra_table_bytes.restype = C.c_uint64
    build = lib.xreal_build_tetra_table
    build.argtypes = [C.POINTER(C.c_ushort), C.POINTER(C.c_uint32)]
    run = lib.xreal_tetra_rgb_lut
    run.argtypes = [C.POINTER(C.c_uint32), *[C.POINTER(C.c_ubyte)]*3, *[C.c_uint]*5]
    assert lib.xreal_tetra_table_bytes() == (4913+768)*4
    table = (C.c_uint32*(lib.xreal_tetra_table_bytes()//4))()
    rng = random.Random(20261110)
    cubes = {
        'identity': [round(v*65280/16) for b in range(17) for g in range(17)
                     for r in range(17) for v in (r, g, b)],
        'random': [rng.randrange(256)*256 for _ in range(14739)]}
    if args.bin:
        cubes.update(cubes_from_bin(args.bin))
    assert build(None, table) == -22
    report = {}
    for name, cube in cubes.items():
        assert build((C.c_ushort*len(cube))(*cube), table) == 0
        width, height = 1009, 6
        pitches = [1019, 1031, 1049]
        pixels = [(r, g, b) for r in (0, 255) for g in (0, 255) for b in (0, 255)]
        pixels += [(v, v, v) for v in range(0, 256, 5)]
        pixels += [(17, 34, 51)]*8 + [(17, 34, 51)]*3 + [(17, 34, 52)]
        pixels += [(16, 16, 32), (32, 16, 16), (16, 32, 16)]  # tied fractions
        pixels += [tuple(rng.randrange(256) for _ in range(3))
                   for _ in range(width*height-len(pixels))]
        planes = [(C.c_ubyte*(s*height))(*([171]*(s*height))) for s in pitches]
        for i, rgb in enumerate(pixels):
            y, x = divmod(i, width)
            for c in range(3):
                planes[c][y*pitches[c]+x] = rgb[c]
        assert run(table, *planes, *pitches, width, height) == 0
        trilinear = 0
        for i, rgb in enumerate(pixels):
            y, x = divmod(i, width)
            actual = [planes[c][y*pitches[c]+x] for c in range(3)]
            assert actual == tetra(cube, rgb), (name, rgb, actual, tetra(cube, rgb))
            if name == 'identity':
                assert actual == list(rgb)
            trilinear = max(trilinear, max(abs(a-b) for a, b in zip(actual, reference(cube, rgb))))
        if name.endswith('.cube'):
            assert trilinear <= 3, (name, trilinear)
        for c, s in enumerate(pitches):
            for y in range(height):
                assert bytes(planes[c][y*s+width:(y+1)*s]) == bytes([171])*(s-width)
        previous = [bytes(x) for x in planes]
        for w, h, rs in [(0, 4, 1019), (1921, 1, 2000), (1009, 1081, 1019), (1009, 4, 1008)]:
            assert run(table, *planes, rs, pitches[1], pitches[2], w, h) == -22
            assert [bytes(x) for x in planes] == previous
        report[name] = {'exact_vs_tetra_oracle': True, 'max_diff_vs_trilinear': trilinear}
    print(json.dumps({'pixels_per_cube': width*height, 'cubes': report}))


if __name__ == '__main__':
    main()
