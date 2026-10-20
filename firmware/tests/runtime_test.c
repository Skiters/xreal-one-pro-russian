#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include "../runtime.h"
static atomic_int active, frames;
struct job {unsigned input, output;};
static void *work(void *arg) {
    struct job *j=arg;
    j->output=j->input*j->input;
    return 0;
}
static void *client(void *arg) {
    (void)arg;
    for(unsigned n=0;n<1000;n++) {
        assert(!xreal_frame_lock());
        assert(atomic_fetch_add(&active,1)==0);
        struct job jobs[4];
        for(unsigned i=0;i<4;i++) jobs[i]=(struct job){n+i,0};
        xreal_parallel_run(work,jobs,sizeof(jobs[0]),4);
        for(unsigned i=0;i<4;i++) assert(jobs[i].output==(n+i)*(n+i));
        assert(atomic_fetch_sub(&active,1)==1);
        atomic_fetch_add(&frames,1);
        xreal_frame_unlock();
    }
    return 0;
}
int main(void) {
    pthread_t clients[5];
    for(unsigned i=0;i<5;i++) assert(!pthread_create(&clients[i],0,client,0));
    for(unsigned i=0;i<5;i++) assert(!pthread_join(clients[i],0));
    assert(frames==5000);
    assert(xreal_pool_workers()==3);
    puts("PASS persistent pool: 5000 frames, 5 concurrent callers, 3 reusable workers");
}
