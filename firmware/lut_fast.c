/* Full RGB cube, not three diagonal curves.
 * Expand R and G once at mode initialization, retain 17 B slices in Q4.
 * A pixel needs two table reads and one packed interpolation. 8.5 MiB.
 * Q4 keeps intermediate rounding below one RGB8 level versus the cube. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
#define TABLE_WORDS (17U * 256U * 256U)
#define LANES (0xfffULL | (0xfffULL << 20) | (0xfffULL << 40))

unsigned long long xreal_rgb_table_bytes(void) {
    return (u64)TABLE_WORDS * sizeof(u64);
}

int xreal_build_rgb_table_range(const u16 *cube, u64 *table,
                                 u32 begin_b, u32 end_b) {
    if (!cube || !table || begin_b > end_b || end_b > 17) return -22;
    for (u32 b = begin_b; b < end_b; ++b) {
        for (u32 g = 0; g < 256; ++g) {
            u32 gi = g*16/255, gf = g*16-gi*255;
            if (gi == 16) { gi=15; gf=255; }
            for (u32 r = 0; r < 256; ++r) {
                u32 ri=r*16/255, rf=r*16-ri*255;
                if (ri == 16) { ri=15; rf=255; }
                u32 base=((b*17+gi)*17+ri)*3;
                u64 packed=0;
                for (u32 c=0; c<3; ++c) {
                    u32 a=(cube[base+c]*(255-rf)+cube[base+3+c]*rf+127)/255;
                    u32 d=(cube[base+51+c]*(255-rf)+cube[base+54+c]*rf+127)/255;
                    u32 value=(a*(255-gf)+d*gf+127)/255;
                    packed |= (u64)((value+8)/16) << (20*c);
                }
                table[(b*256+g)*256+r]=packed;
            }
        }
    }
    return 0;
}

int xreal_full_rgb_lut(const u64 *table, u8 *r, u8 *g, u8 *b,
                       u32 rs, u32 gs, u32 bs, u32 width, u32 height) {
    if (!table || !r || !g || !b || !width || !height ||
        width>1920 || height>1080 || rs<width || gs<width || bs<width ||
        rs>4096 || gs>4096 || bs>4096) return -22;
    for (u32 y=0; y<height; ++y) {
        u8 *rp=r+(u64)y*rs, *gp=g+(u64)y*gs, *bp=b+(u64)y*bs;
        for (u32 x=0; x<width; ++x) {
            u32 blue=bp[x]*16, bi=blue/255, frac=blue-bi*255;
            if (bi==16) { bi=15; frac=255; }
            u32 weight=(frac*256+127)/255;
            u32 index=(bi<<16) | ((u32)gp[x]<<8) | rp[x];
            u64 value=((table[index]*(256-weight) +
                         table[index+65536]*weight + (128ULL | (128ULL<<20) |
                                                     (128ULL<<40))) >> 8) & LANES;
            rp[x]=(u8)(((value & 0xfff)+8)>>4);
            gp[x]=(u8)((((value>>20)&0xfff)+8)>>4);
            bp[x]=(u8)((((value>>40)&0xfff)+8)>>4);
        }
    }
    return 0;
}
