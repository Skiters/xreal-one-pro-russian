/* Native Linux test only. Never linked into the target adapter. */
#include <stddef.h>
#include <stdint.h>
static unsigned char ram[32*1024*1024];
static uint64_t fail_map, fail_flush;
static unsigned mappings;
/* Bookkeeping only (no cache/ioctl model): how many injected faults were hit,
 * the size passed to the last faulted flush, and mappings not yet unmapped. */
static unsigned map_faults, flush_faults, last_fault_size;
static int live_maps;
void test_faults(uint64_t mapping, uint64_t flushing) {
    fail_map=mapping; fail_flush=flushing; mappings=0;
    map_faults=0; flush_faults=0; last_fault_size=0;
}
unsigned test_mappings(void) { return mappings; }
unsigned test_map_faults(void) { return map_faults; }
unsigned test_flush_faults(void) { return flush_faults; }
unsigned test_last_fault_size(void) { return last_fault_size; }
int test_live_maps(void) { return live_maps; }
void *test_memory(uint64_t pa) {
    if(pa<0x33000000 || pa>=0x35000000) return NULL;
    return ram+(pa-0x33000000);
}
void *ar_hal_sys_mmap_cache(uint64_t pa,unsigned size) {
    ++mappings;
    if(pa==fail_map){++map_faults;return NULL;}
    if(pa<0x33000000 || pa>=0x35000000 ||
       size>0x35000000-pa) return NULL;
    ++live_maps;
    return test_memory(pa);
}
int ar_hal_sys_munmap(void *ptr,unsigned size) { (void)size;if(ptr)--live_maps;return 0; }
int ar_hal_sys_mmz_invalid_cache(uint64_t pa,void *ptr,unsigned size) {
    (void)pa;(void)ptr;(void)size;return 0;
}
int ar_hal_sys_mmz_flush_cache_pa(uint64_t pa,unsigned size) {
    if(pa==fail_flush){++flush_faults;last_fault_size=size;return -5;}
    return 0;
}
