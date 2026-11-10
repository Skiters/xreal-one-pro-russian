/* Full RGB cube, not three diagonal curves.
 * Expand R and G once at mode initialization, retain 17 B slices in Q2.
 * Two 32-bit table reads per pixel. 4.25 MiB (half the former table).
 * Q2 and rounded interpolation stay within one RGB8 level versus the cube. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef u32 pixels4 __attribute__((vector_size(16)));
typedef u8 bytes4 __attribute__((vector_size(4)));
#define TABLE_WORDS (17U * 256U * 256U)


unsigned long long xreal_rgb_table_bytes(void) {
    return (u64)(TABLE_WORDS+256) * sizeof(u32);
}

int xreal_build_rgb_table_range(const u16 *cube, u64 *table,
                                 u32 begin_b, u32 end_b) {
    if (!cube || !table || begin_b > end_b || end_b > 17) return -22;
    u32 *compact=(u32 *)table;
    if (!begin_b) for (u32 blue=0;blue<256;++blue) {
        u32 v=blue*16, slice=v/255, f=v-slice*255;
        if(slice==16){slice=15;f=255;}
        compact[TABLE_WORDS+blue]=(slice<<16) | ((f*256+127)/255);
    }
    for (u32 b = begin_b; b < end_b; ++b) {
        for (u32 g = 0; g < 256; ++g) {
            u32 gi = g*16/255, gf = g*16-gi*255;
            if (gi == 16) { gi=15; gf=255; }
            for (u32 r = 0; r < 256; ++r) {
                u32 ri=r*16/255, rf=r*16-ri*255;
                if (ri == 16) { ri=15; rf=255; }
                u32 base=((b*17+gi)*17+ri)*3;
                u32 packed=0;
                for (u32 c=0; c<3; ++c) {
                    u32 a=(cube[base+c]*(255-rf)+cube[base+3+c]*rf+127)/255;
                    u32 d=(cube[base+51+c]*(255-rf)+cube[base+54+c]*rf+127)/255;
                    u32 value=(a*(255-gf)+d*gf+127)/255;
                    packed |= ((value+32)/64) << (10*c);
                }
                compact[(b*256+g)*256+r]=packed;
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
    const u32 *compact=(const u32 *)table;
    for (u32 y=0; y<height; ++y) {
        u8 *rp=r+(u64)y*rs, *gp=g+(u64)y*gs, *bp=b+(u64)y*bs;
        u32 x=0;
        for (; x+4<=width; x+=4) {
            pixels4 a,d,weight;
            for (u32 lane=0;lane<4;++lane) {
                u32 mapping=compact[TABLE_WORDS+bp[x+lane]];
                u32 index=(mapping&0xffff0000) |
                          ((u32)gp[x+lane]<<8) | rp[x+lane];
                a[lane]=compact[index];d[lane]=compact[index+65536];
                weight[lane]=mapping&511;
            }
            pixels4 inverse=256-weight;
            pixels4 red=((a&1023)*inverse+(d&1023)*weight+512)>>10;
            pixels4 green=(((a>>10)&1023)*inverse+((d>>10)&1023)*weight+512)>>10;
            pixels4 blue=((a>>20)*inverse+(d>>20)*weight+512)>>10;
            bytes4 rr=__builtin_convertvector(red,bytes4);
            bytes4 gg=__builtin_convertvector(green,bytes4);
            bytes4 bb=__builtin_convertvector(blue,bytes4);
            __builtin_memcpy(rp+x,&rr,4);
            __builtin_memcpy(gp+x,&gg,4);
            __builtin_memcpy(bp+x,&bb,4);
        }
        for (; x<width; ++x) {
            u32 mapping=compact[TABLE_WORDS+bp[x]], weight=mapping&511;
            u32 index=(mapping&0xffff0000) | ((u32)gp[x]<<8) | rp[x];
            u32 a=compact[index], d=compact[index+65536];
            rp[x]=(u8)(((a&1023)*(256-weight)+(d&1023)*weight+512)>>10);
            gp[x]=(u8)((((a>>10)&1023)*(256-weight)+((d>>10)&1023)*weight+512)>>10);
            bp[x]=(u8)(((a>>20)*(256-weight)+(d>>20)*weight+512)>>10);
        }
    }
    return 0;
}

/* Compact cache-resident variant (perf branch).
 * The table above is 4.25 MiB and is read at random positions; the device
 * (4x Cortex-A53 @ 1.27 GHz, 512 KiB shared L2, 4 KiB pages, no THP) misses
 * L2 and the TLB on most pixels. Here only R is expanded, for each of the
 * 17x17 (B,G) cube nodes: 17*17*256 words = 289 KiB, which fits in L2.
 * G and B use exact 1/255 weights, so the only rounding is the Q2 entry
 * (<= 1/8 RGB8 level) and the final division: <= 1 level versus the cube.
 * Words after the table: 256 G-axis and 256 B-axis maps, (offset<<8)|frac. */
#define COMPACT_WORDS (17U * 17U * 256U)
#define COMPACT_G_STEP 256U
#define COMPACT_B_STEP (17U * 256U)

unsigned long long xreal_compact_table_bytes(void) {
    return (u64)(COMPACT_WORDS + 512U) * sizeof(u32);
}

int xreal_build_compact_table_range(const u16 *cube, u32 *table,
                                    u32 begin_b, u32 end_b) {
    if (!cube || !table || begin_b > end_b || end_b > 17) return -22;
    if (!begin_b) for (u32 v = 0; v < 256; ++v) {
        u32 i = v*16/255, f = v*16 - i*255;
        if (i == 16) { i = 15; f = 255; }
        table[COMPACT_WORDS+v] = ((i*COMPACT_G_STEP) << 8) | f;
        table[COMPACT_WORDS+256+v] = ((i*COMPACT_B_STEP) << 8) | f;
    }
    for (u32 b = begin_b; b < end_b; ++b)
        for (u32 g = 0; g < 17; ++g)
            for (u32 r = 0; r < 256; ++r) {
                u32 ri = r*16/255, rf = r*16 - ri*255;
                if (ri == 16) { ri = 15; rf = 255; }
                u32 base = ((b*17+g)*17+ri)*3, packed = 0;
                for (u32 c = 0; c < 3; ++c) {
                    u32 v = (cube[base+c]*(255-rf) + cube[base+3+c]*rf + 127)/255;
                    packed |= ((v+32)/64) << (10*c);
                }
                table[(b*17+g)*256+r] = packed;
            }
    return 0;
}

/* (x0*(255-wb) + x1*wb) has 255*255*4 units per RGB8 level; <= 66.3e6. */
#define COMPACT_DIVISOR 260100U
#define COMPACT_CHANNEL(p00,p01,p10,p11,shift,ig,wg,ib,wb) \
    ((((((p00)>>(shift))&1023)*(ig) + (((p01)>>(shift))&1023)*(wg))*(ib) + \
      ((((p10)>>(shift))&1023)*(ig) + (((p11)>>(shift))&1023)*(wg))*(wb) + \
      COMPACT_DIVISOR/2) / COMPACT_DIVISOR)

/* prefetch=1: while converting a 4-pixel group, request the table lines of
 * the group 8 pixels ahead (PRFM, no fault on any address). The in-order A53
 * otherwise stalls on each table miss; whether the extra instructions pay
 * off depends on L2 pressure, so the adapter's autotune decides. */
static inline __attribute__((always_inline))
int compact_impl(const u32 *table, u8 *r, u8 *g, u8 *b,
                 u32 rs, u32 gs, u32 bs, u32 width, u32 height, int prefetch) {
    if (!table || !r || !g || !b || !width || !height ||
        width>1920 || height>1080 || rs<width || gs<width || bs<width ||
        rs>4096 || gs>4096 || bs>4096) return -22;
    const u32 *gmap = table + COMPACT_WORDS, *bmap = gmap + 256;
    for (u32 y = 0; y < height; ++y) {
        u8 *rp = r+(u64)y*rs, *gp = g+(u64)y*gs, *bp = b+(u64)y*bs;
        /* Flat areas (letterbox bars, UI, sky) repeat whole 4-pixel groups. */
        u32 last_r = 0, last_g = 0, last_b = 0, out_r = 0, out_g = 0, out_b = 0;
        int have_last = 0;
        u32 x = 0;
        for (; x+4 <= width; x += 4) {
            u32 kr, kg, kb;
            __builtin_memcpy(&kr, rp+x, 4);
            __builtin_memcpy(&kg, gp+x, 4);
            __builtin_memcpy(&kb, bp+x, 4);
            if (prefetch && x+12 <= width)
                for (u32 lane = 0; lane < 4; ++lane) {
                    u32 index = (gmap[gp[x+8+lane]] >> 8) + (bmap[bp[x+8+lane]] >> 8) + rp[x+8+lane];
                    __builtin_prefetch(table+index, 0, 3);
                    __builtin_prefetch(table+index+COMPACT_G_STEP, 0, 3);
                    __builtin_prefetch(table+index+COMPACT_B_STEP, 0, 3);
                    __builtin_prefetch(table+index+COMPACT_B_STEP+COMPACT_G_STEP, 0, 3);
                }
            if (have_last && kr == last_r && kg == last_g && kb == last_b) {
                __builtin_memcpy(rp+x, &out_r, 4);
                __builtin_memcpy(gp+x, &out_g, 4);
                __builtin_memcpy(bp+x, &out_b, 4);
                continue;
            }
            pixels4 p00, p01, p10, p11, wg, wb;
            for (u32 lane = 0; lane < 4; ++lane) {
                u32 gm = gmap[gp[x+lane]], bm = bmap[bp[x+lane]];
                u32 index = (gm >> 8) + (bm >> 8) + rp[x+lane];
                p00[lane] = table[index];
                p01[lane] = table[index+COMPACT_G_STEP];
                p10[lane] = table[index+COMPACT_B_STEP];
                p11[lane] = table[index+COMPACT_B_STEP+COMPACT_G_STEP];
                wg[lane] = gm & 255; wb[lane] = bm & 255;
            }
            pixels4 ig = 255-wg, ib = 255-wb;
            pixels4 red = COMPACT_CHANNEL(p00,p01,p10,p11,0,ig,wg,ib,wb);
            pixels4 green = COMPACT_CHANNEL(p00,p01,p10,p11,10,ig,wg,ib,wb);
            pixels4 blue = COMPACT_CHANNEL(p00,p01,p10,p11,20,ig,wg,ib,wb);
            bytes4 rr = __builtin_convertvector(red, bytes4);
            bytes4 gg = __builtin_convertvector(green, bytes4);
            bytes4 bb = __builtin_convertvector(blue, bytes4);
            __builtin_memcpy(rp+x, &rr, 4);
            __builtin_memcpy(gp+x, &gg, 4);
            __builtin_memcpy(bp+x, &bb, 4);
            last_r = kr; last_g = kg; last_b = kb; have_last = 1;
            __builtin_memcpy(&out_r, &rr, 4);
            __builtin_memcpy(&out_g, &gg, 4);
            __builtin_memcpy(&out_b, &bb, 4);
        }
        for (; x < width; ++x) {
            u32 gm = gmap[gp[x]], bm = bmap[bp[x]];
            u32 index = (gm >> 8) + (bm >> 8) + rp[x];
            u32 p00 = table[index], p01 = table[index+COMPACT_G_STEP];
            u32 p10 = table[index+COMPACT_B_STEP];
            u32 p11 = table[index+COMPACT_B_STEP+COMPACT_G_STEP];
            u32 wg = gm & 255, wb = bm & 255, ig = 255-wg, ib = 255-wb;
            rp[x] = (u8)COMPACT_CHANNEL(p00,p01,p10,p11,0,ig,wg,ib,wb);
            gp[x] = (u8)COMPACT_CHANNEL(p00,p01,p10,p11,10,ig,wg,ib,wb);
            bp[x] = (u8)COMPACT_CHANNEL(p00,p01,p10,p11,20,ig,wg,ib,wb);
        }
    }
    return 0;
}

int xreal_compact_rgb_lut(const u32 *table, u8 *r, u8 *g, u8 *b,
                          u32 rs, u32 gs, u32 bs, u32 width, u32 height) {
    return compact_impl(table, r, g, b, rs, gs, bs, width, height, 0);
}

/* Same table and bit-identical output as xreal_compact_rgb_lut. */
int xreal_compact_pf_rgb_lut(const u32 *table, u8 *r, u8 *g, u8 *b,
                             u32 rs, u32 gs, u32 bs, u32 width, u32 height) {
    return compact_impl(table, r, g, b, rs, gs, bs, width, height, 1);
}

/* Compact2: same values and arithmetic as the compact kernel (bit-identical
 * output), laid out [b][r][g] so that each pixel's G and G+1 neighbours sit
 * in adjacent words: two cache lines per pixel instead of four. The 8-pixel
 * body issues all 32 table loads before any arithmetic, which suits the
 * in-order Cortex-A53 (it stalls on the first use of a missing load). */
#define C2_R_STEP 17U
#define C2_B_STEP (256U * 17U)

unsigned long long xreal_compact2_table_bytes(void) {
    return (u64)(COMPACT_WORDS + 512U) * sizeof(u32);
}

int xreal_build_compact2_table_range(const u16 *cube, u32 *table,
                                     u32 begin_b, u32 end_b) {
    if (!cube || !table || begin_b > end_b || end_b > 17) return -22;
    if (!begin_b) for (u32 v = 0; v < 256; ++v) {
        u32 i = v*16/255, f = v*16 - i*255;
        if (i == 16) { i = 15; f = 255; }
        table[COMPACT_WORDS+v] = (i << 8) | f;                       /* G: +i */
        table[COMPACT_WORDS+256+v] = ((i*C2_B_STEP) << 8) | f;       /* B */
    }
    for (u32 b = begin_b; b < end_b; ++b)
        for (u32 r = 0; r < 256; ++r) {
            u32 ri = r*16/255, rf = r*16 - ri*255;
            if (ri == 16) { ri = 15; rf = 255; }
            for (u32 g = 0; g < 17; ++g) {
                u32 base = ((b*17+g)*17+ri)*3, packed = 0;
                for (u32 c = 0; c < 3; ++c) {
                    u32 v = (cube[base+c]*(255-rf) + cube[base+3+c]*rf + 127)/255;
                    packed |= ((v+32)/64) << (10*c);
                }
                table[b*C2_B_STEP + r*C2_R_STEP + g] = packed;
            }
        }
    return 0;
}

static inline void c2_gather(const u32 *table, const u32 *gmap, const u32 *bmap,
                             const u8 *rp, const u8 *gp, const u8 *bp,
                             pixels4 *p00, pixels4 *p01, pixels4 *p10, pixels4 *p11,
                             pixels4 *wg, pixels4 *wb) {
    for (u32 lane = 0; lane < 4; ++lane) {
        u32 gm = gmap[gp[lane]], bm = bmap[bp[lane]];
        const u32 *e = table + (gm >> 8) + (bm >> 8) + rp[lane]*C2_R_STEP;
        (*p00)[lane] = e[0]; (*p01)[lane] = e[1];
        (*p10)[lane] = e[C2_B_STEP]; (*p11)[lane] = e[C2_B_STEP+1];
        (*wg)[lane] = gm & 255; (*wb)[lane] = bm & 255;
    }
}

static inline void c2_store(u8 *rp, u8 *gp, u8 *bp, pixels4 p00, pixels4 p01,
                            pixels4 p10, pixels4 p11, pixels4 wg, pixels4 wb) {
    pixels4 ig = 255-wg, ib = 255-wb;
    pixels4 red = COMPACT_CHANNEL(p00,p01,p10,p11,0,ig,wg,ib,wb);
    pixels4 green = COMPACT_CHANNEL(p00,p01,p10,p11,10,ig,wg,ib,wb);
    pixels4 blue = COMPACT_CHANNEL(p00,p01,p10,p11,20,ig,wg,ib,wb);
    bytes4 rr = __builtin_convertvector(red, bytes4);
    bytes4 gg = __builtin_convertvector(green, bytes4);
    bytes4 bb = __builtin_convertvector(blue, bytes4);
    __builtin_memcpy(rp, &rr, 4);
    __builtin_memcpy(gp, &gg, 4);
    __builtin_memcpy(bp, &bb, 4);
}

int xreal_compact2_rgb_lut(const u32 *table, u8 *r, u8 *g, u8 *b,
                           u32 rs, u32 gs, u32 bs, u32 width, u32 height) {
    if (!table || !r || !g || !b || !width || !height ||
        width>1920 || height>1080 || rs<width || gs<width || bs<width ||
        rs>4096 || gs>4096 || bs>4096) return -22;
    const u32 *gmap = table + COMPACT_WORDS, *bmap = gmap + 256;
    for (u32 y = 0; y < height; ++y) {
        u8 *rp = r+(u64)y*rs, *gp = g+(u64)y*gs, *bp = b+(u64)y*bs;
        u64 last_r = 0, last_g = 0, last_b = 0, out_r = 0, out_g = 0, out_b = 0;
        int have_last = 0;
        u32 x = 0;
        for (; x+8 <= width; x += 8) {
            u64 kr, kg, kb;
            __builtin_memcpy(&kr, rp+x, 8);
            __builtin_memcpy(&kg, gp+x, 8);
            __builtin_memcpy(&kb, bp+x, 8);
            if (have_last && kr == last_r && kg == last_g && kb == last_b) {
                __builtin_memcpy(rp+x, &out_r, 8);
                __builtin_memcpy(gp+x, &out_g, 8);
                __builtin_memcpy(bp+x, &out_b, 8);
                continue;
            }
            pixels4 a00, a01, a10, a11, awg, awb, b00, b01, b10, b11, bwg, bwb;
            c2_gather(table, gmap, bmap, rp+x, gp+x, bp+x, &a00, &a01, &a10, &a11, &awg, &awb);
            c2_gather(table, gmap, bmap, rp+x+4, gp+x+4, bp+x+4, &b00, &b01, &b10, &b11, &bwg, &bwb);
            c2_store(rp+x, gp+x, bp+x, a00, a01, a10, a11, awg, awb);
            c2_store(rp+x+4, gp+x+4, bp+x+4, b00, b01, b10, b11, bwg, bwb);
            last_r = kr; last_g = kg; last_b = kb; have_last = 1;
            __builtin_memcpy(&out_r, rp+x, 8);
            __builtin_memcpy(&out_g, gp+x, 8);
            __builtin_memcpy(&out_b, bp+x, 8);
        }
        for (; x < width; ++x) {
            u32 gm = gmap[gp[x]], bm = bmap[bp[x]];
            const u32 *e = table + (gm >> 8) + (bm >> 8) + rp[x]*C2_R_STEP;
            u32 p00 = e[0], p01 = e[1], p10 = e[C2_B_STEP], p11 = e[C2_B_STEP+1];
            u32 wg = gm & 255, wb = bm & 255, ig = 255-wg, ib = 255-wb;
            rp[x] = (u8)COMPACT_CHANNEL(p00,p01,p10,p11,0,ig,wg,ib,wb);
            gp[x] = (u8)COMPACT_CHANNEL(p00,p01,p10,p11,10,ig,wg,ib,wb);
            bp[x] = (u8)COMPACT_CHANNEL(p00,p01,p10,p11,20,ig,wg,ib,wb);
        }
    }
    return 0;
}
