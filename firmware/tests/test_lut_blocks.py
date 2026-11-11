"""blocks kernel: accuracy versus the float trilinear reference and versus
compact16 on video-like synthetic content; smooth-tile share; NEON/portable
outputs can be compared with --dump."""
import argparse
import ctypes as C
import json
import math
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lut import reference, cubes_from_bin


def frames(rng, w, h):
    out = {}
    # Smooth film-like: soft gradients + low sensor noise.
    px = []
    for y in range(h):
        for x in range(w):
            base = (128 + 90*math.sin(x/97 + y/151), 110 + 80*math.cos(x/131 - y/83), 90 + 70*math.sin((x+y)/173))
            px.append(tuple(min(255, max(0, int(v + rng.randrange(-1, 2)))) for v in base))
    out['film-smooth'] = px
    # Same with hard edges (objects, subtitles).
    px2 = []
    for y in range(h):
        for x in range(w):
            p = px[y*w+x]
            if (x // 37 + y // 23) % 5 == 0:
                p = (235, 235, 235) if (x+y) % 3 else (16, 16, 16)
            px2.append(p)
    out['film-edges'] = px2
    out['grain'] = [tuple(min(255, max(0, v + rng.randrange(-6, 7))) for v in p) for p in px]
    out['noise'] = [tuple(rng.randrange(256) for _ in range(3)) for _ in range(w*h)]
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument('library')
    p.add_argument('--bin', type=Path)
    p.add_argument('--dump')
    args = p.parse_args()
    lib = C.CDLL(str(Path(args.library).resolve()))
    lib.xreal_compact_table_bytes.restype = C.c_uint64
    build = lib.xreal_build_compact_table_range
    build.argtypes = [C.POINTER(C.c_ushort), C.POINTER(C.c_uint32), C.c_uint, C.c_uint]
    run16 = lib.xreal_compact16_rgb_lut
    run16.argtypes = [C.POINTER(C.c_uint32), *[C.POINTER(C.c_ubyte)]*3, *[C.c_uint]*5]
    blocks = lib.xreal_blocks_rgb_lut
    blocks.argtypes = run16.argtypes
    smooth = C.c_uint64.in_dll(lib, 'xreal_blocks_smooth_tiles')
    tiles = C.c_uint64.in_dll(lib, 'xreal_blocks_tiles')
    table = (C.c_uint32*(lib.xreal_compact_table_bytes()//4))()
    rng = random.Random(20261111)
    cubes = {'identity': [round(v*65280/16) for b in range(17) for g in range(17)
                          for r in range(17) for v in (r, g, b)]}
    if args.bin:
        cubes.update(cubes_from_bin(args.bin))
    w, h = 243, 61                      # odd sizes exercise the tails
    pitches = [256, 272, 288]
    report = {}
    for name, cube in cubes.items():
        assert build((C.c_ushort*len(cube))(*cube), table, 0, 17) == 0
        for kind, pixels in frames(rng, w, h).items():
            def planes():
                ps = [(C.c_ubyte*(s*h))(*([171]*(s*h))) for s in pitches]
                for i, rgb in enumerate(pixels):
                    y, x = divmod(i, w)
                    for c in range(3):
                        ps[c][y*pitches[c]+x] = rgb[c]
                return ps
            a, bb = planes(), planes()
            assert run16(table, *a, *pitches, w, h) == 0
            smooth.value = tiles.value = 0
            assert blocks(table, *bb, *pitches, w, h) == 0
            err_ref = err_c16 = 0
            for i, rgb in enumerate(pixels[::7]):
                i *= 7
                y, x = divmod(i, w)
                o = [bb[c][y*pitches[c]+x] for c in range(3)]
                q = [a[c][y*pitches[c]+x] for c in range(3)]
                err_ref = max(err_ref, max(abs(u-v) for u, v in zip(o, reference(cube, rgb))))
                err_c16 = max(err_c16, max(abs(u-v) for u, v in zip(o, q)))
                if name == 'identity':
                    assert o == list(rgb), (kind, rgb, o)
            for c, s in enumerate(pitches):
                for y in range(h):
                    assert bytes(bb[c][y*s+w:(y+1)*s]) == bytes([171])*(s-w)
            if name.endswith('.cube'):
                assert err_ref <= 2, (name, kind, err_ref)
            if args.dump:
                with open(args.dump, 'ab') as f:
                    f.write(b''.join(bytes(x) for x in bb))
            report.setdefault(name.rsplit('/', 1)[-1], {})[kind] = {
                'max_err_vs_float': err_ref, 'max_diff_vs_compact16': err_c16,
                'smooth_tile_share': round(smooth.value / max(1, tiles.value), 3)}
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
