/* Test-only bridge to the unchanged Pilot 1.9.1 One Pro mesh kernel.
 * Depth inference remains Pilot 2.0. This is NOT the complete old 3D stack.
 * The original kernel updates BOTH source and destination eye images.
 */
#ifdef XREAL_COMPARE_ONEPRO_WARP
#include "runtime.h"
#include "onepro_warp.h"
typedef unsigned long u64;
typedef unsigned int u32;
typedef unsigned char u8;
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern int memcmp(const void *, const void *, u64);
extern int open(const char *, int, ...);
extern long read(int, void *, u64);
extern long write(int, const void *, u64);
extern int close(int);
extern int fsync(int);
extern int rename(const char *, const char *);
extern int pthread_once(int *, void (*)(void));

static int selection_once;
static int algorithm = 2; /* Existing Pro+ port is the default. */
static int load_kernel(void);
static void read_selection(void) {
    char value[2];
    int fd = open("/usrdata/real3d-algorithm", 0);
    if (fd < 0) return;
    long n = read(fd, value, sizeof(value));
    close(fd);
    if (n == 1 && (value[0] == '1' || value[0] == '2'))
        __atomic_store_n(&algorithm, value[0] - '0', __ATOMIC_RELEASE);
}
int xreal_compare_algorithm(void) {
    if (pthread_once(&selection_once, read_selection)) return 2;
    return __atomic_load_n(&algorithm, __ATOMIC_ACQUIRE);
}
int xreal_compare_get(int *value) {
    if (!value) return -22;
    *value = xreal_compare_algorithm();
    return 0;
}
int xreal_compare_set(int value) {
    if (value != 1 && value != 2) return -22;
    (void)xreal_compare_algorithm();
    if (value == 1) {
        if (xreal_frame_lock()) return -5;
        int loaded = load_kernel();
        xreal_frame_unlock();
        if (loaded) return loaded;
    }
    /* Algorithm selection must never write the native depth-strength property. */
    if (xreal_frame_lock()) return -5;
    char byte = (char)('0' + value);
    int fd = open("/usrdata/real3d-algorithm.tmp", 1 | 64 | 512, 0600);
    if (fd < 0) { xreal_frame_unlock(); return -5; }
    int ok = write(fd, &byte, 1) == 1 && fsync(fd) == 0;
    if (close(fd)) ok = 0;
    if (ok && rename("/usrdata/real3d-algorithm.tmp",
                     "/usrdata/real3d-algorithm") == 0)
        __atomic_store_n(&algorithm, value, __ATOMIC_RELEASE);
    else ok = 0;
    xreal_frame_unlock();
    return ok ? 0 : -5;
}

/* cv::Mat header used by the unchanged kernel. No OpenCV ownership object:
 * data stays with the caller and the kernel only reads data/cols/step. */
struct mat {
    u32 flags, dims, rows, cols;
    u8 *data;
    u64 reserved[6];
    u64 *step;
    u64 steps[2];
};
_Static_assert(sizeof(struct mat) == 96, "One Pro cv::Mat ABI");
_Static_assert(__builtin_offsetof(struct mat, step) == 72, "Mat step");
struct job {
    struct mat *depth;
    u32 begin, end;
    u8 *src_y; u32 src_y_stride, pad0;
    u8 *src_u; u32 src_u_stride, pad1;
    u8 *src_v; u32 src_v_stride, pad2;
    u8 *dst_y; u32 dst_y_stride, pad3;
    u8 *dst_u; u32 dst_u_stride, pad4;
    u8 *dst_v; u32 dst_v_stride;
    float multiplier;
    u32 width, pad5;
};
_Static_assert(sizeof(struct job) == 120, "One Pro lambda ABI");
_Static_assert(__builtin_offsetof(struct job, multiplier) == 108, "Multiplier");
static void (*kernel)(struct job *);
static u8 integer_depth[480 * 270];
static int load_attempted, load_error = -5;
static int load_kernel(void) {
    if (kernel) return 0;
    if (load_attempted) return load_error;
    load_attempted = 1;
    /* Only this hash-pinned library is packaged by compare_modes.py. Never
     * register its plugin or replace Pilot 2's ImagePostProcessor interface. */
    void *handle = dlopen("/usrdata/pilot/bin/libxreal_onepro_mesh.so", 2);
    if (!handle) return -5;
    u8 *entry = dlsym(handle, "NRPluginLoad");
    if (!entry) return -5;
    u8 *base = entry - 0x1d9d0;
    static const u8 expected[] = {
        0xfd,0x7b,0xb1,0xa9,0x41,0xd0,0x3b,0xd5,
        0xfd,0x03,0x00,0x91,0xf9,0x23,0x00,0xf9,
    };
    if (memcmp(base + 0x38150, expected, sizeof(expected))) {
        load_error = -22;
        return load_error;
    }
    /* Original wrapper initializes this reciprocal table before dispatch.
     * No registered DepthManager shares this private library instance. */
    u32 *reciprocal = (u32 *)(base + 0x1ee2c0);
    reciprocal[0] = 0;
    for (u32 i = 1; i < 256; ++i) reciprocal[i] = 262144U / i;
    kernel = (void (*)(struct job *))(base + 0x38150);
    return 0;
}
static void *run_job(void *arg) { kernel(arg); return (void *)0; }

int xreal_onepro_warp(u8 *src[3], u8 *dst[3], const u32 *ss, const u32 *ds,
                      const u8 *depth, u32 fractional_bits) {
    if (!src || !dst || !ss || !ds || !depth ||
        fractional_bits < 2 || fractional_bits > 4) return -22;
    for (u32 i = 0; i < 3; ++i)
        if (!src[i] || !dst[i] ||
            (i == 0 ? (ss[i] != 1920 && ss[i] != 2048) :
                       (ss[i] != 960 && ss[i] != 1024)) ||
            (i == 0 ? (ds[i] != 1920 && ds[i] != 2048) :
                       (ds[i] != 960 && ds[i] != 1024))) return -22;
    int rc = load_kernel();
    if (rc) return rc; /* No source buffer has been changed. */
    /* Legacy mesh takes integer disparity, not the new Q2/Q3/Q4 map.
     * Truncation bounds displacement by the supplied map; never amplify it. */
    u32 maximum = 0;
    for (u32 i = 0; i < sizeof(integer_depth); ++i) {
        u32 value = depth[i] >> fractional_bits;
        integer_depth[i] = (u8)value;
        if (value > maximum) maximum = value;
    }
    struct mat mat = {.flags=0x42ff4000, .dims=2, .rows=270, .cols=480,
                      .data=integer_depth, .steps={480, 1}};
    mat.step = mat.steps;
    struct job jobs[4];
    for (u32 i = 0; i < 4; ++i) {
        jobs[i] = (struct job){
            .depth=&mat, .begin=(270*i/4)*4, .end=(270*(i+1)/4)*4,
            .src_y=src[0], .src_y_stride=ss[0],
            .src_u=src[1], .src_u_stride=ss[1],
            .src_v=src[2], .src_v_stride=ss[2],
            .dst_y=dst[0], .dst_y_stride=ds[0],
            .dst_u=dst[1], .dst_u_stride=ds[1],
            .dst_v=dst[2], .dst_v_stride=ds[2],
            .multiplier=(float)maximum, .width=1920,
        };
    }
    xreal_parallel_run(run_job, jobs, sizeof(jobs[0]), 4);
    return 0;
}
#endif
