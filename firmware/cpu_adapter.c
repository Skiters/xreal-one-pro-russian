/* Experimental replacement for the LUT and warp DSP RPC ABI of Pilot 2.0.
 * No DSP power, clocks or SRAM changes. Unsupported RPCs fail explicitly.
 */
#include "runtime.h"
typedef unsigned long u64;
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;
extern void *malloc(u64);
extern void free(void *);
extern int strcmp(const char *,const char *);
extern void *ar_hal_sys_mmap_cache(u64,u32);
extern int ar_hal_sys_munmap(void *,u32);
extern int ar_hal_sys_mmz_invalid_cache(u64,void *,u32);
extern int ar_hal_sys_mmz_flush_cache(u64,void *,u32);
extern int ar_hal_sys_mmz_flush_cache_pa(u64,u32);
struct timespec {long sec,nsec;};
extern int clock_gettime(int,struct timespec *);
extern int open(const char *,int,...);
extern long write(int,const void *,u64);
extern int close(int);
extern int snprintf(char *,u64,const char *,...);
static u64 *dense;
static u32 frame_count;
extern u64 xreal_rgb_table_bytes(void);
extern int xreal_build_rgb_table_range(const u16 *,u64 *,u32,u32);
extern int xreal_full_rgb_lut(const u64 *,u8 *,u8 *,u8 *,u32,u32,u32,u32,u32);
static void trace(const char *stage,u64 p,u32 n,long value) {
    char line[200];
    int count=snprintf(line,sizeof(line),"CPU diagnostic: %s phys=0x%lx size=%u value=%ld\n",stage,p,n,value);
    if(count<=0 || count>=(int)sizeof(line))return;
    write(2,line,(u64)count);
    int fd=open("/usrdata/aihdr-cpu.log",1|64|1024,0600);
    if(fd>=0){write(fd,line,(u64)count);close(fd);}
}
static void record(const char *event,int code) {
    char line[160];int n=snprintf(line,sizeof(line),"CPU v7: %s rc=%d frames=%u\n",event,code,frame_count);
    if(n<=0 || n>=(int)sizeof(line))return;
    int fd=open("/usrdata/aihdr-cpu.log",1|64|1024,0600);
    if(fd>=0){write(fd,line,(u64)n);close(fd);}
}
struct rpc {char name[32]; u64 reserved; const void *args; u32 length;};
extern int xreal_real3d_rpc(u32,const struct rpc *,u32);
struct init_args {u64 lut,result; u32 a,b;};
struct frame_args {u64 planes[3],result; u32 strides[3],width,height;};
_Static_assert(__builtin_offsetof(struct rpc,args)==40,"RPC ABI");
_Static_assert(__builtin_offsetof(struct frame_args,width)==44,"frame ABI");
static int valid(u64 p,u64 n) {return n && p>=0x33000000 && p<0x40000000 && n<=0x40000000-p;}
static void *map(u64 p,u32 n) {
    if(!valid(p,n)){trace("invalid physical range",p,n,0);return (void *)0;}
    void *v=ar_hal_sys_mmap_cache(p,n);
    if(!v || v==(void *)-1){trace("mmap_cache failed",p,n,(long)v);return (void *)0;}
    int rc=ar_hal_sys_mmz_invalid_cache(p,v,n);
    /* This vendor build returns 0x80028003 when the invalidate ioctl
     * succeeds (confirmed in libhal_sys.so and on the physical device). */
    if(rc!=0 && (u32)rc!=0x80028003u){trace("invalid_cache failed",p,n,rc);ar_hal_sys_munmap(v,n);return (void *)0;}
    return v;
}
static int result(u64 p,int value) {
    int *v=map(p,64);if(!v){trace("result map failed",p,64,value);return -5;}
    *v=value;int rc=ar_hal_sys_mmz_flush_cache_pa(p,64);
    if(rc)trace("result PA flush failed",p,64,rc);
    ar_hal_sys_munmap(v,64);return rc ? -5 : 0;
}
struct build_job {const u16 *cube;u64 *table;u32 begin,end;int rc;};
static void *build_worker(void *arg) {
    struct build_job *j=arg;
    j->rc=xreal_build_rgb_table_range(j->cube,j->table,j->begin,j->end);
    return (void *)0;
}
struct rgb_job {const u64 *table;u8 *planes[3];u32 strides[3],width,height;int rc;};
static void *rgb_worker(void *arg) {
    struct rgb_job *j=arg;
    j->rc=xreal_full_rgb_lut(j->table,j->planes[0],j->planes[1],j->planes[2],
        j->strides[0],j->strides[1],j->strides[2],j->width,j->height);
    return (void *)0;
}
static int rgb_parallel(const u64 *table,u8 *planes[3],const u32 strides[3],u32 w,u32 h) {
    struct rgb_job jobs[4];u32 count=h<4?1:4;
    for(u32 i=0;i<count;i++) {
        u32 begin=h*i/count,end=h*(i+1)/count;
        jobs[i].table=table;jobs[i].width=w;jobs[i].height=end-begin;jobs[i].rc=0;
        for(u32 c=0;c<3;c++) {
            jobs[i].planes[c]=planes[c]+(u64)begin*strides[c];
            jobs[i].strides[c]=strides[c];
        }
    }
    xreal_parallel_run(rgb_worker,jobs,sizeof(jobs[0]),count);
    for(u32 i=0;i<count;i++)if(jobs[i].rc)return jobs[i].rc;
    return 0;
}

static int dispatch_rpc(u32 core,const struct rpc *call,u32 sync) {
    if(core || !call || sync!=1 || call->name[31] || !call->args)return -22;
    if(!strcmp(call->name,"ArArmlut3dInit")) {
        if(call->length!=24){trace("init length",0,call->length,0);return -22;}
        const struct init_args *a=call->args;
        trace("init LUT address",a->lut,14739,(long)a->result);
        /* Validate the result before allocating or changing current state. */
        if(!valid(a->result,64)||!valid(a->lut,14739)||
           (a->result<a->lut+14739 && a->lut<a->result+64))return -22;
        u8 *p=map(a->lut,14739);if(!p){trace("LUT map failed",a->lut,14739,0);return -5;}
        /* DSP ABI carries three 17^3 channel planes, not RGB triplets. */
        u16 cube[14739];
        for(u32 i=0;i<4913;i++)for(u32 c=0;c<3;c++)
            cube[i*3+c]=(u16)p[c*4913+i]*256;
        ar_hal_sys_munmap(p,14739);
        free(dense);dense=(void *)0;
        u64 *next=malloc(xreal_rgb_table_bytes());
        if(!next){record("LUT allocation failed",-12);return result(a->result,-12);}
        struct build_job jobs[4];
        for(u32 i=0;i<4;i++)jobs[i]=(struct build_job){cube,next,17*i/4,17*(i+1)/4,0};
        xreal_parallel_run(build_worker,jobs,sizeof(jobs[0]),4);
        int rc=0;
        for(u32 i=0;i<4;i++)if(jobs[i].rc)rc=jobs[i].rc;
        if(rc)free(next);else dense=next;
        frame_count=0;record("LUT initialized",rc);
        return result(a->result,rc);
    }
    if(!strcmp(call->name,"ArArmlut3dClose")) {
        if(call->length!=8)return -22;
        u64 p=*(const u64 *)call->args;
        if(!valid(p,64))return -22;
        record("LUT closing",0);free(dense);dense=(void *)0;return result(p,0);
    }
    if(strcmp(call->name,"ArArmlut3dProcessRgbToYuv"))return xreal_real3d_rpc(core,call,sync);
    if(call->length!=52 || !dense)return -22;
    const struct frame_args *a=call->args;
    if(!a->width || !a->height || a->width>1920 || a->height>1080 ||
       (a->width&1)||(a->height&1)||(a->strides[0]&1)||!valid(a->result,64))return -22;
    u32 sizes[3];u8 *planes[3]={(void *)0,(void *)0,(void *)0};int rc=-22;
    for(u32 i=0;i<3;i++) {
        if(a->strides[i]<a->width||a->strides[i]>4096)return -22;
        sizes[i]=a->strides[i]*a->height;
        if(!valid(a->planes[i],sizes[i]))return -22;
        if(a->result<a->planes[i]+sizes[i] && a->planes[i]<a->result+64)return -22;
        for(u32 j=0;j<i;j++) if(a->planes[i]<a->planes[j]+sizes[j] && a->planes[j]<a->planes[i]+sizes[i])return -22;
    }
    for(u32 i=0;i<3;i++){planes[i]=map(a->planes[i],sizes[i]);if(!planes[i]){rc=-5;goto done;}}
    struct timespec before,converted,flushed;
    clock_gettime(1,&before);
    rc=rgb_parallel(dense,planes,a->strides,a->width,a->height);
    clock_gettime(1,&converted);
    if(!rc && sizes[0]>0x100000) {
        /* Vendor HAL uses a global cache-flush ioctl for sizes above 1 MiB.
         * All three RGB planes have been modified before this call. */
        int flush=ar_hal_sys_mmz_flush_cache_pa(a->planes[0],sizes[0]);
        if(flush){trace("frame global flush failed",a->planes[0],sizes[0],flush);rc=-5;}
    } else if(!rc) for(u32 i=0;i<3;i++) {
        for(u32 offset=0;offset<sizes[i];offset+=0x100000) {
            u32 length=sizes[i]-offset;
            if(length>0x100000)length=0x100000;
            int flush=ar_hal_sys_mmz_flush_cache_pa(a->planes[i]+offset,length);
            if(flush){trace("frame PA flush failed",a->planes[i]+offset,length,flush);rc=-5;break;}
        }
        if(rc)break;
    }
    clock_gettime(1,&flushed);
    if(frame_count%
#ifdef XREAL_UI_FAST
       1800
#else
       30
#endif
       ==0) {
        long convert_us=(converted.sec-before.sec)*1000000+(converted.nsec-before.nsec)/1000;
        long flush_us=(flushed.sec-converted.sec)*1000000+(flushed.nsec-converted.nsec)/1000;
        trace("convert microseconds",a->planes[0],a->width,convert_us);
        trace("flush microseconds",a->planes[0],a->width,flush_us);
        trace("frame timestamp microseconds",0,frame_count,
              flushed.sec*1000000+flushed.nsec/1000);
    }
done:
    for(u32 i=0;i<3;i++)if(planes[i])ar_hal_sys_munmap(planes[i],sizes[i]);
    if(!rc)frame_count++;
    if(rc || frame_count==1)record("frame processed",rc);
    return result(a->result,rc);
}

/* Private renamed imports: these do not affect other plugins/system libraries. */
int xreal_cpu_no_dsp(void){return 0;}

int xreal_cpu_rpc(u32 core,const struct rpc *call,u32 sync) {
    int rc=xreal_frame_lock();
    if(rc)return rc;
    rc=dispatch_rpc(core,call,sync);
    xreal_frame_unlock();
    return rc;
}
