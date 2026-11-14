/* Native Linux/WSL fault-injection regression for firmware/cpu_adapter.c.
 * Built only by run_cpu_adapter_faults.sh; never linked into the target adapter.
 * The adapter object is compiled with -D renames of its libc/LUT-builder imports
 * (malloc, free, clock_gettime, open, write, close, xreal_build_rgb_table_range)
 * to the fi_* shims below. The production source is not modified.
 *
 * Contracts checked:
 *  1. LUT map failure with fully validated, disjoint LUT/result ranges replaces
 *     the previous result with -5; the LUT input is not written.
 *  2. Re-initialization releases the previous LUT table before the new table is
 *     allocated/built (never two live tables). If malloc or the table build
 *     fails, Init reports the error through result (rc 0, status -12 / build rc)
 *     and every following ProcessRgbToYuv is rejected with -22 before any HAL
 *     mapping: no previous LUT is ever applied. A later successful Init recovers.
 *  3. A clock_gettime failure never prints a time derived from that failed
 *     reading and does not disturb frame processing.
 *  4. HDR ProcessRgbToYuv faults (HAL fake: 32 MiB RAM at 0x33000000):
 *     a. a plane mmap failure gives rc 0 with result -5, planes untouched,
 *        no mapping left behind;
 *     b. result aliasing any plane is rejected with -22 before any mapping;
 *     c. 1920x1080 frame whose global (>1 MiB, whole plane 0) flush fails gives
 *        rc 0 with result -5.
 *     Each has a successful control frame with the same setup.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* hal_stub.c */
void test_faults(uint64_t mapping, uint64_t flushing);
unsigned test_mappings(void);
unsigned test_map_faults(void);
unsigned test_flush_faults(void);
unsigned test_last_fault_size(void);
int test_live_maps(void);
void *test_memory(uint64_t pa);
/* lut_fast.c (real implementation, wrapped by fi_build_rgb_table_range) */
int xreal_build_rgb_table_range(const uint16_t *, unsigned long long *, uint32_t, uint32_t);

struct rpc {char name[32]; uint64_t reserved; const void *args; uint32_t length;};
struct init_args {uint64_t lut, result; uint32_t a, b;};
struct frame_args {uint64_t planes[3], result; uint32_t strides[3], width, height;};
int xreal_cpu_rpc(uint32_t, const struct rpc *, uint32_t);

/* Real3D is outside this test; the adapter forwards unknown RPCs here. */
int xreal_real3d_rpc(uint32_t core, const struct rpc *call, uint32_t sync) {
    (void)core; (void)call; (void)sync; return -38;
}

/* ---- fault-injection shims ------------------------------------------- */
static int fail_malloc_next;       /* fail the next adapter malloc once     */
static int malloc_attempts;
static long live_allocs;           /* adapter allocations not yet freed     */
static long live_at_malloc;        /* live_allocs when malloc was last called */
static long max_live;              /* peak live_allocs since last reset     */
static volatile int build_fail_rc; /* nonzero: every build range fails      */
static unsigned clock_fail_mask;   /* bit i: fail the i-th clock_gettime    */
static unsigned clock_calls;

void *fi_malloc(unsigned long n) {
    ++malloc_attempts;
    live_at_malloc = live_allocs;
    if (fail_malloc_next) { fail_malloc_next = 0; errno = ENOMEM; return NULL; }
    void *p = malloc(n);
    if (p && ++live_allocs > max_live) max_live = live_allocs;
    return p;
}
void fi_free(void *p) {
    if (p) { --live_allocs; free(p); }
}
/* Called concurrently by pool workers; build_fail_rc is only changed by the
 * test thread between RPCs (ordered by the pool's semaphores). */
int fi_build_rgb_table_range(const uint16_t *cube, void *table, uint32_t begin, uint32_t end) {
    if (build_fail_rc) return build_fail_rc;
    return xreal_build_rgb_table_range(cube, table, begin, end);
}
int fi_clock_gettime(int clock, struct timespec *ts) {
    unsigned i = clock_calls++;
    if (i < 32 && (clock_fail_mask >> i & 1)) { errno = EINVAL; return -1; } /* ts untouched */
    return clock_gettime(clock, ts);
}

/* The adapter's trace()/record() output is captured in memory: no /usrdata. */
#define FAKE_LOG_FD 1000
static char captured[1 << 16];
static size_t captured_len;
int fi_open(const char *path, int flags, ...) { (void)path; (void)flags; return FAKE_LOG_FD; }
int fi_close(int fd) { (void)fd; return 0; }
long fi_write(int fd, const void *buf, unsigned long n) {
    if (fd != 2 && fd != FAKE_LOG_FD) { errno = EBADF; return -1; }
    size_t room = sizeof(captured) - 1 - captured_len, k = n < room ? n : room;
    memcpy(captured + captured_len, buf, k);
    captured_len += k; captured[captured_len] = 0;
    if (getenv("FI_VERBOSE") && fd == 2) (void)!write(2, buf, n);
    return (long)n;
}
static void capture_reset(void) { captured_len = 0; captured[0] = 0; }

/* ---- harness ---------------------------------------------------------- */
static int failures, checks;
#define CHECK(cond, ...) do { ++checks; if (!(cond)) { ++failures; \
    printf("FAIL %s:%d: %s -- ", __FILE__, __LINE__, #cond); \
    printf(__VA_ARGS__); putchar('\n'); } } while (0)

enum { RESULT = 0x33000000, LUT = 0x33100000, LUT_BYTES = 14739, CELLS = 4913,
       W = 8, H = 2, PLANE = 0x33200000 };
#define SENTINEL 0x5a5a5a5a

static volatile int32_t *status(void) { return test_memory(RESULT); }

static int rpc(const char *name, const void *args, uint32_t length) {
    struct rpc c;
    memset(&c, 0, sizeof c);
    memcpy(c.name, name, strlen(name));
    c.args = args; c.length = length;
    return xreal_cpu_rpc(0, &c, 1);
}
static void fill_lut(uint8_t r, uint8_t g, uint8_t b) {
    uint8_t *p = test_memory(LUT);
    memset(p, r, CELLS); memset(p + CELLS, g, CELLS); memset(p + 2 * CELLS, b, CELLS);
}
static int init_lut(uint64_t lut, uint64_t result) {
    struct init_args a = {lut, result, 0, 0};
    return rpc("ArArmlut3dInit", &a, 24);
}
static int close_lut(void) {
    uint64_t p = RESULT;
    return rpc("ArArmlut3dClose", &p, 8);
}
static uint64_t plane_pa(int c) { return PLANE + (uint64_t)c * 0x1000; }
static uint8_t pattern(int c, int i) { return (uint8_t)(i * 37 + c * 91); }
static void fill_planes(void) {
    for (int c = 0; c < 3; c++) {
        uint8_t *p = test_memory(plane_pa(c));
        for (int i = 0; i < W * H; i++) p[i] = pattern(c, i);
    }
}
/* True if no plane byte was changed since fill_planes(). */
static int planes_untouched(void) {
    for (int c = 0; c < 3; c++) {
        const uint8_t *p = test_memory(plane_pa(c));
        for (int i = 0; i < W * H; i++) if (p[i] != pattern(c, i)) return 0;
    }
    return 1;
}
static int send_frame(uint64_t result) {
    struct frame_args f = {{plane_pa(0), plane_pa(1), plane_pa(2)}, result, {W, W, W}, W, H};
    return rpc("ArArmlut3dProcessRgbToYuv", &f, 52); /* ABI length, without tail padding */
}
static int process(void) {
    fill_planes();
    return send_frame(RESULT);
}
/* Full-size frame: 3 x 1920*1080 = 3 x 2073600 bytes, each > 1 MiB, placed in
 * disjoint 4 MiB slots inside the 32 MiB fake (end 0x33dfa400 < 0x35000000). */
enum { BIG_W = 1920, BIG_H = 1080, BIG_PLANE = 0x33400000 };
static uint64_t big_plane_pa(int c) { return BIG_PLANE + (uint64_t)c * 0x400000; }
static int process_big(void) {
    for (int c = 0; c < 3; c++) memset(test_memory(big_plane_pa(c)), 0x11 * (c + 1), BIG_W * BIG_H);
    struct frame_args f = {{big_plane_pa(0), big_plane_pa(1), big_plane_pa(2)}, RESULT,
                           {BIG_W, BIG_W, BIG_W}, BIG_W, BIG_H};
    return rpc("ArArmlut3dProcessRgbToYuv", &f, 52);
}
static int big_planes_are(uint8_t r, uint8_t g, uint8_t b) {
    const uint8_t want[3] = {r, g, b};
    for (int c = 0; c < 3; c++) {
        const uint8_t *p = test_memory(big_plane_pa(c));
        for (int i = 0; i < BIG_W * BIG_H; i++) if (p[i] != want[c]) return 0;
    }
    return 1;
}
/* A constant LUT (planes filled with r,g,b) maps every pixel exactly to r,g,b. */
static int planes_are(uint8_t r, uint8_t g, uint8_t b) {
    const uint8_t want[3] = {r, g, b};
    for (int c = 0; c < 3; c++) {
        const uint8_t *p = test_memory(plane_pa(c));
        for (int i = 0; i < W * H; i++) if (p[i] != want[c]) return 0;
    }
    return 1;
}
static void plane0_text(char *out, size_t n) {
    const uint8_t *p = test_memory(plane_pa(0));
    snprintf(out, n, "R plane starts %u,%u,%u", p[0], p[1], p[2]);
}
/* Fill stack below the caller with a pattern so that any uninitialized local
 * read by the adapter is a visible, reproducible value in the trace output. */
static __attribute__((noinline)) void poison_stack(void) {
    volatile unsigned char area[32768];
    for (size_t i = 0; i < sizeof area; i++) area[i] = 0x5a;
}

/* ---- 1: LUT map failure must report through result --------------------- */
static void test_lut_map_failure_reports_result(void) {
    puts("-- 1: LUT map failure with validated disjoint inputs");
    test_faults(0, 0);
    fill_lut(10, 20, 30);
    CHECK(init_lut(LUT, RESULT) == 0 && *status() == 0, "baseline init failed");

    /* Negative controls: aliasing result/LUT must be rejected before any HAL call. */
    const uint64_t aliased[] = {LUT, LUT + LUT_BYTES - 4, LUT - 32};
    for (unsigned i = 0; i < sizeof aliased / sizeof aliased[0]; i++) {
        int32_t *alias = test_memory(aliased[i]);
        uint8_t before[64]; memcpy(before, alias, sizeof before);
        test_faults(0, 0);
        int rc = init_lut(LUT, aliased[i]);
        CHECK(rc == -22 && test_mappings() == 0 && !memcmp(before, alias, sizeof before),
              "aliased result 0x%llx: rc=%d mappings=%u", (unsigned long long)aliased[i], rc, test_mappings());
    }

    fill_lut(200, 210, 220);
    uint8_t snapshot[LUT_BYTES];
    memcpy(snapshot, test_memory(LUT), LUT_BYTES);
    *status() = SENTINEL;
    test_faults(LUT, 0);                  /* only the LUT mapping fails */
    int rc = init_lut(LUT, RESULT);
    unsigned mapped = test_mappings();
    test_faults(0, 0);
    CHECK(mapped > 0, "inputs were not accepted; LUT map was never attempted (rc=%d)", rc);
    CHECK(rc == -5, "LUT map failure must preserve RPC error: rc=%d", rc);
    CHECK(*status() == -5, "result not updated on LUT map failure: result=0x%x (sentinel 0x%x), rc=%d",
          (unsigned)*status(), SENTINEL, rc);
    CHECK(!memcmp(snapshot, test_memory(LUT), LUT_BYTES), "LUT input memory was modified");

    *status() = SENTINEL;
    rc = process();
    CHECK(rc == 0 && *status() == 0 && planes_are(10, 20, 30),
          "previous LUT not usable after map failure: rc=%d result=%d", rc, (int)*status());
    close_lut();
}

/* ---- 2: re-init releases the old LUT first; failure leaves no LUT ------- */
/* After a failed Init the adapter has no table: ProcessRgbToYuv returns -22
 * directly (before mapping anything), so result keeps the sentinel and the
 * planes keep their input bytes. Two consecutive frames are checked. */
static void expect_frames_rejected(const char *after) {
    char text[64];
    for (int n = 0; n < 2; n++) {
        fill_planes();
        *status() = SENTINEL;
        test_faults(0, 0);
        int rc = send_frame(RESULT);
        unsigned mapped = test_mappings();
        plane0_text(text, sizeof text);
        CHECK(rc == -22, "%s: frame %d not rejected: rc=%d", after, n, rc);
        CHECK(mapped == 0, "%s: frame %d mapped %u ranges before rejection", after, n, mapped);
        CHECK(*status() == SENTINEL, "%s: frame %d wrote result=%d", after, n, (int)*status());
        CHECK(planes_untouched(), "%s: frame %d converted with a stale LUT: %s", after, n, text);
        CHECK(test_live_maps() == 0, "%s: frame %d left %d mappings", after, n, test_live_maps());
    }
}
/* Init over an existing table; checks the old one is freed before the new
 * table is allocated (so at most one table is ever live). */
static int reinit_checked(const char *what, int *rc) {
    malloc_attempts = 0; live_at_malloc = -1; max_live = live_allocs;
    *status() = SENTINEL;
    *rc = init_lut(LUT, RESULT);
    CHECK(malloc_attempts == 1, "%s: expected one table malloc, got %d (rc=%d)", what, malloc_attempts, *rc);
    CHECK(live_at_malloc == 0, "%s: old LUT still live when new table was allocated (live=%ld)",
          what, live_at_malloc);
    CHECK(max_live <= 1, "%s: %ld tables live at once", what, max_live);
    return *status();
}
static void test_failed_reinit_drops_old_lut(void) {
    puts("-- 2: re-initialization frees the old LUT first; failure rejects frames");
    test_faults(0, 0);
    fill_lut(10, 20, 30);
    CHECK(init_lut(LUT, RESULT) == 0 && *status() == 0, "baseline init failed");
    CHECK(process() == 0 && *status() == 0 && planes_are(10, 20, 30), "baseline frame wrong");
    CHECK(live_allocs == 1, "expected one live LUT table, have %ld", live_allocs);

    /* 2a: allocation of the new table fails. */
    int rc, st;
    fill_lut(200, 210, 220);
    fail_malloc_next = 1;
    st = reinit_checked("2a malloc failure", &rc);
    CHECK(!fail_malloc_next, "2a: injected malloc failure was not consumed");
    fail_malloc_next = 0;
    CHECK(rc == 0 && st == -12, "2a: malloc failure must be reported via result: rc=%d result=%d", rc, st);
    CHECK(live_allocs == 0, "2a: table still live after failed re-init: live=%ld", live_allocs);
    expect_frames_rejected("after malloc failure");

    /* Recovery control: Init with no current table succeeds and is applied. */
    fill_lut(40, 50, 60);
    malloc_attempts = 0; *status() = SENTINEL;
    rc = init_lut(LUT, RESULT);
    CHECK(rc == 0 && *status() == 0 && live_allocs == 1 && malloc_attempts == 1,
          "recovery init after 2a failed: rc=%d result=%d live=%ld", rc, (int)*status(), live_allocs);
    CHECK(process() == 0 && *status() == 0 && planes_are(40, 50, 60), "recovered LUT not applied");

    /* 2b: building the new table fails (all ranges return -77). */
    fill_lut(200, 210, 220);
    build_fail_rc = -77;
    st = reinit_checked("2b build failure", &rc);
    build_fail_rc = 0;
    CHECK(rc == 0 && st == -77, "2b: build failure must be reported via result: rc=%d result=%d", rc, st);
    CHECK(live_allocs == 0, "2b: table still live after failed build: live=%ld", live_allocs);
    expect_frames_rejected("after build failure");

    /* Positive control: successful Init after failure, then a successful
     * re-init over an existing table (old freed first, new one applied). */
    *status() = SENTINEL;
    rc = init_lut(LUT, RESULT);
    CHECK(rc == 0 && *status() == 0 && live_allocs == 1,
          "init after 2b failed: rc=%d result=%d live=%ld", rc, (int)*status(), live_allocs);
    CHECK(process() == 0 && *status() == 0 && planes_are(200, 210, 220), "new LUT not applied");
    fill_lut(10, 20, 30);
    st = reinit_checked("successful re-init", &rc);
    CHECK(rc == 0 && st == 0 && live_allocs == 1,
          "successful re-init: rc=%d result=%d live=%ld", rc, st, live_allocs);
    CHECK(process() == 0 && *status() == 0 && planes_are(10, 20, 30), "re-initialized LUT not applied");
    CHECK(close_lut() == 0 && *status() == 0 && live_allocs == 0, "close did not release the table");
    expect_frames_rejected("after close");
}

/* ---- 3: clock_gettime failure ------------------------------------------ */
static void test_clock_failure(void) {
    puts("-- 3: clock_gettime failure");
    /* mask bits: 0 = before conversion, 1 = after conversion, 2 = after flush */
    const unsigned masks[] = {0, 1, 2, 4, 7};
    for (unsigned m = 0; m < sizeof masks / sizeof masks[0]; m++) {
        unsigned mask = masks[m];
        test_faults(0, 0);
        fill_lut(10, 20, 30);
        CHECK(init_lut(LUT, RESULT) == 0 && *status() == 0, "init failed (mask %u)", mask);
        capture_reset();                   /* frame_count is 0: timing is traced */
        clock_calls = 0; clock_fail_mask = mask; *status() = SENTINEL;
        poison_stack();
        int rc = process();
        clock_fail_mask = 0;
        CHECK(rc == 0 && *status() == 0 && planes_are(10, 20, 30),
              "mask %u: frame disturbed rc=%d result=%d", mask, rc, (int)*status());
        CHECK(strstr(captured, "frame processed rc=0") != NULL, "mask %u: frame not counted", mask);
        int before = !(mask & 1), converted = !(mask & 2), flushed = !(mask & 4);
        int has_convert = strstr(captured, "convert microseconds") != NULL;
        int has_flush = strstr(captured, "flush microseconds") != NULL;
        int has_stamp = strstr(captured, "frame timestamp microseconds") != NULL;
        if (!mask) {
            CHECK(has_convert && has_flush && has_stamp, "control: timing traces not observed:\n%s", captured);
        } else {
            CHECK(!has_convert || (before && converted),
                  "mask %u: conversion time printed from a failed clock reading:\n%s", mask, captured);
            CHECK(!has_flush || (converted && flushed),
                  "mask %u: flush time printed from a failed clock reading:\n%s", mask, captured);
            CHECK(!has_stamp || flushed,
                  "mask %u: timestamp printed from a failed clock reading:\n%s", mask, captured);
        }
        close_lut();
    }
}

/* ---- 4a: HDR plane mmap failure ----------------------------------------- */
static void test_frame_plane_map_failure(void) {
    puts("-- 4a: ProcessRgbToYuv plane mmap failure");
    test_faults(0, 0);
    fill_lut(10, 20, 30);
    CHECK(init_lut(LUT, RESULT) == 0 && *status() == 0, "init failed");
    /* Control: identical frame without faults. */
    *status() = SENTINEL; test_faults(0, 0);
    int rc = process();
    CHECK(rc == 0 && *status() == 0 && planes_are(10, 20, 30) && test_live_maps() == 0,
          "control frame: rc=%d result=%d live maps=%d", rc, (int)*status(), test_live_maps());
    for (int c = 0; c < 3; c++) {
        fill_planes();
        *status() = SENTINEL;
        test_faults(plane_pa(c), 0);      /* only plane c fails to map */
        rc = send_frame(RESULT);
        unsigned hit = test_map_faults();
        test_faults(0, 0);
        CHECK(hit == 1, "plane %d: injected mmap failure hit %u times (rc=%d)", c, hit, rc);
        CHECK(rc == 0, "plane %d: result write must succeed, rc=%d", c, rc);
        CHECK(*status() == -5, "plane %d: result=%d, want -5", c, (int)*status());
        CHECK(planes_untouched(), "plane %d: planes modified despite map failure", c);
        CHECK(test_live_maps() == 0, "plane %d: %d mappings left behind", c, test_live_maps());
    }
    /* The LUT is still valid: the next clean frame succeeds. */
    *status() = SENTINEL;
    rc = process();
    CHECK(rc == 0 && *status() == 0 && planes_are(10, 20, 30),
          "frame after map failures: rc=%d result=%d", rc, (int)*status());
    close_lut();
}

/* ---- 4b: result aliasing a plane ---------------------------------------- */
static void test_frame_result_alias(void) {
    puts("-- 4b: ProcessRgbToYuv result aliasing a plane");
    test_faults(0, 0);
    fill_lut(10, 20, 30);
    CHECK(init_lut(LUT, RESULT) == 0 && *status() == 0, "init failed");
    for (int c = 0; c < 3; c++) {
        /* start, last 4 bytes, and a result whose 64-byte tail covers the start */
        const int64_t offsets[] = {0, W * H - 4, -32};
        for (unsigned k = 0; k < sizeof offsets / sizeof offsets[0]; k++) {
            uint64_t alias = plane_pa(c) + (uint64_t)offsets[k];
            fill_planes();
            *status() = SENTINEL;
            test_faults(0, 0);
            int rc = send_frame(alias);
            unsigned mapped = test_mappings();
            CHECK(rc == -22 && mapped == 0,
                  "plane %d alias 0x%llx: rc=%d mappings=%u", c, (unsigned long long)alias, rc, mapped);
            CHECK(planes_untouched() && *status() == SENTINEL,
                  "plane %d alias 0x%llx: memory written before rejection", c, (unsigned long long)alias);
        }
        /* Controls: result directly adjacent before/after the plane is disjoint. */
        const uint64_t adjacent[] = {plane_pa(c) - 64, plane_pa(c) + W * H};
        for (unsigned k = 0; k < 2; k++) {
            volatile int32_t *r = test_memory(adjacent[k]);
            *r = SENTINEL;
            fill_planes();
            test_faults(0, 0);
            int rc = send_frame(adjacent[k]);
            CHECK(rc == 0 && *r == 0 && planes_are(10, 20, 30) && test_live_maps() == 0,
                  "plane %d adjacent result 0x%llx: rc=%d result=%d", c,
                  (unsigned long long)adjacent[k], rc, (int)*r);
        }
    }
    close_lut();
}

/* ---- 4c: 1920x1080 global flush failure --------------------------------- */
static void test_frame_global_flush_failure(void) {
    puts("-- 4c: ProcessRgbToYuv 1920x1080 global flush failure");
    test_faults(0, 0);
    fill_lut(10, 20, 30);
    CHECK(init_lut(LUT, RESULT) == 0 && *status() == 0, "init failed");
    /* Control: same frame, no fault. */
    *status() = SENTINEL; test_faults(0, 0);
    int rc = process_big();
    CHECK(rc == 0 && *status() == 0, "control 1080p frame: rc=%d result=%d", rc, (int)*status());
    CHECK(big_planes_are(10, 20, 30), "control 1080p frame not converted");
    CHECK(test_flush_faults() == 0 && test_live_maps() == 0, "control 1080p: unexpected HAL state");

    *status() = SENTINEL;
    test_faults(0, big_plane_pa(0));      /* flush starting at plane 0 fails */
    rc = process_big();
    unsigned hit = test_flush_faults(), size = test_last_fault_size();
    test_faults(0, 0);
    CHECK(hit == 1, "global flush fault hit %u times (rc=%d)", hit, rc);
    CHECK(size == BIG_W * BIG_H, "faulted flush size %u, want whole plane 0 (%u)", size, BIG_W * BIG_H);
    CHECK(rc == 0, "result write must succeed, rc=%d", rc);
    CHECK(*status() == -5, "global flush failure: result=%d, want -5", (int)*status());
    CHECK(test_live_maps() == 0, "%d mappings left after flush failure", test_live_maps());

    *status() = SENTINEL;
    rc = process_big();
    CHECK(rc == 0 && *status() == 0 && big_planes_are(10, 20, 30),
          "1080p frame after flush failure: rc=%d result=%d", rc, (int)*status());
    close_lut();
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    test_lut_map_failure_reports_result();
    test_failed_reinit_drops_old_lut();
    test_clock_failure();
    test_frame_plane_map_failure();
    test_frame_result_alias();
    test_frame_global_flush_failure();
    if (failures) { printf("FAIL cpu_adapter faults: %d of %d checks failed\n", failures, checks); return 1; }
    printf("PASS cpu_adapter faults: %d checks\n", checks);
    return 0;
}
