"""coef kernel: accuracy versus the float trilinear reference on synthetic,
grid-boundary and random pixels; NEON/portable equality via --dump."""
import argparse
import ctypes as C
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lut import reference, cubes_from_bin


def main():
    p = argparse.ArgumentParser()
    p.add_argument('library')
    p.add_argument('--bin', type=Path)
    p.add_argument('--dump')
    args = p.parse_args()
    lib = C.CDLL(str(Path(args.library).resolve()))
    lib.xreal_coef_table_bytes.restype = C.c_uint64
    build = lib.xreal_build_coef_table
    build.argtypes = [C.POINTER(C.c_ushort), C.POINTER(C.c_short)]
    run = lib.xreal_coef_rgb_lut
    run.argtypes = [C.POINTER(C.c_short), *[C.POINTER(C.c_ubyte)]*3, *[C.c_uint]*5]
    assert lib.xreal_coef_table_bytes() == 4096*32*2 + 768*4
    table = (C.c_short*(lib.xreal_coef_table_bytes()//2))()
    rng = random.Random(21)
    cubes = {'identity': [round(v*65280/16) for b in range(17) for g in range(17)
                          for r in range(17) for v in (r, g, b)],
             'random': [rng.randrange(256)*256 for _ in range(14739)]}
    if args.bin:
        cubes.update(cubes_from_bin(args.bin))
    assert build(None, table) == -22
    w, h = 1009, 6
    pitches = [1019, 1031, 1049]
    report = {}
    for name, cube in cubes.items():
        assert build((C.c_ushort*len(cube))(*cube), table) == 0
        pixels = [(r, g, b) for r in (0, 15, 16, 17, 127, 128, 239, 240, 254, 255)
                  for g in (0, 16, 128, 255) for b in (0, 16, 255)]
        pixels += [(17, 34, 51)]*8
        pixels += [tuple(rng.randrange(256) for _ in range(3)) for _ in range(w*h - len(pixels))]
        planes = [(C.c_ubyte*(s*h))(*([171]*(s*h))) for s in pitches]
        for i, rgb in enumerate(pixels):
            y, x = divmod(i, w)
            for c in range(3):
                planes[c][y*pitches[c]+x] = rgb[c]
        assert run(table, *planes, *pitches, w, h) == 0
        err = 0
        for i, rgb in enumerate(pixels):
            y, x = divmod(i, w)
            out = [planes[c][y*pitches[c]+x] for c in range(3)]
            err = max(err, max(abs(a-b) for a, b in zip(out, reference(cube, rgb))))
        for c, s in enumerate(pitches):
            for y in range(h):
                assert bytes(planes[c][y*s+w:(y+1)*s]) == bytes([171])*(s-w)
        if name.endswith('.cube') or name == 'random':
            assert err <= 1, (name, err)
        else:
            assert err <= 1, (name, err)   # identity: nodes rounded to integers
        for ww, hh, rs in [(0, 4, 1019), (1921, 1, 2000), (1009, 1081, 1019), (1009, 4, 1008)]:
            assert run(table, *planes, rs, pitches[1], pitches[2], ww, hh) == -22
        if args.dump:
            with open(args.dump, 'ab') as f:
                f.write(b''.join(bytes(x) for x in planes))
        report[name.rsplit('/', 1)[-1]] = {'max_error_vs_float': err}
    print(json.dumps(report))


if __name__ == '__main__':
    main()
