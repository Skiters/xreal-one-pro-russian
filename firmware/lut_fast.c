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
