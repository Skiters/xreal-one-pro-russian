#ifndef XREAL_ONEPRO_WARP_H
#define XREAL_ONEPRO_WARP_H
int xreal_compare_algorithm(void);
int xreal_compare_get(int *value);
int xreal_compare_set(int value);
int xreal_onepro_warp(unsigned char *src[3], unsigned char *dst[3],
                      const unsigned *src_stride, const unsigned *dst_stride,
                      const unsigned char *depth, unsigned fractional_bits);
#endif
