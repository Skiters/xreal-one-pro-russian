"""Compare diagnostic RPC captures. Does not establish physical eye routing."""
import argparse
import json
from pathlib import Path


def pgm(path, width, height):
    data = path.read_bytes()
    header = f"P5\n{width} {height}\n255\n".encode()
    if not data.startswith(header) or len(data) != len(header)+width*height:
        raise ValueError("Incomplete or unexpected capture: " + str(path))
    return data[len(header):]


def analyze(folder):
    width, height = 1920, 1080
    metadata = json.loads((folder/'real3d-capture.json').read_text(encoding='utf-8'))
    bits = metadata['fractional_bits']
    if metadata['capture_rc'] != 0 or type(bits) is not int or bits not in (2, 3, 4):
        raise ValueError("Failed capture or unsupported displacement format.")
    scale = 1 << bits
    source = pgm(folder/'real3d-input.pgm', width, height)
    output = pgm(folder/'real3d-warped.pgm', width, height)
    depth = pgm(folder/'real3d-displacement.pgm', 480, 270)
    mismatches = changed = 0
    for y in range(height):
        row = y*width
        zrow = (y//4)*480
        for x in range(width):
            offset = depth[zrow+x//4]
            index, fraction = divmod(x*scale+offset, scale)
            a = source[row+min(index, width-1)]
            b = source[row+min(index+1, width-1)]
            expected = (a*(scale-fraction)+b*fraction+scale//2)//scale
            actual = output[row+x]
            mismatches += actual != expected
            changed += actual != source[row+x]
    return dict(pixels=width*height, changed_pixels=changed,
                reference_mismatches=mismatches,
                displacement_luma_pixels=[min(depth)/scale, max(depth)/scale],
                fractional_bits=bits, matches_reference=mismatches == 0,
                physical_stereo_confirmed=False,
                limitation="Input/warped RPC buffers only; final eye routing and optics remain unverified.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = json.dumps(analyze(args.folder), indent=2)
    if args.output:
        args.output.write_text(result+'\n', encoding='utf-8')
    print(result)
