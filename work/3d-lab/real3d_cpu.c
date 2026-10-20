/* Experimental CPU fallback for Pilot 2.0 Real3D RPCs on One Pro.
 * The vendor's 480-pixel depth map drives horizontal stereo disparity.
 * A failed ABI/layout check leaves the existing HDR path untouched.
 */
#include "runtime.h"
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

/* The live Real3D frame is 1920x1080 with a 480x270 depth map.  Each
 * depth sample covers four luma pixels or two chroma pixels.  Process a
 * complete depth cell at once so that division and disparity calculation
 * are not repeated for every output pixel. */
static int warp_plane_cells_range(const u8 *src, u8 *dst, const u8 *depth,
                                  u32 width, u32 height, u32 src_stride,
                                  u32 dst_stride, u32 depth_width,
                                  u32 depth_height, u32 begin, u32 end, u32 fractional_bits) {
    if (fractional_bits < 2 || fractional_bits > 4 ||
        !depth_width || !depth_height || !width || !height ||
        !src || !dst || !depth || src_stride < width || dst_stride < width) return -22;
    u32 pixels = width / depth_width;
    u32 rows = height / depth_height;
    if ((pixels != 2 && pixels != 4) || !rows ||
        width % depth_width || height % depth_height ||
        begin > end || end > depth_height) return -22;
    if (depth_width > 480) return -22;
    for (u32 zy = begin; zy < end; ++zy) {
        const u8 *z = depth + (size_t)zy * depth_width;
        u16 source_x[480];
        for (u32 zx = 0; zx < depth_width; ++zx)
            source_x[zx] = (u16)(zx * pixels * 32 +
                depth_shift(z[zx], width, depth_width, fractional_bits));
        for (u32 ry = 0; ry < rows; ++ry) {
            u32 y = zy * rows + ry;
            const u8 *s = src + (size_t)y * src_stride;
            u8 *d = dst + (size_t)y * dst_stride;
            for (u32 zx = 0, x = 0; zx < depth_width; ++zx, x += pixels) {
                u32 q = source_x[zx], sx = q / 32;
                if (!(q & 31) && sx + pixels <= width) {
                    if (pixels == 4)
                        __builtin_memcpy(d + x, s + sx, 4);
                    else
                        __builtin_memcpy(d + x, s + sx, 2);
                } else {
                    for (u32 k = 0; k < pixels; ++k)
                        d[x + k] = sample_q5(s, (int)q + (int)k * 32, width);
                }
            }
        }
    }
    return 0;
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
    int rc;
};

static void *segment_worker(void *arg) {
    struct segment_job *j = arg;
    j->rc = 0;
    for (u32 channel = 0; channel < 3; ++channel) {
        j->rc = warp_plane_cells_range(j->src[channel], j->dst[channel],
                                        j->depth, j->width[channel],
                                        j->height[channel], j->src_stride[channel],
                                        j->dst_stride[channel], j->depth_width,
                                        j->depth_height, j->begin, j->end, j->fractional_bits);
        if (j->rc) break;
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
        destination[channel] = map(f->dst[channel], dst_sizes[channel]);
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
        for (u32 i = 0; i < 4; ++i) {
            jobs[i] = (struct segment_job){
                .depth = z, .depth_width = f->depth_width,
                .depth_height = depth_height, .fractional_bits = f->fractional_bits,
                .begin = depth_height * i / 4,
                .end = depth_height * (i + 1) / 4, .rc = 0,
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
        return set_result(f->result, 0);
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
