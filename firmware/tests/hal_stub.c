/* Native Linux test only. Never linked into the target adapter. */
#include <stddef.h>
#include <stdint.h>
static unsigned char ram[32*1024*1024];
static uint64_t fail_map, fail_flush;
static unsigned mappings;
void test_faults(uint64_t mapping, uint64_t flushing) {
    fail_map=mapping; fail_flush=flushing; mappings=0;
}
unsigned test_mappings(void) { return mappings; }
void *test_memory(uint64_t pa) {
    if(pa<0x33000000 || pa>=0x35000000) return NULL;
    return ram+(pa-0x33000000);
}
void *ar_hal_sys_mmap_cache(uint64_t pa,unsigned size) {
    ++mappings;
    if(pa==fail_map || pa<0x33000000 || pa>=0x35000000 ||
       size>0x35000000-pa) return NULL;
    return test_memory(pa);
}
int ar_hal_sys_munmap(void *ptr,unsigned size) { (void)ptr;(void)size;return 0; }
int ar_hal_sys_mmz_invalid_cache(uint64_t pa,void *ptr,unsigned size) {
    (void)pa;(void)ptr;(void)size;return 0;
}
int ar_hal_sys_mmz_flush_cache_pa(uint64_t pa,unsigned size) {
    (void)size;return pa==fail_flush ? -5 : 0;
}
