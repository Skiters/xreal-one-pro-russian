/* Experimental replacement for the LUT and warp DSP RPC ABI of Pilot 2.0.
 * No DSP power, clocks or SRAM changes. Unsupported RPCs fail explicitly.
 */
#include "runtime.h"
#if defined(__aarch64__) && !defined(XREAL_NO_NEON)
#include <arm_neon.h>
#endif
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
extern int nanosleep(const struct timespec *,struct timespec *);
/* perf9 adaptive pacing. While a frame (map + convert + flush) fits a 60 Hz
 * source, AI HDR runs every frame. When it does not, Pilot skips source
 * frames at irregular points (judder); then each call returns no earlier
 * than PACE_US after the previous one began, locking onto every second
 * source frame: an even 30 frames/s. EMA of the busy time with hysteresis. */
#define PACE_US 32800L
/* perf13: pacing DISABLED (threshold unreachable). On the device even 30/s
 * made film look stalled: what keeps film motion smooth is how often a fresh
 * source frame is taken (Movie at a 90 Hz source, ~15 ms, looks smooth), not
 * an even cadence. AI HDR now uses the 90 Hz source like Movie, unpaced. */
#define PACE_ON_US 100000000L
#define PACE_OFF_US 13800L
static long pace_ema_us, pace_last_start_us;
static int pacing;
static long now_us(void){struct timespec t;clock_gettime(1,&t);return t.sec*1000000L+t.nsec/1000;}
extern int open(const char *,int,...);
extern long write(int,const void *,u64);
extern int close(int);
extern long read(int,void *,u64);
extern int snprintf(char *,u64,const char *,...);
static u64 *dense;
static u32 frame_count;
extern u64 xreal_rgb_table_bytes(void);
extern int xreal_build_rgb_table_range(const u16 *,u64 *,u32,u32);
extern int xreal_full_rgb_lut(const u64 *,u8 *,u8 *,u8 *,u32,u32,u32,u32,u32);
extern u64 xreal_compact_table_bytes(void);
extern int xreal_build_compact_table_range(const u16 *,u32 *,u32,u32);
extern int xreal_compact_rgb_lut(const u32 *,u8 *,u8 *,u8 *,u32,u32,u32,u32,u32);
extern int xreal_compact16_rgb_lut(const u32 *,u8 *,u8 *,u8 *,u32,u32,u32,u32,u32);
extern int xreal_blocks_rgb_lut(const u32 *,u8 *,u8 *,u8 *,u32,u32,u32,u32,u32);
extern void xreal_blocks_take_counts(unsigned long long *,unsigned long long *);
/* Kernels: 0 dense (rc7 table, only when the compact table cannot be built),
 * 1 compact, 2 compact16 (same table, 16-bit NEON arithmetic, within one
 * level of the cube), 3 blocks (compact16 plus smooth 2x2 block reuse, within
 * two levels; aims at < 16.7 ms so a 60 Hz source is never skipped). perf4 on video: compact 23.4 ms beat compact+prefetch
 * 33.7 and tetrahedral 33.4; those stay in lut_fast.c but are not used.
 * After LUT init, kernels 1 and 2 alternate for TUNE frames each and the
 * lower median wins (short, so the first second stays smooth). */
#define TUNE 10
static u32 *compact;
static int choice=-1;
static u32 tune[4][TUNE], tuned[4];
static u32 median(u32 *v,u32 n) {
    for(u32 i=1;i<n;i++)for(u32 j=i;j && v[j-1]>v[j];j--){u32 x=v[j];v[j]=v[j-1];v[j-1]=x;}
    return v[n/2];
}
static void trace(const char *stage,u64 p,u32 n,long value) {
    char line[200];
    int count=snprintf(line,sizeof(line),"CPU diagnostic: %s phys=0x%lx size=%u value=%ld\n",stage,p,n,value);
    if(count<=0 || count>=(int)sizeof(line))return;
    write(2,line,(u64)count);
    int fd=open("/usrdata/aihdr-cpu.log",1|64|1024,0600);
    if(fd>=0){write(fd,line,(u64)count);close(fd);}
}
/* Reads a small decimal sysfs value; -1 when unavailable. Diagnostics only. */
static long sysfs_number(const char *path) {
    char text[32];int fd=open(path,0);
    if(fd<0)return -1;
    long n=read(fd,text,sizeof(text)-1);close(fd);
    if(n<=0)return -1;
    long v=0;for(long i=0;i<n && text[i]>='0' && text[i]<='9';i++)v=v*10+(text[i]-'0');
    return v;
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
struct compact_job {const u16 *cube;u32 *table;u32 begin,end;int rc;};
static void *compact_worker(void *arg) {
    struct compact_job *j=arg;
    j->rc=xreal_build_compact_table_range(j->cube,j->table,j->begin,j->end);
    return (void *)0;
}
/* Dynamic row scheduling: the frame is cut into CHUNK-row bands and each of
 * the four workers takes the next free band. Pilot's own threads share the
 * four A53 cores, so fixed quarters made every frame wait for the most
 * interrupted core; now the idle cores finish its remaining bands. */
#define CHUNK 24
struct rgb_frame {const u64 *table;const u32 *compact;int kernel;u8 *planes[3];u32 strides[3],width,height,chunks,next;int rc;};
struct rgb_job {struct rgb_frame *f;int rc;};
static int rgb_rows(const struct rgb_frame *f,u32 begin,u32 rows) {
    u8 *p[3];
    for(u32 c=0;c<3;c++)p[c]=f->planes[c]+(u64)begin*f->strides[c];
    if(f->kernel==3)
        return xreal_blocks_rgb_lut(f->compact,p[0],p[1],p[2],f->strides[0],f->strides[1],f->strides[2],f->width,rows);
    if(f->kernel==2)
        return xreal_compact16_rgb_lut(f->compact,p[0],p[1],p[2],f->strides[0],f->strides[1],f->strides[2],f->width,rows);
    if(f->kernel==1)
        return xreal_compact_rgb_lut(f->compact,p[0],p[1],p[2],f->strides[0],f->strides[1],f->strides[2],f->width,rows);
    return xreal_full_rgb_lut(f->table,p[0],p[1],p[2],f->strides[0],f->strides[1],f->strides[2],f->width,rows);
}
static void *rgb_worker(void *arg) {
    struct rgb_job *j=arg;struct rgb_frame *f=j->f;
    j->rc=0;
    for(;;) {
        u32 chunk=__atomic_fetch_add(&f->next,1,__ATOMIC_RELAXED);
        if(chunk>=f->chunks)break;
        u32 begin=chunk*CHUNK,rows=f->height-begin<CHUNK ? f->height-begin : CHUNK;
        int rc=rgb_rows(f,begin,rows);
        if(rc && !j->rc)j->rc=rc;
    }
    return (void *)0;
}
static int rgb_parallel(const u64 *table,const u32 *small,int kernel,u8 *planes[3],const u32 strides[3],u32 w,u32 h) {
    struct rgb_frame f={table,small,kernel,{planes[0],planes[1],planes[2]},
                        {strides[0],strides[1],strides[2]},w,h,(h+CHUNK-1)/CHUNK,0,0};
    struct rgb_job jobs[4];
    for(u32 i=0;i<4;i++)jobs[i]=(struct rgb_job){&f,0};
    xreal_parallel_run(rgb_worker,jobs,sizeof(jobs[0]),4);
    for(u32 i=0;i<4;i++)if(jobs[i].rc)return jobs[i].rc;
    return 0;
}

/* perf12 repeated-frame reuse with tolerance. A 24 fps film in a 60 Hz
 * signal repeats frames 3:2, but macOS temporally dithers 8-bit external
 * outputs, so repeats differ by +-1 in the low bit (perf11's exact hashes
 * found 0 repeats on a film). A frame counts as a repeat when EVERY pixel of
 * every plane is within DD_TOL of the previous input; motion anywhere (even a
 * subtitle) exceeds it and is converted normally. A repeat gets the kept
 * output of the previous frame (differs from a fresh conversion by at most
 * the cube slope times DD_TOL, ~2-3 levels). The compare stops at the first
 * difference. Copies are kept only while repeats occur (>= 10% of 120). */
#define DD_TOL 2
/* perf13: repeat reuse DISABLED. With 78% repeats on a film the 6 MB input
 * and output copies still cost more than they saved on the A53 memory bus
 * (26-45 ms per frame instead of ~18). Code kept for reference and tests. */
#define DD_ENABLE 0
struct dd_job {u8 *planes[3]; u8 *in[3]; u8 *out[3]; u32 strides[3], w, y0, y1; int mode, differ;};
static u8 *dd_in[3], *dd_out[3]; static u32 dd_w, dd_h; static int dd_prev_valid, dd_have_copy, dd_keep=1;
static u32 dd_window, dd_window_repeats, dd_repeats, dd_served, dd_hist[6];
static long dd_last_call_us;
static int dd_row_differs(const u8 *a,const u8 *b,u32 w) {
    u32 x=0;
#if defined(__aarch64__) && !defined(XREAL_NO_NEON)
    for(;x+16<=w;x+=16) {
        uint8x16_t d=vabdq_u8(vld1q_u8(a+x),vld1q_u8(b+x));
        if(vmaxvq_u8(d)>DD_TOL)return 1;
    }
#endif
    for(;x<w;x++){int d=(int)a[x]-(int)b[x];if(d>DD_TOL||d<-DD_TOL)return 1;}
    return 0;
}
static void *dd_worker(void *arg) {
    struct dd_job *j=arg;
    for(u32 c=0;c<3;c++)for(u32 y=j->y0;y<j->y1;y++) {
        u8 *row=j->planes[c]+(u64)y*j->strides[c];
        u64 off=(u64)y*j->w;
        if(j->mode==0){if(dd_row_differs(row,j->in[c]+off,j->w)){j->differ=1;return (void *)0;}}
        else if(j->mode==1) __builtin_memcpy(row,j->out[c]+off,j->w);   /* kept output -> frame */
        else if(j->mode==2) __builtin_memcpy(j->out[c]+off,row,j->w);   /* frame -> kept output */
        else __builtin_memcpy(j->in[c]+off,row,j->w);                   /* frame -> kept input */
    }
    return (void *)0;
}
static int dd_run(u8 *planes[3],const u32 strides[3],u32 w,u32 h,int mode) {
    struct dd_job jobs[4];
    for(u32 i=0;i<4;i++)
        jobs[i]=(struct dd_job){{planes[0],planes[1],planes[2]},{dd_in[0],dd_in[1],dd_in[2]},{dd_out[0],dd_out[1],dd_out[2]},
                                {strides[0],strides[1],strides[2]},w,h*i/4,h*(i+1)/4,mode,0};
    xreal_parallel_run(dd_worker,jobs,sizeof(jobs[0]),4);
    int differ=0;for(u32 i=0;i<4;i++)differ|=jobs[i].differ;
    return differ;
}
static void dd_reset(void) {
    for(u32 c=0;c<3;c++){free(dd_in[c]);free(dd_out[c]);dd_in[c]=dd_out[c]=(u8 *)0;}
    dd_w=dd_h=0;dd_prev_valid=dd_have_copy=0;dd_keep=1;dd_window=dd_window_repeats=0;dd_last_call_us=0;
}
static int dd_prepare(u32 w,u32 h) {
    if(w==dd_w && h==dd_h && dd_in[0])return 0;
    dd_reset();
    for(u32 c=0;c<3;c++)if(!(dd_in[c]=malloc((u64)w*h)) || !(dd_out[c]=malloc((u64)w*h))){dd_reset();return -12;}
    dd_w=w;dd_h=h;return 0;
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
        {   /* Identifies which cube Pilot loaded (e.g. a softened AI HDR table). */
            u32 h=2166136261u;
            for(u32 i=0;i<14739;i++)h=(h^p[i])*16777619u;
            trace("LUT fnv1a32",a->lut,14739,(long)h);
        }
        ar_hal_sys_munmap(p,14739);
        free(dense);dense=(void *)0;
        free(compact);compact=(void *)0;
        choice=-1;tuned[2]=tuned[3]=0;pacing=0;pace_ema_us=0;pace_last_start_us=0;dd_reset();
        int rc=0;
        u32 *small=malloc(xreal_compact_table_bytes());
        int crc=small ? 0 : -12;
        if(small) {
            struct compact_job cjobs[4];
            for(u32 i=0;i<4;i++)cjobs[i]=(struct compact_job){cube,small,17*i/4,17*(i+1)/4,0};
            xreal_parallel_run(compact_worker,cjobs,sizeof(cjobs[0]),4);
            for(u32 i=0;i<4;i++)if(cjobs[i].rc)crc=cjobs[i].rc;
        }
        if(!crc)compact=small;
        else {
            /* Compact table unavailable: rc7 behaviour with the dense table. */
            free(small);record("perf: compact table unavailable, dense only",crc);
            u64 *next=malloc(xreal_rgb_table_bytes());
            if(!next){record("LUT allocation failed",-12);return result(a->result,-12);}
            struct build_job jobs[4];
            for(u32 i=0;i<4;i++)jobs[i]=(struct build_job){cube,next,17*i/4,17*(i+1)/4,0};
            xreal_parallel_run(build_worker,jobs,sizeof(jobs[0]),4);
            for(u32 i=0;i<4;i++)if(jobs[i].rc)rc=jobs[i].rc;
            if(rc)free(next);else{dense=next;choice=0;}
        }
        frame_count=0;record("LUT initialized",rc);
        return result(a->result,rc);
    }
    if(!strcmp(call->name,"ArArmlut3dClose")) {
        if(call->length!=8)return -22;
        u64 p=*(const u64 *)call->args;
        if(!valid(p,64))return -22;
        record("LUT closing",0);free(dense);dense=(void *)0;
        free(compact);compact=(void *)0;dd_reset();
        choice=-1;return result(p,0);
    }
    if(strcmp(call->name,"ArArmlut3dProcessRgbToYuv"))return xreal_real3d_rpc(core,call,sync);
    if(call->length!=52 || (choice==0 ? !dense : !compact))return -22;
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
    long call_start_us=now_us();
    for(u32 i=0;i<3;i++){planes[i]=map(a->planes[i],sizes[i]);if(!planes[i]){rc=-5;goto done;}}
    struct timespec before,converted,flushed;
    /* perf10: compact16 only. blocks (v1 39 ms, v2 23.2 ms vs 13.1 ms) lost
     * on the device both times; it stays in lut_fast.c with its tests. */
    if(choice<0)choice=2;
    int kernel=choice;
    clock_gettime(1,&before);
    {   long gap=dd_last_call_us ? call_start_us-dd_last_call_us : -1;
        dd_last_call_us=call_start_us;
        if(gap>=0)dd_hist[gap<14000?0:gap<20000?1:gap<30000?2:gap<36000?3:gap<50000?4:5]++;
    }
    int served=0;
    int dd_ok=DD_ENABLE && !dd_prepare(a->width,a->height);
    if(dd_ok) {
        int same=dd_prev_valid && !dd_run(planes,a->strides,a->width,a->height,0);
        if(same){dd_repeats++;dd_window_repeats++;}
        if(same && dd_have_copy){dd_run(planes,a->strides,a->width,a->height,1);served=1;dd_served++;rc=0;}
        if(++dd_window>=120){dd_keep=dd_window_repeats*10>=dd_window;dd_window=dd_window_repeats=0;if(!dd_keep)dd_have_copy=0;}
    }
    if(!served) {
        /* Keep this input as the reference before converting it in place. */
        if(dd_ok){dd_run(planes,a->strides,a->width,a->height,3);dd_prev_valid=1;}
        rc=rgb_parallel(dense,compact,kernel,planes,a->strides,a->width,a->height);
        if(!rc && dd_ok && dd_keep){dd_run(planes,a->strides,a->width,a->height,2);dd_have_copy=1;}
        else{dd_have_copy=0;if(rc)dd_prev_valid=0;}
    }
    clock_gettime(1,&converted);
    if(!rc && choice<0) {
        long us=(converted.sec-before.sec)*1000000+(converted.nsec-before.nsec)/1000;
        if(tuned[kernel]<TUNE)tune[kernel][tuned[kernel]++]=(u32)(us>0 ? us : 0);
        if(tuned[2]>=TUNE && tuned[3]>=TUNE) {
            u32 m2=median(tune[2],TUNE),m3=median(tune[3],TUNE);
            choice=m3<=m2 ? 3 : 2;
            char line[200];
            int n=snprintf(line,sizeof(line),"CPU perf7: autotune compact16_median_us=%u blocks_median_us=%u choice=%s\n",
                           m2,m3,choice==3 ? "blocks" : "compact16");
            if(n>0 && n<(int)sizeof(line)) {
                int fd=open("/usrdata/aihdr-cpu.log",1|64|1024,0600);
                if(fd>=0){write(fd,line,(u64)n);close(fd);}
            }
        }
    }
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
        if(frame_count%300==0 && frame_count) {
            trace("perf11 repeats per 300",0,frame_count,(long)dd_repeats);
            trace("perf11 served from copy per 300",0,frame_count,(long)dd_served);
            trace("perf11 copies kept",0,frame_count,(long)dd_keep);
            char line[200];
            int n=snprintf(line,sizeof(line),"CPU perf11 call gaps <14:%u 14-20:%u 20-30:%u 30-36:%u 36-50:%u >50:%u frames=%u\n",
                           dd_hist[0],dd_hist[1],dd_hist[2],dd_hist[3],dd_hist[4],dd_hist[5],frame_count);
            if(n>0 && n<(int)sizeof(line)){int fd=open("/usrdata/aihdr-cpu.log",1|64|1024,0600);if(fd>=0){write(fd,line,(u64)n);close(fd);}}
            dd_repeats=dd_served=0;for(u32 k=0;k<6;k++)dd_hist[k]=0;
            trace("perf9 busy ema us",0,frame_count,pace_ema_us);
            trace("perf9 pacing",0,frame_count,pacing);
            unsigned long long t=0,sm=0;
            xreal_blocks_take_counts(&sm,&t);
            trace("blocks smooth tiles per mille",0,frame_count,t ? (long)(sm*1000/t) : -1);
            trace("cpu0 kHz",0,frame_count,sysfs_number("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"));
        }
    }
done:
    for(u32 i=0;i<3;i++)if(planes[i])ar_hal_sys_munmap(planes[i],sizes[i]);
    if(!rc && choice>=0) {
        long busy=now_us()-call_start_us;
        pace_ema_us=pace_ema_us ? pace_ema_us+(busy-pace_ema_us)/16 : busy;
        int was=pacing;
        if(!pacing && pace_ema_us>PACE_ON_US)pacing=1;
        else if(pacing && pace_ema_us<PACE_OFF_US)pacing=0;
        if(pacing!=was)trace(pacing ? "perf9 pacing on (even 30/s), busy ema us" : "perf9 pacing off (60/s), busy ema us",0,frame_count,pace_ema_us);
        if(pacing) {
            /* perf10 fix: measure from THIS call's start. perf9 measured from
             * the previous call's start, so only every other frame slept and
             * the rate stayed ~45-57 instead of an even 30. */
            long wait=PACE_US-(now_us()-call_start_us);
            if(wait>0){struct timespec t={wait/1000000L,(wait%1000000L)*1000L};nanosleep(&t,(struct timespec *)0);}
        }
    }
    pace_last_start_us=call_start_us;
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
