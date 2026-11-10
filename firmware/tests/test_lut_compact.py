"""Compact AI HDR kernel: float trilinear oracle and agreement with the dense kernel."""
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
    args = p.parse_args()
    lib = C.CDLL(str(Path(args.library).resolve()))
    lib.xreal_rgb_table_bytes.restype = C.c_uint64
    lib.xreal_compact_table_bytes.restype = C.c_uint64
    build_dense = lib.xreal_build_rgb_table_range
    build_dense.argtypes = [C.POINTER(C.c_ushort), C.POINTER(C.c_uint64), C.c_uint, C.c_uint]
    build = lib.xreal_build_compact_table_range
    build.argtypes = [C.POINTER(C.c_ushort), C.POINTER(C.c_uint32), C.c_uint, C.c_uint]
    dense_run = lib.xreal_full_rgb_lut
    dense_run.argtypes = [C.POINTER(C.c_uint64), *[C.POINTER(C.c_ubyte)]*3, *[C.c_uint]*5]
    run = lib.xreal_compact_rgb_lut
    run.argtypes = [C.POINTER(C.c_uint32), *[C.POINTER(C.c_ubyte)]*3, *[C.c_uint]*5]
    lib.xreal_compact2_table_bytes.restype = C.c_uint64
    build2 = lib.xreal_build_compact2_table_range
    build2.argtypes = build.argtypes
    run2 = lib.xreal_compact2_rgb_lut
    run2.argtypes = run.argtypes
    assert lib.xreal_compact_table_bytes() == (17*17*256+512)*4
    assert lib.xreal_compact2_table_bytes() == (17*17*256+512)*4
    table2 = (C.c_uint32*(lib.xreal_compact2_table_bytes()//4))()
    dense = (C.c_uint64*(lib.xreal_rgb_table_bytes()//8))()
    table = (C.c_uint32*(lib.xreal_compact_table_bytes()//4))()
    rng = random.Random(20261110)
    cubes = {
        'identity': [round(v*65280/16) for b in range(17) for g in range(17)
                     for r in range(17) for v in (r, g, b)],
        'cross-channel': [round(v*65280/32) for b in range(17) for g in range(17)
                          for r in range(17) for v in (g+b, r+b, r+g)],
        'random': [rng.randrange(256)*256 for _ in range(14739)]}
    if args.bin:
        cubes.update(cubes_from_bin(args.bin))
    report = {}
    for name, cube in cubes.items():
        data = (C.c_ushort*len(cube))(*cube)
        # Build in uneven ranges, as the adapter's workers do.
        for begin, end in ((0, 4), (4, 9), (9, 13), (13, 17)):
            assert build(data, table, begin, end) == 0
            assert build2(data, table2, begin, end) == 0
        assert build_dense(data, dense, 0, 17) == 0
        width, height = 1009, 6
        pitches = [1019, 1031, 1049]
        pixels = [(r, g, b) for r in (0, 255) for g in (0, 255) for b in (0, 255)]
        # Repeated 4-pixel groups exercise the flat-area shortcut, including a
        # group that differs from the previous one only in its last lane.
        pixels += [(17, 34, 51)]*8 + [(17, 34, 51)]*3 + [(17, 34, 52)]
        pixels += [tuple(rng.randrange(256) for _ in range(3))
                   for _ in range(width*height-len(pixels))]
        planes = [(C.c_ubyte*(s*height))(*([171]*(s*height))) for s in pitches]
        twins = [(C.c_ubyte*(s*height))(*([171]*(s*height))) for s in pitches]
        thirds = [(C.c_ubyte*(s*height))(*([171]*(s*height))) for s in pitches]
        for i, rgb in enumerate(pixels):
            y, x = divmod(i, width)
            for c in range(3):
                planes[c][y*pitches[c]+x] = rgb[c]
                twins[c][y*pitches[c]+x] = rgb[c]
                thirds[c][y*pitches[c]+x] = rgb[c]
        assert run(table, *planes, *pitches, width, height) == 0
        assert dense_run(dense, *twins, *pitches, width, height) == 0
        assert run2(table2, *thirds, *pitches, width, height) == 0
        assert [bytes(x) for x in thirds] == [bytes(x) for x in planes], 'compact2 must equal compact'
        error = against_dense = 0
        for i, rgb in enumerate(pixels):
            y, x = divmod(i, width)
            actual = [planes[c][y*pitches[c]+x] for c in range(3)]
            other = [twins[c][y*pitches[c]+x] for c in range(3)]
            error = max(error, max(abs(a-b) for a, b in zip(actual, reference(cube, rgb))))
            against_dense = max(against_dense, max(abs(a-b) for a, b in zip(actual, other)))
            if name == 'identity':
                assert actual == list(rgb), (rgb, actual)
        assert error <= 1, (name, error)
        for c, s in enumerate(pitches):
            for y in range(height):
                assert bytes(planes[c][y*s+width:(y+1)*s]) == bytes([171])*(s-width)
        previous = [bytes(x) for x in planes]
        for w, h, rs in [(0, 4, 1019), (1921, 1, 2000), (1009, 1081, 1019), (1009, 4, 1008)]:
            assert run(table, *planes, rs, pitches[1], pitches[2], w, h) == -22
            assert run2(table2, *planes, rs, pitches[1], pitches[2], w, h) == -22
            assert [bytes(x) for x in planes] == previous
        report[name] = {'max_error_vs_float': error, 'max_diff_vs_dense_kernel': against_dense}
    print(json.dumps({'pixels_per_cube': width*height, 'cubes': report}))


if __name__ == '__main__':
    main()
