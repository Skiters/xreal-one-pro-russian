#include "runtime.h"
typedef unsigned long thread_t;
typedef struct { _Alignas(16) unsigned char bytes[64]; } opaque;
extern int pthread_once(int *, void (*)(void));
extern int pthread_mutex_init(void *, const void *);
extern int pthread_mutex_lock(void *);
extern int pthread_mutex_unlock(void *);
extern int pthread_create(thread_t *, const void *, void *(*)(void *), void *);
extern int pthread_detach(thread_t);
extern int sem_init(void *, int, unsigned);
extern int sem_destroy(void *);
extern int sem_wait(void *);
extern int sem_post(void *);
extern int *__errno_location(void);
extern void abort(void);
static int once, ready;
static opaque frame_mutex;
struct slot {
    opaque start, done;
    void *(*fn)(void *);
    void *arg;
    int live;
};
static struct slot slots[3];
static unsigned workers;

static void wait_for(opaque *sem) {
    /* semaphores live until process exit; retry interrupted waits. Never
     * let the caller release an MMZ buffer before a worker has finished. */
    while (sem_wait(sem))
        if (*__errno_location() != 4) abort(); /* EINTR only; never spin on corruption. */
}
static void *run_slot(void *arg) {
    struct slot *s = arg;
    for (;;) {
        wait_for(&s->start);
        s->fn(s->arg);
        if (sem_post(&s->done)) abort();
    }
    return (void *)0;
}
static void initialize(void) {
    if (pthread_mutex_init(&frame_mutex, (void *)0)) return;
    ready = 1;
    for (unsigned i = 0; i < 3; ++i) {
        struct slot *s = &slots[i];
        if (sem_init(&s->start, 0, 0)) continue;
        if (sem_init(&s->done, 0, 0)) { sem_destroy(&s->start); continue; }
        thread_t thread;
        if (pthread_create(&thread, (void *)0, run_slot, s)) {
            sem_destroy(&s->start); sem_destroy(&s->done); continue;
        }
        pthread_detach(thread);
        s->live = 1;
        ++workers;
    }
}
int xreal_frame_lock(void) {
    if (pthread_once(&once, initialize) || !ready) return -5;
    return pthread_mutex_lock(&frame_mutex) ? -5 : 0;
}
void xreal_frame_unlock(void) { pthread_mutex_unlock(&frame_mutex); }
unsigned xreal_pool_workers(void) { return workers; }
void xreal_parallel_run(void *(*worker)(void *), void *jobs,
                        unsigned long stride, unsigned count) {
    /* The RPC frame mutex serializes calls and LUT init/close. Jobs and all
     * mapped planes stay owned by the caller until every completion arrives. */
    unsigned started = 0;
    for (unsigned i = 0; i < count; ++i) {
        void *job = (unsigned char *)jobs + stride * i;
        if (i < 3 && slots[i].live) {
            slots[i].fn = worker; slots[i].arg = job;
            if (!sem_post(&slots[i].start)) { started |= 1U << i; continue; }
        }
        worker(job);  /* Resource failure degrades to synchronous execution. */
    }
    for (unsigned i = 0; i < 3; ++i)
        if (started & (1U << i)) wait_for(&slots[i].done);
}
