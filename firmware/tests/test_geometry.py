"""Execute ARM64 kernels; compare physical luma/chroma displacement independently."""
import random
from itertools import product
from arm64_helper import run

rng=random.Random(62026)
cases=0
for ratio,bits in product((4,2),(2,3,4)):
    width,height=16*ratio,4
    stride=width+9
    source=rng.randbytes(stride*height)
    for value in range(256):
        depth=bytes([value])*32
        # Same physical displacement for Y and subsampled U/V.
        # Vendor RPC carries an already-scaled unsigned fixed-point map.
        displacement=value/(1<<bits)/(2 if ratio==2 else 1)
        expected=bytearray([238])*(stride*height)
        for y in range(height):
            for x in range(width):
                pos=min(x+displacement,width-1)
                left=int(pos)
                alpha=pos-left
                a,b=source[y*stride+left],source[y*stride+min(left+1,width-1)]
                expected[y*stride+x]=int(a*(1-alpha)+b*alpha+.5)
        for symbol in ('real3d_warp_plane_for_test','real3d_warp_cells_for_test'):
            actual=run(symbol,source,depth,width,height,stride,16,2,fractional_bits=bits)
            assert actual==bytes(expected),(ratio,bits,value,symbol)
        cases+=1
print(f'PASS {cases} ARM64 geometry cases: all depth values, luma/chroma scale, fractional chroma, padding')
