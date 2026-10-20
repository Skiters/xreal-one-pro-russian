"""Independent floating-point trilinear oracle; accepts a native lut_fast library."""
import argparse
import ctypes as C
import io
import json
from pathlib import Path
import random
import zipfile


def reference(cube, rgb):
    position = [x*16/255 for x in rgb]
    low = [min(int(x),15) for x in position]
    frac = [x-i for x,i in zip(position,low)]
    sums = [0.,0.,0.]
    for b in (0,1):
        for g in (0,1):
            for r in (0,1):
                weight = 1.
                for index, corner in enumerate((r,g,b)):
                    weight *= frac[index] if corner else 1-frac[index]
                start = (((low[2]+b)*17+low[1]+g)*17+low[0]+r)*3
                for channel in range(3):
                    sums[channel] += cube[start+channel]*weight/256
    return [int(x+.5) for x in sums]


def main():
    p=argparse.ArgumentParser()
    p.add_argument('library')
    p.add_argument('--bin',type=Path)
    args=p.parse_args()
    lib=C.CDLL(str(Path(args.library).resolve()))
    build=lib.xreal_build_rgb_table_range
    build.argtypes=[C.POINTER(C.c_ushort),C.POINTER(C.c_uint64),C.c_uint,C.c_uint]
    run=lib.xreal_full_rgb_lut
    run.argtypes=[C.POINTER(C.c_uint64),*[C.POINTER(C.c_ubyte)]*3,*[C.c_uint]*5]
    lib.xreal_rgb_table_bytes.restype=C.c_uint64
    table=(C.c_uint64*(lib.xreal_rgb_table_bytes()//8))()
    rng=random.Random(20261020)
    cubes={
        'identity':[round(v*65280/16) for b in range(17) for g in range(17)
                    for r in range(17) for v in (r,g,b)],
        'cross-channel':[round(v*65280/32) for b in range(17) for g in range(17)
                         for r in range(17) for v in (g+b,r+b,r+g)],
        'random':[rng.randrange(256)*256 for _ in range(14739)]}
    if args.bin:
        data=args.bin.read_bytes()
        with zipfile.ZipFile(io.BytesIO(data[64:].translate(bytes(i^0x66 for i in range(256))))) as z:
            for name in z.namelist():
                if name.endswith('.cube'):
                    rows=[]
                    for line in z.read(name).decode().splitlines():
                        try: values=[float(v) for v in line.split()]
                        except ValueError: continue
                        if len(values)==3: rows.extend(round(v*255)*256 for v in values)
                    if len(rows)==14739: cubes[name]=rows
    errors={}
    for name,cube in cubes.items():
        data=(C.c_ushort*len(cube))(*cube)
        assert build(data,table,0,17)==0
        # Unequal pitches, non-vector width, random pixels and all cube corners.
        width,height=1009,4
        pitches=[1019,1031,1049]
        planes=[(C.c_ubyte*(s*height))(*([171]*(s*height))) for s in pitches]
        pixels=[(r,g,b) for r in (0,255) for g in (0,255) for b in (0,255)]
        pixels += [tuple(rng.randrange(256) for _ in range(3)) for _ in range(width*height-8)]
        for i,rgb in enumerate(pixels):
            y,x=divmod(i,width)
            for c in range(3): planes[c][y*pitches[c]+x]=rgb[c]
        assert run(table,*planes,*pitches,width,height)==0
        error=0
        for i,rgb in enumerate(pixels):
            y,x=divmod(i,width)
            actual=[planes[c][y*pitches[c]+x] for c in range(3)]
            error=max(error,max(abs(a-b) for a,b in zip(actual,reference(cube,rgb))))
            if name=='identity': assert actual==list(rgb),(rgb,actual)
        assert error<=1,(name,error)
        errors[name]=error
        for c,s in enumerate(pitches):
            for y in range(height):
                assert bytes(planes[c][y*s+width:(y+1)*s])==bytes([171])*(s-width)
        previous=[bytes(x) for x in planes]
        for w,h,rs in [(0,4,1019),(1921,1,2000),(1009,1081,1019),(1009,4,1008)]:
            assert run(table,*planes,rs,pitches[1],pitches[2],w,h)==-22
            assert [bytes(x) for x in planes]==previous
    print(json.dumps({'pixels_per_cube':width*height,'max_error_rgb8':errors}))


if __name__=='__main__':
    main()
