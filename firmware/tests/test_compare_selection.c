/* Native fault injection for selection/persistence; never opens device files. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define open fixture_open
#define read fixture_read
#define write fixture_write
#define close fixture_close
#define fsync fixture_fsync
#define rename fixture_rename
#define dlopen fixture_dlopen
#define dlsym fixture_dlsym
#define pthread_once fixture_once
#define XREAL_COMPARE_ONEPRO_WARP 1
#include "../onepro_warp.c"
#undef open
#undef read
#undef write
#undef close
#undef fsync
#undef rename
#undef dlopen
#undef dlsym
#undef pthread_once

static int fault, vendor_error, vendor_calls, locks, loads;
static char saved='?', temporary;
static unsigned char vendor_memory[0x200000];
int fixture_open(const char *path, int flags, ...) {
    assert(!strncmp(path,"/usrdata/real3d-algorithm",
                    sizeof("/usrdata/real3d-algorithm")-1));
    return fault==1 ? -1 : (flags ? 21 : 20);
}
long fixture_read(int fd, void *out, unsigned long length) {
    assert(fd==20 && length==2);
    *(char *)out=saved; return 1;
}
long fixture_write(int fd, const void *in, unsigned long length) {
    assert(fd==21 && length==1);
    if(fault==2)return 0;
    temporary=*(const char *)in;return 1;
}
int fixture_close(int fd) { assert(fd==20 || fd==21);return fault==4 ? -1 : 0; }
int fixture_fsync(int fd) { assert(fd==21);return fault==3 ? -1 : 0; }
int fixture_rename(const char *from,const char *to) {
    assert(strstr(from,".tmp") && !strstr(to,".tmp"));
    if(fault==5)return -1;
    saved=temporary;return 0;
}
void *fixture_dlopen(const char *name,int flags) {
    assert(strstr(name,"libxreal_onepro_mesh.so") && flags==2);
    ++loads;
    static const unsigned char prologue[]={0xfd,0x7b,0xb1,0xa9,0x41,0xd0,0x3b,0xd5,
                                           0xfd,0x03,0x00,0x91,0xf9,0x23,0x00,0xf9};
    memcpy(vendor_memory+0x38150,prologue,sizeof(prologue));
    return vendor_memory;
}
void *fixture_dlsym(void *handle,const char *name) {
    assert(handle==vendor_memory && !strcmp(name,"NRPluginLoad"));
    return vendor_memory+0x1d9d0;
}
int fixture_once(int *once,void (*fn)(void)) {
    if(!*once){*once=1;fn();}return 0;
}
int xreal_frame_lock(void) { assert(!locks);++locks;return 0; }
void xreal_frame_unlock(void) { assert(locks==1);--locks; }
void xreal_parallel_run(void *(*fn)(void *),void *jobs,unsigned long stride,unsigned count) {
    (void)fn;(void)jobs;(void)stride;(void)count;assert(0);
}
static int vendor_set(int value) {
    assert(value==2 && locks==0);++vendor_calls;return vendor_error;
}
int main(void) {
    int choice;
    assert(xreal_compare_get(&choice)==0 && choice==2); /* invalid saved byte */
    assert(xreal_compare_set(0,vendor_set)==-22 && !vendor_calls);
    assert(xreal_compare_set(3,vendor_set)==-22 && !vendor_calls);
    assert(xreal_compare_set(1,0)==-22 && !vendor_calls);
    assert(xreal_compare_set(1,vendor_set)==0 && saved=='1' && loads==1);
    assert(xreal_compare_get(&choice)==0 && choice==1);
    vendor_error=-9;
    assert(xreal_compare_set(2,vendor_set)==-9 && saved=='1');
    vendor_error=0;
    for(fault=1;fault<=5;++fault) {
        assert(xreal_compare_set(2,vendor_set)==-5 && saved=='1');
        assert(xreal_compare_algorithm()==1 && !locks);
    }
    fault=0;
    assert(xreal_compare_set(2,vendor_set)==0 && saved=='2');
    assert(xreal_compare_get(&choice)==0 && choice==2 && !locks);
    assert(xreal_compare_get(0)==-22);
    puts("Selection: default, two algorithms, shared Natural setting, loader once, "
         "invalid choices and five persistence failures passed.");
}
