"""Known translated ramps exercise the capture check without using the warp code."""
from pathlib import Path
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_real3d_capture import analyze


def save(folder, name, width, height, pixels):
    (folder/name).write_bytes(f'P5\n{width} {height}\n255\n'.encode()+pixels)


with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp)
    (folder/'real3d-capture.json').write_text(
        '{"fractional_bits":2,"capture_rc":0}', encoding='utf-8')
    # Interior ramp with slope four: Q2 map value five gives +5 intensity.
    # Periodic wrapping and the final edge are independently interpolated.
    row = bytes((x % 64)*4 for x in range(1920))
    shifted = bytes(round(row[min(x+1,1919)]*.75+row[min(x+2,1919)]*.25)
                    for x in range(1920))
    save(folder, 'real3d-input.pgm',1920,1080,row*1080)
    save(folder, 'real3d-warped.pgm',1920,1080,shifted*1080)
    save(folder, 'real3d-displacement.pgm',480,270,bytes([5])*129600)
    for bits in (2,3,4):
        (folder/'real3d-capture.json').write_text(
            json.dumps(dict(fractional_bits=bits,capture_rc=0)), encoding='utf-8')
        save(folder, 'real3d-displacement.pgm',480,270,bytes([5 << (bits-2)])*129600)
        result = analyze(folder)
        assert result['matches_reference'] and result['changed_pixels']>1000000
    assert result['displacement_luma_pixels']==[1.25,1.25]
    assert result['physical_stereo_confirmed'] is False
    target = folder/'real3d-warped.pgm'
    raw = bytearray(target.read_bytes()); raw[-5] ^= 1
    target.write_bytes(raw)
    assert analyze(folder)['reference_mismatches']==1
    target.write_bytes(raw[:-1])
    try:
        analyze(folder)
    except ValueError:
        pass
    else:
        raise AssertionError('Truncated capture accepted')
print('PASS capture analyzer: independent ramp, fractional shift, corruption and truncation')
