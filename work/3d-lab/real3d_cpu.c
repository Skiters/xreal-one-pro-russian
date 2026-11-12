/* Experimental CPU fallback for Pilot 2.0 Real3D RPCs on One Pro.
 * The vendor's 480-pixel depth map drives horizontal stereo disparity.
 * A failed ABI/layout check leaves the existing HDR path untouched.
 */
#include "runtime.h"
#if defined(__aarch64__) && !defined(XREAL_NO_NEON)
#include <arm_neon.h>
#endif
#ifdef XREAL_COMPARE_ONEPRO_WARP
#include "onepro_warp.h"
#endif
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long u64;
typedef unsigned long size_t;
#define NULL ((void *)0)
extern int strcmp(const char *, const char *);
extern int snprintf(char *, size_t, const char *, ...);
extern int open(const char *, int, ...);
extern long write(int, const void *, size_t);
extern int close(int);
extern int pthread_create(u64 *, const void *, void *(*)(void *), void *);
extern int pthread_join(u64, void **);
struct timespec { long sec, nsec; };
extern int clock_gettime(int, struct timespec *);
extern int nanosleep(const struct timespec *, struct timespec *);

/* perf8 frame pacing. Pilot's 2D->3D path delivers ~48 frames/s, which the
 * 90 Hz OLED shows as an irregular mix of 1 and 2 refreshes (judder). Each
 * Process call now returns no earlier than PACE_US after the previous call
 * began, slightly under two 60 Hz source frames, so the pipeline locks onto
 * every second source frame: 30 frames/s, each shown exactly 3 refreshes.
 * The warp itself is unchanged (it finishes in ~15 ms). */
/* perf11: pacing DISABLED. On the device the even 30/s cadence raised
 * Pilot's synth latency from ~70 ms to 100-177 ms (frames queue while the
 * call sleeps) and head-locked 3D visibly lagged and jumped. 2D->3D frames
 * must return as soon as the warp is done. */
#define PACE_US 0ULL
static u64 pace_last_start, pace_slept_us, pace_late;

extern void *ar_hal_sys_mmap_cache(u64, u32);
extern int ar_hal_sys_munmap(void *, u32);
extern int ar_hal_sys_mmz_invalid_cache(u64, void *, u32);
extern int ar_hal_sys_mmz_flush_cache_pa(u64, u32);

struct rpc {
    char name[32];
    u64 reserved;
    const void *args;
    u32 length;
};

/* Recovered from the Pilot 2.0 ARM64 call at 0xdb160..0xdb208. */
struct real3d_frame {
    u32 width, height;
    u32 src_stride_y, src_stride_uv;
    u32 dst_stride_y, dst_stride_uv;
    u32 depth_width, fractional_bits;
    u64 src[3];
    u64 dst[3];
    u64 depth;
    u64 result;
};
_Static_assert(sizeof(struct real3d_frame) == 96, "Real3D RPC ABI");
_Static_assert(__builtin_offsetof(struct rpc, args) == 40, "RPC pointer ABI");

static int initialized;
static u32 frames;
static u32 depth_min, depth_max, shifted_samples;
static u32 map_us, warp_us, flush_us;

#ifdef XREAL_CAPTURE_REAL3D
/* One bounded capture per process, at the same RPC and before unmapping.
 * These are input/warped frames, NOT a capture of the final eye displays. */
static int captured;
static int write_all(int fd, const void *data, size_t size) {
    const u8 *p = data;
    while (size) {
        long n = write(fd, p, size);
        if (n <= 0 || (size_t)n > size) return -1;
        p += n; size -= (size_t)n;
    }
    return 0;
}
static int capture_plane(const char *path, const u8 *data,
                         u32 width, u32 height, u32 stride) {
    char header[64];
    int n = snprintf(header, sizeof(header), "P5\n%u %u\n255\n", width, height);
    if (n <= 0 || n >= (int)sizeof(header)) return -1;
    int fd = open(path, 1 | 64 | 512, 0600);
    if (fd < 0) return -1;
    int rc = write_all(fd, header, (size_t)n);
    for (u32 y = 0; !rc && y < height; ++y)
        rc = write_all(fd, data+(size_t)y*stride, width);
    close(fd);
    return rc;
}
#endif

static u64 monotonic_us(void) {
    struct timespec t;
    if (clock_gettime(1, &t)) return 0;
    return (u64)t.sec * 1000000ULL + (u64)t.nsec / 1000;
}

static void diagnostic(const char *event, const struct real3d_frame *f, int rc) {
    char line[550];
    int n;
    if (f) {
        n = snprintf(line, sizeof(line),
            "Real3D CPU %s rc=%d frame=%u %ux%u strides=%u/%u -> %u/%u depth_width=%u fractional_bits=%u "
            "src=%lx,%lx,%lx dst=%lx,%lx,%lx map=%lx result=%lx "
            "depth_range=%u..%u shifted=%u time_us=%u/%u/%u stamp_us=%lu workers=%u\n",
            event, rc, frames, f->width, f->height,
            f->src_stride_y, f->src_stride_uv,
            f->dst_stride_y, f->dst_stride_uv,
            f->depth_width, f->fractional_bits,
            f->src[0], f->src[1], f->src[2],
            f->dst[0], f->dst[1], f->dst[2], f->depth, f->result,
            depth_min, depth_max, shifted_samples, map_us, warp_us, flush_us,
            monotonic_us(), xreal_pool_workers());
    } else {
        n = snprintf(line, sizeof(line), "Real3D CPU %s rc=%d frames=%u\n",
                     event, rc, frames);
    }
    if (n <= 0 || n >= (int)sizeof(line)) return;
    int fd = open("/usrdata/real3d-cpu.log", 1 | 64 | 1024, 0600);
    if (fd >= 0) { (void)write(fd, line, (size_t)n); (void)close(fd); }
}

/* One Pro's MMZ pool used by the already validated HDR fallback. */
static int valid(u64 address, u32 bytes) {
    return bytes && address >= 0x33000000ULL && address < 0x40000000ULL &&
           bytes <= 0x40000000ULL - address;
}

static void *map(u64 address, u32 bytes) {
    if (!valid(address, bytes)) return NULL;
    void *p = ar_hal_sys_mmap_cache(address, bytes);
    if (!p || p == (void *)-1) return NULL;
    int rc = ar_hal_sys_mmz_invalid_cache(address, p, bytes);
    if (rc && (u32)rc != 0x80028003U) {
        ar_hal_sys_munmap(p, bytes);
        return NULL;
    }
    return p;
}

/* Destination planes are written completely (whole 64-byte lines: widths
 * 1920/960 and strides 2048/1920/1024/960 are multiples of 64) and never
 * read, so their cache lines need no invalidation before the warp; the
 * flush after the warp writes them back. Unaligned layouts keep the
 * invalidate. */
static void *map_output(u64 address, u32 bytes, u32 stride) {
    if ((address & 63) || (stride & 63)) return map(address, bytes);
    if (!valid(address, bytes)) return NULL;
    void *p = ar_hal_sys_mmap_cache(address, bytes);
    if (!p || p == (void *)-1) return NULL;
    return p;
}

static int set_result(u64 address, int result) {
    int *p = map(address, 64);
    if (!p) return -5;
    *p = result;
    int rc = ar_hal_sys_mmz_flush_cache_pa(address, 64);
    ar_hal_sys_munmap(p, 64);
    return rc ? -5 : 0;
}

static int depth_shift(u8 value, u32 width, u32 depth_width, u32 fractional_bits) {
    /* Pilot has already scaled NPU depth by the pose/mode multiplier and
     * encoded displacement with 2, 3 or 4 fractional bits in luma pixels.
     * Q5 preserves even a Q4 luma step after chroma subsampling. */
    return value << (5 - fractional_bits - (width / depth_width == 2));
}

static u8 sample_q5(const u8 *s, int q, u32 width) {
    if (q <= 0) return s[0];
    u32 x = (u32)q / 32, frac = (u32)q & 31;
    if (x >= width - 1) return s[width - 1];
    return (u8)((s[x] * (32-frac) + s[x+1] * frac + 16) >> 5);
}

/* Four neighbouring pixels share one fractional displacement. Keep the
 * scalar Q5 weights/rounding exactly, using four independent 16-bit lanes.
 * Call only when both four-byte loads are wholly inside the source row. */
static void sample_cell4(u8 *dst, const u8 *src, u32 frac) {
    typedef u8 bytes4 __attribute__((vector_size(4)));
    typedef u16 words4 __attribute__((vector_size(8)));
    bytes4 left, right;
    __builtin_memcpy(&left, src, 4);
    __builtin_memcpy(&right, src + 1, 4);
    words4 a = __builtin_convertvector(left, words4);
    words4 b = __builtin_convertvector(right, words4);
    words4 value = (a * (u16)(32-frac) + b * (u16)frac + (u16)16) >> 5;
    bytes4 result = __builtin_convertvector(value, bytes4);
    __builtin_memcpy(dst, &result, 4);
}

/* Two 16-bit lanes packed into a register; each sum is <= 8176 so neither
 * multiplication nor the rounding addition can carry into the next lane. */
static void sample_cell2(u8 *dst, const u8 *src, u32 frac) {
    u16 left, right;
    __builtin_memcpy(&left, src, 2);
    __builtin_memcpy(&right, src + 1, 2);
    u32 a = (left & 255U) | ((u32)(left & 0xff00U) << 8);
    u32 b = (right & 255U) | ((u32)(right & 0xff00U) << 8);
    u32 value = (a * (32-frac) + b * frac + 0x00100010U) >> 5;
    u16 result = (u16)((value & 255U) | ((value >> 8) & 0xff00U));
    __builtin_memcpy(dst, &result, 2);
}

/* The live Real3D frame is 1920x1080 with a 480x270 depth map.  Each
 * depth sample covers four luma pixels or two chroma pixels.  Process a
 * complete depth cell at once so that division and disparity calculation
 * are not repeated for every output pixel. */
/* smooth=0 is the rc7 behaviour: one displacement per depth row, so a
 * depth step moves a whole 4-row (luma) / 2-row (chroma) band and slanted
 * object edges show a staircase. smooth=1 blends each output row's
 * displacement with the neighbouring depth row by distance to the row
 * centres (weights 1/8, 3/8 for luma; 1/4 for chroma), clamped at the top
 * and bottom. smooth=2 additionally blends horizontally with the
 * neighbouring depth column by distance to the cell centres (the same
 * weights), so every output pixel gets its own displacement: bilinear
 * interpolation of the 480x270 map instead of 4-pixel steps along slanted
 * edges. Cells whose neighbours carry the same displacement keep the shared
 * fast path. */
/* perf15 luma sharpness: 4-tap Catmull-Rom instead of 2-tap linear when the
 * Q5 source position is fractional (integer positions are still copied).
 * Q8 weights for the 32 phases, each row sums to 256. Anti-ringing: the
 * result is clamped to the two centre samples, so subtitles and hard edges
 * get no halo, while gradients/texture keep more contrast than linear. */
static const short CR_Q8[32][4] = {
    {0, 256, 0, 0},
    {-4, 256, 4, 0},
    {-7, 253, 10, 0},
    {-10, 251, 16, -1},
    {-12, 247, 23, -2},
    {-14, 242, 31, -3},
    {-16, 237, 39, -4},
    {-17, 230, 48, -5},
    {-18, 222, 58, -6},
    {-19, 214, 68, -7},
    {-19, 206, 78, -9},
    {-19, 196, 89, -10},
    {-19, 186, 100, -11},
    {-18, 176, 111, -13},
    {-18, 166, 122, -14},
    {-17, 155, 133, -15},
    {-16, 144, 144, -16},
    {-15, 133, 155, -17},
    {-14, 122, 166, -18},
    {-13, 111, 176, -18},
    {-11, 100, 186, -19},
    {-10, 89, 196, -19},
    {-9, 78, 206, -19},
    {-7, 68, 214, -19},
    {-6, 58, 222, -18},
    {-5, 48, 230, -17},
    {-4, 39, 237, -16},
    {-3, 31, 242, -14},
    {-2, 23, 247, -12},
    {-1, 16, 251, -10},
    {0, 10, 253, -7},
    {0, 4, 256, -4}
};

static u8 sample_cubic_q5(const u8 *s, int q, u32 width) {
    if (q <= 0) return s[0];
    u32 x = (u32)q >> 5, f = (u32)q & 31;
    if (x >= width - 1) return s[width - 1];
    if (!f) return s[x];
    int a = s[x ? x - 1 : 0], b = s[x], c = s[x + 1], d = s[x + 2 < width ? x + 2 : width - 1];
    int v = (CR_Q8[f][0] * a + CR_Q8[f][1] * b + CR_Q8[f][2] * c + CR_Q8[f][3] * d + 128) >> 8;
    int lo = b < c ? b : c, hi = b < c ? c : b;
    return (u8)(v < lo ? lo : v > hi ? hi : v);
}

/* Four luma pixels sharing one fractional phase (the cell fast path).
 * Caller guarantees 1 <= sx and sx + 7 <= width. Same arithmetic as
 * sample_cubic_q5. */
static void sample_cell4_cubic(u8 *dst, const u8 *s, u32 sx, u32 f) {
#if defined(__aarch64__) && !defined(XREAL_NO_NEON)
    uint8x8_t win = vld1_u8(s + sx - 1);                 /* s[sx-1 .. sx+6] */
    int16x4_t t0 = vreinterpret_s16_u16(vget_low_u16(vmovl_u8(win)));
    int16x4_t t1 = vreinterpret_s16_u16(vget_low_u16(vmovl_u8(vext_u8(win, win, 1))));
    int16x4_t t2 = vreinterpret_s16_u16(vget_low_u16(vmovl_u8(vext_u8(win, win, 2))));
    int16x4_t t3 = vreinterpret_s16_u16(vget_low_u16(vmovl_u8(vext_u8(win, win, 3))));
    int32x4_t acc = vmull_n_s16(t0, CR_Q8[f][0]);
    acc = vmlal_n_s16(acc, t1, CR_Q8[f][1]);
    acc = vmlal_n_s16(acc, t2, CR_Q8[f][2]);
    acc = vmlal_n_s16(acc, t3, CR_Q8[f][3]);
    int16x4_t v = vmovn_s32(vshrq_n_s32(vaddq_s32(acc, vdupq_n_s32(128)), 8));
    v = vmax_s16(v, vmin_s16(t1, t2));
    v = vmin_s16(v, vmax_s16(t1, t2));
    uint8x8_t out = vqmovun_s16(vcombine_s16(v, v));
    vst1_lane_u32((uint32_t *)(void *)dst, vreinterpret_u32_u8(out), 0);
#else
    for (u32 k = 0; k < 4; ++k) dst[k] = sample_cubic_q5(s, (int)((sx + k) * 32 + f), sx + 8);
#endif
}

/* perf18: per-pixel cubic for four luma pixels with individual phases, one
 * 16-byte window + TBL gathers and a 4x4 weight transpose. Returns 0 when the
 * window does not fit (caller uses sample_cubic_q5). Same arithmetic. */
static inline int sample_pixels4_cubic(u8 *d, const u8 *s, u32 x, const u32 sh[4], u32 width) {
#if defined(__aarch64__) && !defined(XREAL_NO_NEON)
    u32 i[4], f[4];
    for (u32 k = 0; k < 4; ++k) { u32 q = (x + k) * 32 + sh[k]; i[k] = q >> 5; f[k] = q & 31; }
    u32 lo = i[0], hi = i[0];
    for (u32 k = 1; k < 4; ++k) { if (i[k] < lo) lo = i[k]; if (i[k] > hi) hi = i[k]; }
    if (lo < 1 || hi - lo > 12 || lo - 1 + 16 > width) return 0;
    uint8x16_t win = vld1q_u8(s + lo - 1);
    u64 base = (u64)(i[0] - lo) | (u64)(i[1] - lo) << 8 | (u64)(i[2] - lo) << 16 | (u64)(i[3] - lo) << 24;
    int16x4_t tap[4];
    for (u32 t = 0; t < 4; ++t) {
        uint8x8_t idx = vcreate_u8(base + 0x01010101ULL * t);
        tap[t] = vreinterpret_s16_u16(vget_low_u16(vmovl_u8(vqtbl1_u8(win, idx))));
    }
    /* Rows = pixels, columns = taps; transpose to tap-major weight vectors. */
    int16x4_t r0 = vld1_s16(CR_Q8[f[0]]), r1 = vld1_s16(CR_Q8[f[1]]);
    int16x4_t r2 = vld1_s16(CR_Q8[f[2]]), r3 = vld1_s16(CR_Q8[f[3]]);
    int16x4x2_t a = vtrn_s16(r0, r1), b = vtrn_s16(r2, r3);
    int32x2x2_t c = vtrn_s32(vreinterpret_s32_s16(a.val[0]), vreinterpret_s32_s16(b.val[0]));
    int32x2x2_t e = vtrn_s32(vreinterpret_s32_s16(a.val[1]), vreinterpret_s32_s16(b.val[1]));
    int16x4_t w0 = vreinterpret_s16_s32(c.val[0]), w2 = vreinterpret_s16_s32(c.val[1]);
    int16x4_t w1 = vreinterpret_s16_s32(e.val[0]), w3 = vreinterpret_s16_s32(e.val[1]);
    int32x4_t acc = vmull_s16(tap[0], w0);
    acc = vmlal_s16(acc, tap[1], w1);
    acc = vmlal_s16(acc, tap[2], w2);
    acc = vmlal_s16(acc, tap[3], w3);
    int16x4_t v = vmovn_s32(vshrq_n_s32(vaddq_s32(acc, vdupq_n_s32(128)), 8));
    v = vmax_s16(v, vmin_s16(tap[1], tap[2]));
    v = vmin_s16(v, vmax_s16(tap[1], tap[2]));
    vst1_lane_u32((uint32_t *)(void *)d, vreinterpret_u32_u8(vqmovun_s16(vcombine_s16(v, v))), 0);
    return 1;
#else
    (void)d; (void)s; (void)x; (void)sh; (void)width;
    return 0;
#endif
}

#define HFLAT 4U

/* perf16 edge-aware depth (joint bilateral upsampling, luma only). Where the
 * 480x270 displacement jumps by more than JBU_EDGE (1/2 pixel) around a
 * cell, each pixel takes a weighted mix of the 2x2 nearest cells' raw
 * displacements: spatial (bilinear) weight times a range weight from how
 * close the pixel's source luma is to each cell's centre luma. The depth
 * boundary then follows the object contour instead of a smooth ramp across
 * foreground and background. Elsewhere the perf2/perf5 smoothing is kept. */
#define JBU_EDGE 32U   /* perf18: 1 pixel (was 1/2): object boundaries, not depth noise */
static const unsigned short JBU_RANGE_Q8[256] = {256, 254, 249, 240, 229, 215, 199, 182, 164, 146, 128, 110, 94, 79, 66, 54, 43, 34, 27, 21, 16, 12, 9, 6, 5, 3, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1};
/* Four luma pixels with individual Q5 displacements: one 16-byte load and a
 * TBL gather instead of eight byte loads. Same arithmetic as sample_q5.
 * Returns 0 (caller falls back) when the window does not fit. */
static inline int sample_pixels4(u8 *d, const u8 *s, u32 x, const u32 sh[4], u32 width) {
#if defined(__aarch64__) && !defined(XREAL_NO_NEON)
    u32 i0 = sh[0] >> 5, i1 = sh[1] >> 5, i2 = sh[2] >> 5, i3 = sh[3] >> 5;
    u32 lo = i0 < i1 ? i0 : i1;
    if (i2 < lo) lo = i2;
    if (i3 < lo) lo = i3;
    u32 hi = i0 > i1 ? i0 : i1;
    if (i2 > hi) hi = i2;
    if (i3 > hi) hi = i3;
    u32 base = x + lo;
    if (hi - lo + 4 > 15 || base + 16 > width) return 0;
    u64 a = (u64)(i0 - lo) | (u64)(i1 - lo + 1) << 8 | (u64)(i2 - lo + 2) << 16 | (u64)(i3 - lo + 3) << 24;
    uint8x8_t index = vcreate_u8(a | (a + 0x01010101ULL) << 32);
    uint8x8_t got = vqtbl1_u8(vld1q_u8(s + base), index);
    uint16x8_t wide = vmovl_u8(got);
    uint16x4_t f = vcreate_u16((u64)(sh[0] & 31) | (u64)(sh[1] & 31) << 16 |
                               (u64)(sh[2] & 31) << 32 | (u64)(sh[3] & 31) << 48);
    uint16x4_t v = vmul_u16(vget_low_u16(wide), vsub_u16(vdup_n_u16(32), f));
    v = vmla_u16(v, vget_high_u16(wide), f);
    v = vshr_n_u16(vadd_u16(v, vdup_n_u16(16)), 5);
    vst1_lane_u32((uint32_t *)(void *)d, vreinterpret_u32_u8(vmovn_u16(vcombine_u16(v, v))), 0);
    return 1;
#else
    (void)d; (void)s; (void)x; (void)sh; (void)width;
    return 0;
#endif
}

/* src2/dst2 (optional): a second plane with identical geometry (U with V),
 * warped in the same pass; only for smooth != 2. cubic: luma 4-tap. */
static int warp_plane_cells_impl(const u8 *src, u8 *dst, const u8 *depth,
                                 u32 width, u32 height, u32 src_stride,
                                 u32 dst_stride, u32 depth_width,
                                 u32 depth_height, u32 begin, u32 end,
                                 u32 fractional_bits, int smooth,
                                 const u8 *src2, u8 *dst2, u32 src2_stride, u32 dst2_stride,
                                 int cubic, int jbu, int reproject) {
    if (fractional_bits < 2 || fractional_bits > 4 ||
        !depth_width || !depth_height || !width || !height ||
        !src || !dst || !depth || src_stride < width || dst_stride < width) return -22;
    u32 pixels = width / depth_width;
    u32 rows = height / depth_height;
    if ((pixels != 2 && pixels != 4) || !rows ||
        width % depth_width || height % depth_height ||
        begin > end || end > depth_height) return -22;
    if (depth_width > 480) return -22;
    u32 den_shift = rows == 4 ? 3 : rows == 2 ? 2 : 0;
    if (smooth && !den_shift) return -22;
    if (dst2 && (!src2 || smooth == 2 || src2_stride < width || dst2_stride < width)) return -22;
    cubic = cubic && pixels == 4;
    jbu = jbu && smooth == 2 && pixels == 4 && rows == 4;
    for (u32 zy = begin; zy < end; ++zy) {
        const u8 *z = depth + (size_t)zy * depth_width;
        const u8 *up = depth + (size_t)(zy ? zy - 1 : zy) * depth_width;
        const u8 *down = depth + (size_t)(zy + 1 < depth_height ? zy + 1 : zy) * depth_width;
        u16 source_x[480], here[480], above[480], below[480], blend[480];
        u8 cen_cur[480], cen_up[480], cen_dn[480], edge_up[480], edge_dn[480];
        u32 any_edge = 0;
        if (jbu) {
            /* perf18: edge flags once per depth row (for the upper and lower
             * output-row halves) instead of per cell per output row. Same
             * test as before: max-min of here/left/right in this row and in
             * the other row > JBU_EDGE. */
            const u8 *zu = depth + (size_t)(zy ? zy - 1 : zy) * depth_width;
            const u8 *zd = depth + (size_t)(zy + 1 < depth_height ? zy + 1 : zy) * depth_width;
            for (u32 zx = 0; zx < depth_width; ++zx) {
                u32 l = zx ? zx - 1 : zx, r = zx + 1 < depth_width ? zx + 1 : zx;
                u32 h0 = (u32)depth_shift(z[zx], width, depth_width, fractional_bits);
                u32 h1 = (u32)depth_shift(z[l], width, depth_width, fractional_bits);
                u32 h2 = (u32)depth_shift(z[r], width, depth_width, fractional_bits);
                u32 lo = h0 < h1 ? h0 : h1, hi = h0 < h1 ? h1 : h0;
                if (h2 < lo) lo = h2;
                if (h2 > hi) hi = h2;
                const u8 *o2[2] = { zu, zd };
                u8 *flag[2] = { edge_up, edge_dn };
                for (u32 t = 0; t < 2; ++t) {
                    u32 a0 = (u32)depth_shift(o2[t][zx], width, depth_width, fractional_bits);
                    u32 a1 = (u32)depth_shift(o2[t][l], width, depth_width, fractional_bits);
                    u32 a2 = (u32)depth_shift(o2[t][r], width, depth_width, fractional_bits);
                    u32 l2 = lo, h3 = hi;
                    if (a0 < l2) l2 = a0;
                    if (a0 > h3) h3 = a0;
                    if (a1 < l2) l2 = a1;
                    if (a1 > h3) h3 = a1;
                    if (a2 < l2) l2 = a2;
                    if (a2 > h3) h3 = a2;
                    flag[t][zx] = h3 - l2 > JBU_EDGE;
                    any_edge |= flag[t][zx];
                }
            }
        }
        if (jbu && any_edge) {
            /* Centre luma of each cell: mean of its 2x2 centre pixels. */
            u32 rz[3] = { zy ? zy - 1 : zy, zy, zy + 1 < depth_height ? zy + 1 : zy };
            u8 *cens[3] = { cen_up, cen_cur, cen_dn };
            for (u32 r = 0; r < 3; ++r) {
                const u8 *r1 = src + (size_t)(rz[r] * 4 + 1) * src_stride;
                const u8 *r2 = src + (size_t)(rz[r] * 4 + 2) * src_stride;
                for (u32 zx = 0; zx < depth_width; ++zx) {
                    u32 c = zx * 4 + 1;
                    cens[r][zx] = (u8)((r1[c] + r1[c + 1] + r2[c] + r2[c + 1] + 2) >> 2);
                }
            }
        }
        for (u32 zx = 0; zx < depth_width; ++zx) {
            here[zx] = (u16)depth_shift(z[zx], width, depth_width, fractional_bits);
            source_x[zx] = (u16)(zx * pixels * 32 + here[zx]);
            if (smooth) {
                above[zx] = (u16)depth_shift(up[zx], width, depth_width, fractional_bits);
                below[zx] = (u16)depth_shift(down[zx], width, depth_width, fractional_bits);
            }
        }
        for (u32 ry = 0; ry < rows; ++ry) {
            if (smooth) {
                /* Offset of this row's centre from the depth row's centre,
                 * in units of 1/(2*rows) depth rows. Never zero for even rows. */
                int num = 2 * (int)ry + 1 - (int)rows;
                const u16 *other = num < 0 ? above : below;
                u32 w = (u32)(num < 0 ? -num : num), den = 2 * rows;
                for (u32 zx = 0; zx < depth_width; ++zx) {
                    blend[zx] = (u16)((here[zx] * (den - w) + other[zx] * w + rows) >> den_shift);
                    source_x[zx] = (u16)(zx * pixels * 32 + blend[zx]);
                }
            }
            if (reproject && smooth) {
                /* perf23: the depth map is in source coordinates, but this row
                 * is built in output coordinates (dst[x] = src[x + d]). Read the
                 * displacement where the pixel actually comes from: one fixed-
                 * point step d = D(x + D(x)), per cell. Without it, output pixels
                 * next to a near object take the background's displacement and
                 * repeat the object's edge (halo that grows with depth). */
                u16 moved[480];
                u32 half = 16 * pixels, unit = 32 * pixels;
                for (u32 zx = 0; zx < depth_width; ++zx) {
                    u32 to = zx + (blend[zx] + half) / unit;
                    moved[zx] = blend[to < depth_width ? to : depth_width - 1];
                }
                for (u32 zx = 0; zx < depth_width; ++zx) {
                    blend[zx] = moved[zx];
                    source_x[zx] = (u16)(zx * pixels * 32 + blend[zx]);
                }
            }
            u32 y = zy * rows + ry;
            const u8 *s = src + (size_t)y * src_stride;
            u8 *d = dst + (size_t)y * dst_stride;
            const u8 *s2 = dst2 ? src2 + (size_t)y * src2_stride : NULL;
            u8 *d2 = dst2 ? dst2 + (size_t)y * dst2_stride : NULL;
            for (u32 zx = 0, x = 0; zx < depth_width; ++zx, x += pixels) {
                if (jbu && any_edge && (2 * ry + 1 < rows ? edge_up : edge_dn)[zx]) {
                    u32 l = zx ? zx - 1 : zx, r = zx + 1 < depth_width ? zx + 1 : zx;
                    int num = 2 * (int)ry + 1 - (int)rows;
                    const u16 *oth = num < 0 ? above : below;
                    const u8 *ocen = num < 0 ? cen_up : cen_dn;
                    u32 vw = (u32)(num < 0 ? -num : num);
                    {
                        for (u32 k = 0; k < 4; ++k) {
                            int hnum = 2 * (int)k + 1 - 4;
                            u32 hw = (u32)(hnum < 0 ? -hnum : hnum), side = hnum < 0 ? l : r;
                            u32 dv[4] = { here[zx], here[side], oth[zx], oth[side] };
                            u32 cv[4] = { cen_cur[zx], cen_cur[side], ocen[zx], ocen[side] };
                            u32 sw[4] = { (8 - hw) * (8 - vw), hw * (8 - vw), (8 - hw) * vw, hw * vw };
                            /* perf19 sharp boundaries: split the four cells into a
                             * near and a far layer (around the mid displacement).
                             * When the pixel's luma clearly belongs to one layer
                             * (its weight >= 2x the other), use only that layer,
                             * so objects separate from the background as with the
                             * native mesh; ambiguous luma keeps the soft mix. */
                            u32 g = s[x + k], dmin = dv[0], dmax = dv[0];
                            for (u32 i = 1; i < 4; ++i) { if (dv[i] < dmin) dmin = dv[i]; if (dv[i] > dmax) dmax = dv[i]; }
                            u32 mid2 = dmin + dmax;                 /* 2 * mid */
                            u32 wn = 0, an = 0, wf = 0, af = 0;
                            for (u32 i = 0; i < 4; ++i) {
                                u32 diff = g > cv[i] ? g - cv[i] : cv[i] - g;
                                u32 w = sw[i] * JBU_RANGE_Q8[diff];
                                if (2 * dv[i] >= mid2) { wn += w; an += w * dv[i]; }
                                else { wf += w; af += w * dv[i]; }
                            }
                            u32 wsum, acc;
                            if (wf && wn >= 2 * wf) { wsum = wn; acc = an; }
                            else if (wn && wf >= 2 * wn) { wsum = wf; acc = af; }
                            else { wsum = wn + wf; acc = an + af; }
                            u32 shift = (acc + wsum / 2) / wsum;
                            int q = (int)((x + k) * 32 + shift);
                            d[x + k] = cubic ? sample_cubic_q5(s, q, width) : sample_q5(s, q, width);
                        }
                        continue;
                    }
                }
                if (smooth == 2) {
                    u32 mid = blend[zx];
                    u32 left = blend[zx ? zx - 1 : zx];
                    u32 right = blend[zx + 1 < depth_width ? zx + 1 : zx];
                    /* Neighbours within HFLAT (1/8 pixel): the cell keeps one
                     * displacement; per-pixel blending would move pixels by
                     * at most 0.05 px there, which is invisible. */
                    u32 dl = left > mid ? left - mid : mid - left;
                    u32 dr = right > mid ? right - mid : mid - right;
                    if (dl > HFLAT || dr > HFLAT) {
                        u32 sh[4];
                        if (pixels == 4) {
                            sh[0] = (5 * mid + 3 * left + 4) >> 3;
                            sh[1] = (7 * mid + left + 4) >> 3;
                            sh[2] = (7 * mid + right + 4) >> 3;
                            sh[3] = (5 * mid + 3 * right + 4) >> 3;
                        } else {
                            sh[0] = (3 * mid + left + 2) >> 2;
                            sh[1] = (3 * mid + right + 2) >> 2;
                        }
                        u32 top = mid > left ? mid : left;
                        if (right > top) top = right;
                        if (cubic) {
                            if (!sample_pixels4_cubic(d + x, s, x, sh, width))
                                for (u32 k = 0; k < pixels; ++k)
                                    d[x + k] = sample_cubic_q5(s, (int)((x + k) * 32 + sh[k]), width);
                        } else if (pixels == 4 && sample_pixels4(d + x, s, x, sh, width)) {
                        } else if ((((x + pixels - 1) * 32 + top) >> 5) + 1 < width) {
                            /* Every sample and its right neighbour lie inside the
                             * row: the same arithmetic as sample_q5, unchecked. */
                            for (u32 k = 0; k < pixels; ++k) {
                                u32 q = (x + k) * 32 + sh[k], i = q >> 5, f = q & 31;
                                d[x + k] = (u8)((s[i] * (32 - f) + s[i + 1] * f + 16) >> 5);
                            }
                        } else {
                            for (u32 k = 0; k < pixels; ++k)
                                d[x + k] = sample_q5(s, (int)((x + k) * 32 + sh[k]), width);
                        }
                        continue;
                    }
                }
                u32 q = source_x[zx], sx = q / 32;
                if (!(q & 31) && sx + pixels <= width) {
                    if (pixels == 4)
                        __builtin_memcpy(d + x, s + sx, 4);
                    else
                        __builtin_memcpy(d + x, s + sx, 2);
                    if (d2) __builtin_memcpy(d2 + x, s2 + sx, pixels);
                } else if (cubic) {
                    if (sx >= 1 && sx + 7 <= width)
                        sample_cell4_cubic(d + x, s, sx, q & 31);
                    else
                        for (u32 k = 0; k < pixels; ++k)
                            d[x + k] = sample_cubic_q5(s, (int)q + (int)k * 32, width);
                } else if (sx + pixels < width) {
                    if (pixels == 4)
                        sample_cell4(d + x, s + sx, q & 31);
                    else
                        sample_cell2(d + x, s + sx, q & 31);
                    if (d2) {
                        if (pixels == 4) sample_cell4(d2 + x, s2 + sx, q & 31);
                        else sample_cell2(d2 + x, s2 + sx, q & 31);
                    }
                } else {
                    for (u32 k = 0; k < pixels; ++k)
                        d[x + k] = sample_q5(s, (int)q + (int)k * 32, width);
                    if (d2)
                        for (u32 k = 0; k < pixels; ++k)
                            d2[x + k] = sample_q5(s2, (int)q + (int)k * 32, width);
                }
            }
        }
    }
    return 0;
}

static int warp_plane_cells_range(const u8 *src, u8 *dst, const u8 *depth,
                                  u32 width, u32 height, u32 src_stride,
                                  u32 dst_stride, u32 depth_width,
                                  u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_plane_cells_impl(src, dst, depth, width, height, src_stride, dst_stride,
                                 depth_width, depth_height, begin, end, fractional_bits, 0, NULL, NULL, 0, 0, 0, 0, 0);
}

static int warp_plane_cells_smooth_range(const u8 *src, u8 *dst, const u8 *depth,
                                         u32 width, u32 height, u32 src_stride,
                                         u32 dst_stride, u32 depth_width,
                                         u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_plane_cells_impl(src, dst, depth, width, height, src_stride, dst_stride,
                                 depth_width, depth_height, begin, end, fractional_bits, 1, NULL, NULL, 0, 0, 0, 0, 0);
}

static int warp_plane_cells(const u8 *src, u8 *dst, const u8 *depth,
                            u32 width, u32 height, u32 src_stride,
                            u32 dst_stride, u32 depth_width,
                            u32 depth_height, u32 fractional_bits) {
    return warp_plane_cells_range(src, dst, depth, width, height, src_stride,
                                  dst_stride, depth_width, depth_height,
                                  0, depth_height, fractional_bits);
}

struct segment_job {
    const u8 *src[3], *depth;
    u8 *dst[3];
    u32 width[3], height[3], src_stride[3], dst_stride[3];
    u32 depth_width, depth_height, fractional_bits;
    u32 begin, end;
    u32 *next;
    int rc;
};

static int warp_plane_cells_bilinear_range(const u8 *src, u8 *dst, const u8 *depth,
                                           u32 width, u32 height, u32 src_stride,
                                           u32 dst_stride, u32 depth_width,
                                           u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_plane_cells_impl(src, dst, depth, width, height, src_stride, dst_stride,
                                 depth_width, depth_height, begin, end, fractional_bits, 2, NULL, NULL, 0, 0, 0, 0, 0);
}

static int warp_luma_cubic_range(const u8 *src, u8 *dst, const u8 *depth,
                                 u32 width, u32 height, u32 src_stride,
                                 u32 dst_stride, u32 depth_width,
                                 u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_plane_cells_impl(src, dst, depth, width, height, src_stride, dst_stride,
                                 depth_width, depth_height, begin, end, fractional_bits, 2,
                                 NULL, NULL, 0, 0, 1, 0, 0);
}

static int warp_luma_jbu_range(const u8 *src, u8 *dst, const u8 *depth,
                               u32 width, u32 height, u32 src_stride,
                               u32 dst_stride, u32 depth_width,
                               u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_plane_cells_impl(src, dst, depth, width, height, src_stride, dst_stride,
                                 depth_width, depth_height, begin, end, fractional_bits, 2,
                                 NULL, NULL, 0, 0, 1, 1, 0);
}

static int warp_luma_reproject_range(const u8 *src, u8 *dst, const u8 *depth,
                                     u32 width, u32 height, u32 src_stride,
                                     u32 dst_stride, u32 depth_width,
                                     u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_plane_cells_impl(src, dst, depth, width, height, src_stride, dst_stride,
                                 depth_width, depth_height, begin, end, fractional_bits, 2,
                                 NULL, NULL, 0, 0, 0, 0, 1);
}

static int warp_chroma_pair_range(const u8 *u_src, u8 *u_dst, const u8 *v_src, u8 *v_dst,
                                  const u8 *depth, u32 width, u32 height,
                                  u32 u_src_stride, u32 u_dst_stride,
                                  u32 v_src_stride, u32 v_dst_stride, u32 depth_width,
                                  u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_plane_cells_impl(u_src, u_dst, depth, width, height, u_src_stride, u_dst_stride,
                                 depth_width, depth_height, begin, end, fractional_bits, 1,
                                 v_src, v_dst, v_src_stride, v_dst_stride, 0, 0, 1);
}

/* perf24: live chroma path with reprojection off (clean perf15-20 joint U/V). */
static int warp_chroma_pair_clean_range(const u8 *u_src, u8 *u_dst, const u8 *v_src, u8 *v_dst,
                                  const u8 *depth, u32 width, u32 height,
                                  u32 u_src_stride, u32 u_dst_stride,
                                  u32 v_src_stride, u32 v_dst_stride, u32 depth_width,
                                  u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_plane_cells_impl(u_src, u_dst, depth, width, height, u_src_stride, u_dst_stride,
                                 depth_width, depth_height, begin, end, fractional_bits, 1,
                                 v_src, v_dst, v_src_stride, v_dst_stride, 0, 0, 0);
}

/* Dynamic scheduling: workers take SEGMENT_ROWS depth rows at a time from a
 * shared counter, so a core busy with Pilot's own threads does not hold up
 * the frame. [begin, end) bounds the depth rows this job set covers. */
#define SEGMENT_ROWS 6
static void *segment_worker(void *arg) {
    struct segment_job *j = arg;
    j->rc = 0;
    for (;;) {
        u32 chunk = __atomic_fetch_add(j->next, 1, __ATOMIC_RELAXED);
        u32 first = j->begin + chunk * SEGMENT_ROWS;
        if (first >= j->end) break;
        u32 last = first + SEGMENT_ROWS < j->end ? first + SEGMENT_ROWS : j->end;
        /* Luma: bilinear displacement + 4-tap Catmull-Rom (perf15). Chroma:
         * vertical blend, U and V in one pass sharing the geometry (perf15,
         * byte-identical to two passes). */
        /* perf20: luma back to the perf5/perf11 path (bilinear displacement,
         * 2-tap sampling). On the device the cubic + edge-aware variants
         * cost 22-28 ms (36 frames/s) and perf19's hard layer selection
         * brought back halos and staircase. They stay in this file with
         * tests (warp_luma_cubic_range / warp_luma_jbu_range), unused. */
        /* perf24: reprojection (perf23) dragged the bright background into the
         * near object's edge — white halos, worse with the x1.5 gain. Back to
         * the artifact-free bilinear 2-tap path (perf5/perf11). */
        int rc = warp_plane_cells_bilinear_range(j->src[0], j->dst[0], j->depth, j->width[0],
                                       j->height[0], j->src_stride[0], j->dst_stride[0],
                                       j->depth_width, j->depth_height, first, last,
                                       j->fractional_bits);
        if (rc && !j->rc) j->rc = rc;
        rc = warp_chroma_pair_clean_range(j->src[1], j->dst[1], j->src[2], j->dst[2], j->depth,
                                    j->width[1], j->height[1], j->src_stride[1], j->dst_stride[1],
                                    j->src_stride[2], j->dst_stride[2], j->depth_width,
                                    j->depth_height, first, last, j->fractional_bits);
        if (rc && !j->rc) j->rc = rc;
    }
    return NULL;
}

/* Process one colour plane.  The vendor may request one warped eye or a
 * side-by-side buffer; both layouts are bounded by the validated strides. */
static int warp_plane(const u8 *src, u8 *dst, const u8 *depth,
                      u32 width, u32 height, u32 src_stride, u32 dst_stride,
                      u32 depth_width, u32 depth_stride, u32 depth_height,
                      int side_by_side, u32 fractional_bits) {
    if (fractional_bits < 2 || fractional_bits > 4 ||
        !src || !dst || !depth || !width || !height ||
        src_stride < width || dst_stride < width * (side_by_side ? 2U : 1U) ||
        depth_width < 16 || depth_stride < depth_width || !depth_height)
        return -22;
    for (u32 y = 0; y < height; ++y) {
        const u8 *s = src + (size_t)y * src_stride;
        u8 *d = dst + (size_t)y * dst_stride;
        const u8 *z = depth + (size_t)(y * depth_height / height) * depth_stride;
        for (u32 x = 0; x < width; ++x) {
            u8 near = z[x * depth_width / width];
            int shift = depth_shift(near, width, depth_width, fractional_bits);
            if (side_by_side) {
                d[x] = sample_q5(s, (int)x * 32 + shift / 2, width);
                d[x + width] = sample_q5(s, (int)x * 32 - shift / 2, width);
            } else {
                d[x] = sample_q5(s, (int)x * 32 + shift, width);
            }
        }
    }
    return 0;
}

static int validate_frame(const struct real3d_frame *f) {
    if (!f || f->width != 1920 || f->height != 1080 ||
        f->depth_width != 480 || f->fractional_bits < 2 || f->fractional_bits > 4 ||
        (f->src_stride_y != 1920 && f->src_stride_y != 2048) ||
        (f->dst_stride_y != 1920 && f->dst_stride_y != 2048) ||
        (f->src_stride_uv != 960 && f->src_stride_uv != 1024) ||
        (f->dst_stride_uv != 960 && f->dst_stride_uv != 1024)) return -22;
    /* No format plane, depth map or result slot may alias another. Validate
     * all ranges before the first mapping, cache operation or write. */
    u64 addresses[8];
    u32 sizes[8];
    for (u32 i = 0; i < 3; ++i) {
        addresses[i] = f->src[i]; addresses[i+3] = f->dst[i];
        sizes[i] = i ? f->src_stride_uv*540 : f->src_stride_y*1080;
        sizes[i+3] = i ? f->dst_stride_uv*540 : f->dst_stride_y*1080;
    }
    addresses[6] = f->depth; sizes[6] = 480*270;
    addresses[7] = f->result; sizes[7] = 64;
    for (u32 i = 0; i < 8; ++i) {
        if (!valid(addresses[i], sizes[i])) return -22;
        for (u32 j = 0; j < i; ++j)
            if (addresses[i] < addresses[j]+sizes[j] &&
                addresses[j] < addresses[i]+sizes[i]) return -22;
    }
    return 0;
}

int real3d_validate_frame_for_test(const struct real3d_frame *f) {
    return validate_frame(f);
}

/* perf22 depth pre-pass for the Pro+ path (480x270 map, not the image):
 * 1. Horizontal dilation (max of left/self/right): the vendor Pro+ pipeline
 *    has a dilate stage (dilate_ms in libnr_image_post_processor's WarpCost
 *    fields) that our port skipped. Near objects widen by one cell, so their
 *    silhouette moves whole and the smoothed displacement no longer drags the
 *    object's edge colour into the background (halo at strong depth).
 * 2. Gain: displacement x REAL3D_GAIN_Q8/256 (1.3), clamped to 255, for more
 *    depth than the menu's strongest level. Interior smoothing is unchanged,
 *    so no staircase returns. */
#define REAL3D_GAIN_Q8 256U   /* perf24: x1.0 — gain reintroduced bleed; depth via native "Real3D One Pro" row */
static u8 depth_pre[480 * 270];
static void depth_prepass(const u8 *z, u8 *out, u32 dw, u32 dh) {
    for (u32 y = 0; y < dh; ++y) {
        const u8 *row = z + (size_t)y * dw;
        u8 *o = out + (size_t)y * dw;
        for (u32 x = 0; x < dw; ++x) {
            /* perf23: no dilation. The symmetric max filter also widened near
             * objects on the side away from the warp and made halos worse. */
            u32 m = (row[x] * REAL3D_GAIN_Q8 + 128) >> 8;
            o[x] = (u8)(m > 255 ? 255 : m);
        }
    }
}

int real3d_depth_prepass_for_test(const u8 *z, u8 *out, u32 dw, u32 dh) {
    if (!z || !out || !dw || !dh) return -22;
    depth_prepass(z, out, dw, dh);
    return 0;
}

static int process_frame(const struct real3d_frame *f) {
    /* Recovered Process ABI Q2/Q3/Q4: one warped I420 frame, 480x270 depth.
     * Stride is padding, never evidence of side-by-side or a pixel format. */
    if (!initialized || validate_frame(f)) return -22;
    int side_by_side = 0;
    int chroma_half = 1;
    u32 depth_height = (f->height + 3) / 4;
    u32 depth_size = f->depth_width * depth_height;
    if (!valid(f->depth, depth_size) || !valid(f->result, 64)) return -22;

    u64 started_at = monotonic_us();
    u8 *z = map(f->depth, depth_size);
    if (!z) return -5;
    if (frames < 3 || frames % 300 == 0) {
        depth_min = 255;
        depth_max = shifted_samples = 0;
        for (u32 i = 0; i < depth_size; ++i) {
            u8 v = z[i];
            if (v < depth_min) depth_min = v;
            if (v > depth_max) depth_max = v;
            if (depth_shift(v, f->width, f->depth_width, f->fractional_bits)) ++shifted_samples;
        }
    }
    int rc = 0;
    u8 *source[3] = {NULL, NULL, NULL};
    u8 *destination[3] = {NULL, NULL, NULL};
    u32 widths[3], heights[3], src_strides[3], dst_strides[3];
    u32 src_sizes[3], dst_sizes[3];
    for (u32 channel = 0; channel < 3; ++channel) {
        widths[channel] = channel && chroma_half ? (f->width + 1) / 2 : f->width;
        heights[channel] = channel && chroma_half ? (f->height + 1) / 2 : f->height;
        src_strides[channel] = channel ? f->src_stride_uv : f->src_stride_y;
        dst_strides[channel] = channel ? f->dst_stride_uv : f->dst_stride_y;
        src_sizes[channel] = src_strides[channel] * heights[channel];
        dst_sizes[channel] = dst_strides[channel] * heights[channel];
        if (!valid(f->src[channel], src_sizes[channel]) ||
            !valid(f->dst[channel], dst_sizes[channel]) ||
            (f->src[channel] < f->dst[channel] + dst_sizes[channel] &&
             f->dst[channel] < f->src[channel] + src_sizes[channel])) {
            rc = -22;
            break;
        }
        if ((f->dst[channel] < f->depth + depth_size &&
             f->depth < f->dst[channel] + dst_sizes[channel]) ||
            (f->result < f->dst[channel] + dst_sizes[channel] &&
             f->dst[channel] < f->result + 64) ||
            (f->result < f->src[channel] + src_sizes[channel] &&
             f->src[channel] < f->result + 64)) { rc = -22; break; }
        for (u32 prior = 0; prior < channel; ++prior)
            if ((f->dst[channel] < f->dst[prior] + dst_sizes[prior] &&
                 f->dst[prior] < f->dst[channel] + dst_sizes[channel]) ||
                (f->src[channel] < f->dst[prior] + dst_sizes[prior] &&
                 f->dst[prior] < f->src[channel] + src_sizes[channel]) ||
                (f->dst[channel] < f->src[prior] + src_sizes[prior] &&
                 f->src[prior] < f->dst[channel] + dst_sizes[channel]))
                rc = -22;
        if (rc) break;
    }
    if (rc) goto done;
    for (u32 channel = 0; channel < 3; ++channel) {
        source[channel] = map(f->src[channel], src_sizes[channel]);
        destination[channel] = map_output(f->dst[channel], dst_sizes[channel], dst_strides[channel]);
        if (!source[channel] || !destination[channel]) { rc = -5; goto done; }
    }
    u64 mapped_at = monotonic_us();

#ifdef XREAL_COMPARE_ONEPRO_WARP
    int legacy = xreal_compare_algorithm() == 1;
    if (legacy) {
        rc = xreal_onepro_warp(source, destination, src_strides, dst_strides,
                               z, f->fractional_bits);
    } else
#endif
    if (!side_by_side && chroma_half && f->width == 1920 && f->height == 1080) {
        /* Four independent depth-row segments on the four Cortex-A53 cores. */
        struct segment_job jobs[4];
        u32 next_chunk = 0;
        const u8 *zp = z;
        if (f->depth_width * depth_height <= sizeof(depth_pre)) {
            depth_prepass(z, depth_pre, f->depth_width, depth_height);
            zp = depth_pre;
        }
        for (u32 i = 0; i < 4; ++i) {
            jobs[i] = (struct segment_job){
                .depth = zp, .depth_width = f->depth_width,
                .depth_height = depth_height, .fractional_bits = f->fractional_bits,
                .begin = 0, .end = depth_height, .next = &next_chunk, .rc = 0,
            };
            for (u32 channel = 0; channel < 3; ++channel) {
                jobs[i].src[channel] = source[channel];
                jobs[i].dst[channel] = destination[channel];
                jobs[i].width[channel] = widths[channel];
                jobs[i].height[channel] = heights[channel];
                jobs[i].src_stride[channel] = src_strides[channel];
                jobs[i].dst_stride[channel] = dst_strides[channel];
            }
        }
        xreal_parallel_run(segment_worker, jobs, sizeof(jobs[0]), 4);
        for (u32 i = 0; i < 4; ++i)
            if (jobs[i].rc) rc = jobs[i].rc;
    } else for (u32 channel = 0; channel < 3; ++channel) {
        int sbs = side_by_side && dst_strides[channel] >= 2 * widths[channel];
        if (!sbs && widths[channel] % f->depth_width == 0 &&
            heights[channel] % depth_height == 0 &&
            (widths[channel] / f->depth_width == 2 ||
             widths[channel] / f->depth_width == 4))
            rc = warp_plane_cells(source[channel], destination[channel], z,
                                  widths[channel], heights[channel],
                                  src_strides[channel], dst_strides[channel],
                                  f->depth_width, depth_height, f->fractional_bits);
        else rc = warp_plane(source[channel], destination[channel], z,
                             widths[channel], heights[channel],
                             src_strides[channel], dst_strides[channel],
                             f->depth_width, f->depth_width, depth_height, sbs, f->fractional_bits);
        if (rc) break;
    }
    u64 warped_at = monotonic_us();
#ifdef XREAL_COMPARE_ONEPRO_WARP
    /* The original mesh writes the opposite view back into source planes.
     * All workers must finish before either view is flushed or released. */
    if (!rc && legacy) for (u32 channel = 0; channel < 3; ++channel)
        if (ar_hal_sys_mmz_flush_cache_pa(f->src[channel], src_sizes[channel])) {
            rc = -5;
            break;
        }
#endif
    if (!rc) for (u32 channel = 0; channel < 3; ++channel)
        if (ar_hal_sys_mmz_flush_cache_pa(f->dst[channel], dst_sizes[channel])) {
            rc = -5;
            break;
        }
    if (frames < 3 || frames % 300 == 0) {
        map_us = (u32)(mapped_at - started_at);
        warp_us = (u32)(warped_at - mapped_at);
        flush_us = (u32)(monotonic_us() - warped_at);
    }
#ifdef XREAL_CAPTURE_REAL3D
    if (!rc && !captured && frames == 120) {
        captured = 1;
        int capture_rc = capture_plane("/usrdata/real3d-input.pgm", source[0],
                                       f->width, f->height, f->src_stride_y);
        capture_rc |= capture_plane("/usrdata/real3d-warped.pgm", destination[0],
                                    f->width, f->height, f->dst_stride_y);
        capture_rc |= capture_plane("/usrdata/real3d-displacement.pgm", z,
                                    f->depth_width, depth_height, f->depth_width);
        char meta[128];
        int n = snprintf(meta, sizeof(meta),
            "{\"frame\":%u,\"fractional_bits\":%u,\"capture_rc\":%d}\n",
            frames, f->fractional_bits, capture_rc);
        int fd = open("/usrdata/real3d-capture.json", 1 | 64 | 512, 0600);
        if (fd >= 0) {
            if (n <= 0 || n >= (int)sizeof(meta) || write_all(fd, meta, (size_t)n))
                capture_rc = -1;
            close(fd);
        } else capture_rc = -1;
        diagnostic("capture", f, capture_rc);
    }
#endif
done:
    for (u32 channel = 0; channel < 3; ++channel) {
        if (destination[channel]) ar_hal_sys_munmap(destination[channel], dst_sizes[channel]);
        if (source[channel]) ar_hal_sys_munmap(source[channel], src_sizes[channel]);
    }
    ar_hal_sys_munmap(z, depth_size);
    return rc;
}

int xreal_real3d_rpc(u32 core, const struct rpc *call, u32 sync) {
    if (core || !call || sync != 1 || !call->args) return -22;
    if (!strcmp(call->name, "ArArmWarp2DTo3DInit")) {
        if (call->length != 8) return -22;
        u64 result = *(const u64 *)call->args;
        int rc = set_result(result, 0);
        initialized = rc == 0;
        frames = 0;
        pace_last_start = pace_slept_us = pace_late = 0;
        diagnostic("init", NULL, rc);
        return rc;
    }
    if (!strcmp(call->name, "ArArmWarp2DTo3DClose")) {
        if (call->length != 8) return -22;
        u64 result = *(const u64 *)call->args;
        initialized = 0;
        int rc = set_result(result, 0);
        diagnostic("close", NULL, rc);
        return rc;
    }
    if (!strcmp(call->name, "ArArmWarp2DTo3DProcess")) {
        if (call->length != sizeof(struct real3d_frame)) return -22;
        const struct real3d_frame *f = call->args;
        u64 call_start = monotonic_us();
        int rc = process_frame(f);
        if (rc || frames < 3 || frames % 300 == 0) {
#ifdef XREAL_COMPARE_ONEPRO_WARP
            diagnostic(xreal_compare_algorithm() == 1 ?
                       "process-onepro-mesh" : "process-proplus-port", f, rc);
#else
            diagnostic("process", f, rc);
#endif
        }
        if (rc) {
            /* A rejected descriptor may alias result with a frame plane.
             * Only a fully validated descriptor permits a status write.
             * Runtime map/flush failures must not leave an old success flag. */
            if (!validate_frame(f)) (void)set_result(f->result, rc);
            return rc;
        }
        ++frames;
        int status = set_result(f->result, 0);
        if (pace_last_start) {
            /* perf10 fix: from THIS call's start (perf9 used the previous
             * call's start, which let every other frame through unpaced). */
            u64 elapsed = monotonic_us() - call_start;
            if (elapsed < PACE_US) {
                u64 wait = PACE_US - elapsed;
                struct timespec t = { (long)(wait / 1000000ULL), (long)(wait % 1000000ULL) * 1000 };
                (void)nanosleep(&t, NULL);
                pace_slept_us += wait;
            } else {
                ++pace_late;
            }
        }
        pace_last_start = call_start;
        if (frames % 300 == 0) {
            char line[160];
            int n = snprintf(line, sizeof(line), "Real3D CPU pace frame=%u slept_avg_us=%lu late=%lu target_us=%lu\n",
                             frames, (unsigned long)(pace_slept_us / 300), (unsigned long)pace_late,
                             (unsigned long)PACE_US);
            pace_slept_us = pace_late = 0;
            if (n > 0 && n < (int)sizeof(line)) {
                int fd = open("/usrdata/real3d-cpu.log", 1 | 64 | 1024, 0600);
                if (fd >= 0) { (void)write(fd, line, (size_t)n); (void)close(fd); }
            }
        }
        return status;
    }
    return -95;
}

int real3d_warp_plane_for_test(const u8 *src, u8 *dst, const u8 *depth,
                               u32 width, u32 height, u32 src_stride,
                               u32 dst_stride, u32 depth_width,
                               u32 depth_stride, u32 depth_height, int sbs, u32 fractional_bits) {
    return warp_plane(src, dst, depth, width, height, src_stride, dst_stride,
                      depth_width, depth_stride, depth_height, sbs, fractional_bits);
}

int real3d_warp_cells_for_test(const u8 *src, u8 *dst, const u8 *depth,
                               u32 width, u32 height, u32 src_stride,
                               u32 dst_stride, u32 depth_width,
                               u32 depth_height, u32 fractional_bits) {
    return warp_plane_cells(src, dst, depth, width, height, src_stride,
                            dst_stride, depth_width, depth_height, fractional_bits);
}

int real3d_warp_cells_range_for_test(const u8 *src, u8 *dst, const u8 *depth,
                                     u32 width, u32 height, u32 src_stride,
                                     u32 dst_stride, u32 depth_width,
                                     u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_plane_cells_range(src, dst, depth, width, height,
                                  src_stride, dst_stride, depth_width,
                                  depth_height, begin, end, fractional_bits);
}

int real3d_warp_cells_smooth_range_for_test(const u8 *src, u8 *dst, const u8 *depth,
                                            u32 width, u32 height, u32 src_stride,
                                            u32 dst_stride, u32 depth_width,
                                            u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_plane_cells_smooth_range(src, dst, depth, width, height,
                                         src_stride, dst_stride, depth_width,
                                         depth_height, begin, end, fractional_bits);
}

int real3d_warp_cells_bilinear_range_for_test(const u8 *src, u8 *dst, const u8 *depth,
                                              u32 width, u32 height, u32 src_stride,
                                              u32 dst_stride, u32 depth_width,
                                              u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_plane_cells_bilinear_range(src, dst, depth, width, height,
                                           src_stride, dst_stride, depth_width,
                                           depth_height, begin, end, fractional_bits);
}

int real3d_warp_luma_cubic_range_for_test(const u8 *src, u8 *dst, const u8 *depth,
                                          u32 width, u32 height, u32 src_stride,
                                          u32 dst_stride, u32 depth_width,
                                          u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_luma_cubic_range(src, dst, depth, width, height, src_stride, dst_stride,
                                 depth_width, depth_height, begin, end, fractional_bits);
}

int real3d_warp_chroma_pair_range_for_test(const u8 *u_src, u8 *u_dst, const u8 *v_src, u8 *v_dst,
                                           const u8 *depth, u32 width, u32 height, u32 stride,
                                           u32 depth_width, u32 depth_height, u32 begin, u32 end,
                                           u32 fractional_bits) {
    return warp_chroma_pair_range(u_src, u_dst, v_src, v_dst, depth, width, height,
                                  stride, stride, stride, stride, depth_width, depth_height,
                                  begin, end, fractional_bits);
}

int real3d_warp_luma_jbu_range_for_test(const u8 *src, u8 *dst, const u8 *depth,
                                        u32 width, u32 height, u32 src_stride,
                                        u32 dst_stride, u32 depth_width,
                                        u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_luma_jbu_range(src, dst, depth, width, height, src_stride, dst_stride,
                               depth_width, depth_height, begin, end, fractional_bits);
}

int real3d_warp_luma_reproject_range_for_test(const u8 *src, u8 *dst, const u8 *depth,
                                              u32 width, u32 height, u32 src_stride,
                                              u32 dst_stride, u32 depth_width,
                                              u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    return warp_luma_reproject_range(src, dst, depth, width, height, src_stride, dst_stride,
                                     depth_width, depth_height, begin, end, fractional_bits);
}
