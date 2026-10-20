#ifndef XREAL_CPU_RUNTIME_H
#define XREAL_CPU_RUNTIME_H
/* Linux/AArch64 ABI; the installer pins the target glibc/pthread hashes.
 * libc's opaque sem/mutex storage is overallocated and 16-byte aligned.
 * No pthread structure fields are accessed by this adapter. */
int xreal_frame_lock(void);
void xreal_frame_unlock(void);
void xreal_parallel_run(void *(*worker)(void *), void *jobs,
                        unsigned long stride, unsigned count);
unsigned xreal_pool_workers(void);
#endif
