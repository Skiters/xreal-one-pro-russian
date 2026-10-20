/* Fault-injection fixture only. The genuine kernel is tested in Unicorn. */
static unsigned calls;
extern void *memcpy(void *, const void *, unsigned long);
int xreal_compare_algorithm(void) { return 1; }
unsigned test_legacy_calls(void) { return calls; }
int xreal_onepro_warp(unsigned char *src[3], unsigned char *dst[3],
                      const unsigned *ss, const unsigned *ds,
                      const unsigned char *depth, unsigned fractional_bits) {
    (void)depth; (void)fractional_bits;
    ++calls;
    for (unsigned p=0;p<3;++p) {
        unsigned width=p ? 960 : 1920, height=p ? 540 : 1080;
        for (unsigned y=0;y<height;++y)
            memcpy(dst[p]+y*ds[p],src[p]+y*ss[p],width);
        src[p][0]^=1; /* Model the second view's in-place write. */
    }
    return 0;
}
