"""Compare ARM64 work, not hardware timing/FPS; require identical output bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random


def compare(before, after):
    # The helper also serves existing single-build tests. This command always
    # supplies two explicit libraries and needs no release/default artifact.
    os.environ['REAL3D_LIB'] = str(after)
    from arm64_helper import load_program, run
    programs = [load_program(path) for path in (before, after)]
    rng = random.Random(20261103)
    results = []
    for ratio in (4, 2):
        width, height, depth_width, depth_height = 480*ratio, ratio*2, 480, 2
        stride = 512*ratio
        source = rng.randbytes(stride*height)
        for bits in (2, 3, 4):
            for kind in ('zero', 'integer', 'mixed', 'fractional', 'edge'):
                depth = rng.randbytes(depth_width*depth_height)
                if kind == 'zero':
                    depth = bytes(len(depth))
                elif kind == 'integer':
                    fraction_mask = (1 << (bits+(ratio == 2)))-1
                    depth = bytes(v & ~fraction_mask for v in depth)
                elif kind == 'fractional':
                    depth = bytes(v | 1 for v in depth)
                elif kind == 'edge':
                    depth = bytes([255])*len(depth)
                outputs, counts = [], []
                for program in programs:
                    stats = {}
                    outputs.append(run('real3d_warp_cells_for_test', source, depth,
                                       width, height, stride, depth_width, depth_height,
                                       fractional_bits=bits, program=program, stats=stats))
                    counts.append(stats['instructions'])
                assert outputs[0] == outputs[1], (ratio, bits, kind)
                # Exercise the same partition boundaries used by worker jobs.
                for begin, end in ((0, 1), (1, 2), (1, 1)):
                    partition = run('real3d_warp_cells_range_for_test', source, depth,
                                    width, height, stride, depth_width, depth_height,
                                    begin, end, bits, program=programs[1])
                    expected = bytearray([238]) * (stride*height)
                    expected[begin*ratio*stride:end*ratio*stride] = outputs[0][begin*ratio*stride:end*ratio*stride]
                    assert partition == expected, (ratio, bits, kind, begin, end)
                results.append(dict(pixels_per_cell=ratio, fractional_bits=bits,
                                    depth=kind, before_instructions=counts[0],
                                    after_instructions=counts[1],
                                    reduction_percent=round(100*(1-counts[1]/counts[0]), 2)))
    return dict(before_sha256=hashlib.sha256(Path(before).read_bytes()).hexdigest(),
                after_sha256=hashlib.sha256(Path(after).read_bytes()).hexdigest(),
                cases=results, output_exact=True,
                limitation='Dynamic ARM64 instruction counts, not cycles, FPS or hardware speedup. '
                           'Different instruction costs, caches, scheduling and NPU time are not measured.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.before, args.after)
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    for ratio in (4, 2):
        cases = [r for r in report['cases'] if r['pixels_per_cell'] == ratio]
        print('Pixels/cell:', ratio, '; dynamic instruction reduction by depth case:',
              {k: [r['reduction_percent'] for r in cases if r['depth'] == k]
               for k in ('zero', 'integer', 'mixed', 'fractional', 'edge')})
    print('PASS 30 full-width comparisons and 90 worker partitions; outputs identical. Not hardware FPS.')
