"""perf25 symmetric two-eye warp: oracle for warp_eye_range (dir +1 / -1)."""
import ctypes as C, random, sys
sys.path.insert(0, __file__.rsplit('/',1)[0])

def depth_shift(v, width, dw, bits): return v << (5 - bits - (1 if width//dw==2 else 0))
def sample_q5(row, q, width):
    if q <= 0: return row[0]
    x, f = q//32, q & 31
    if x >= width-1: return row[width-1]
    return (row[x]*(32-f) + row[x+1]*f + 16) >> 5
HFLAT = 4

def oracle(src, depth, width, height, stride, dw, dh, bits, begin, end, direction, fill):
    pixels, rows = width//dw, height//dh
    den = 2*rows; den_shift = {4:3,2:2}[rows]
    out = bytearray([fill])*(stride*height)
    for zy in range(begin, end):
        here=[depth_shift(depth[zy*dw+zx],width,dw,bits) for zx in range(dw)]
        up=[depth_shift(depth[(zy-1 if zy else zy)*dw+zx],width,dw,bits) for zx in range(dw)]
        dn=[depth_shift(depth[(zy+1 if zy+1<dh else zy)*dw+zx],width,dw,bits) for zx in range(dw)]
        for ry in range(rows):
            num=2*ry+1-rows; other=up if num<0 else dn; vw=abs(num)
            blend=[(here[zx]*(den-vw)+other[zx]*vw+rows)>>den_shift for zx in range(dw)]
            y=zy*rows+ry; row=src[y*stride:y*stride+width]
            for zx in range(dw):
                x=zx*pixels; mid=blend[zx]; left=blend[zx-1 if zx else zx]; right=blend[zx+1 if zx+1<dw else zx]
                if abs(left-mid)>HFLAT or abs(right-mid)>HFLAT:
                    if pixels==4: sh=[(5*mid+3*left+4)>>3,(7*mid+left+4)>>3,(7*mid+right+4)>>3,(5*mid+3*right+4)>>3]
                    else: sh=[(3*mid+left+2)>>2,(3*mid+right+2)>>2]
                else: sh=[mid]*pixels
                for k in range(pixels):
                    out[y*stride+x+k]=sample_q5(row,(x+k)*32 + direction*sh[k], width)
    return bytes(out)

def main(path):
    lib=C.CDLL(path)
    fn=lib.real3d_warp_eye_range_for_test
    fn.argtypes=[C.c_char_p,C.c_char_p,C.c_char_p]+[C.c_uint]*9+[C.c_int]
    rng=random.Random(20261112); cases=0
    for ratio in (4,2):
        dw,dh=60,6; width,height,stride=dw*ratio,dh*ratio,dw*ratio+16
        for bits in (2,3,4):
            for kind in ('random','step','flat'):
                src=bytes(rng.randrange(256) for _ in range(stride*height))
                if kind=='random': depth=bytes(rng.randrange(256) for _ in range(dw*dh))
                elif kind=='step': depth=bytes(200 if zx>dw//2 else 10 for _ in range(dh) for zx in range(dw))
                else: depth=bytes([rng.randrange(256)])*(dw*dh)
                for direction in (+1,-1):
                    for begin,end in ((0,dh),(1,4)):
                        dst=C.create_string_buffer(bytes([238])*(stride*height),stride*height)
                        assert fn(src,dst,depth,width,height,stride,stride,dw,dh,begin,end,bits,direction)==0
                        assert dst.raw==oracle(src,depth,width,height,stride,dw,dh,bits,begin,end,direction,238),(ratio,bits,kind,direction,begin,end)
                        cases+=1
    # invalid
    bad=C.create_string_buffer(64); assert fn(b'\0'*64*12,bad,b'\0'*16*4,48,12,64,64,16,4,0,4,3,1)==-22
    print(f'perf25 two-eye warp oracle passed: {cases} cases')

if __name__=='__main__': main(sys.argv[1])
