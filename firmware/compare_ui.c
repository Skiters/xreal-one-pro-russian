/* One extra native menu item. Strength remains entirely in the vendor OSD.
 * All offsets belong to the SHA-pinned OSD in compare_modes.py.
 */
#ifdef XREAL_COMPARE_ONEPRO_WARP
#include "onepro_warp.h"
typedef unsigned long U;
typedef unsigned char B;
extern void *memcpy(void *, const void *, U);
extern void xreal_clone_row(void *, U, U, U, U, U, U);
static U osd;
static U widgets[0x310/8];
static U extra[0x120/8];
static int row_ready, item_ready;
static U invoke(U offset, U a, U b, U c, U d) {
    return ((U (*)(U,U,U,U))(osd+offset))(a,b,c,d);
}
static U pointer(U object, U offset) { return *(U *)(object+offset); }
static void put_pointer(U object, U offset, U value) { *(U *)(object+offset)=value; }
static unsigned mode(U object) { return *(unsigned *)(object+0xb0); }
static void choose(void *closure) {
    int selection=*(int *)closure;
    if (selection!=3 && selection!=4) return;
    if (xreal_compare_set(selection==3 ? 1 : 2)) return;
    int native=3;
    invoke(0xf5b20,(U)&native,0,0,0);
}
static void title(U item, const char *name) {
    invoke(0xadb48,item+0x90,(U)name,0,0); /* std::string::assign */
    invoke(0x28eb08,pointer(item,0),pointer(item,0x90),0,0);
}
static void make_item(U source) {
    /* Only the native inline enum closure is copied; the two strings receive
     * independent constructors. No C++ object from the source is destroyed.
     * Generic AddItem performs its normal deep copy into the menu vector.
     */
    memcpy(extra,(void *)source,sizeof(extra));
    U item=(U)extra;
    invoke(0xf3070,item+0x90,(U)"Real3D Pro+",0,0);
    invoke(0xf3070,item+0xf8,pointer(source,0xf8),0,0);
    put_pointer(item,0,widgets[0x2f0/8]);
    put_pointer(item,0x10,widgets[0x308/8]);
    put_pointer(item,0x18,widgets[0x2f8/8]);
    put_pointer(item,0x20,widgets[0x300/8]);
    put_pointer(item,0x38,widgets[0x2e8/8]);
    *(unsigned *)(item+0xb0)=4;
    *(unsigned *)(item+0x50)=4;
    put_pointer(item,0x68,(U)choose);
    title(item,"Real3D Pro+");
    /* Description lookup through the existing language manager. */
    const char *key="Real3DTips";
    U text[4]={(U)key,10,0,0};
    U translated=invoke(0xb4900,(U)text,0,0,0);
    invoke(0x28eb08,widgets[0x308/8],translated,0,0);
    item_ready=1;
}
static void layout(U list) {
    U begin=pointer(list,0x10), end=pointer(list,0x18);
    if (end-begin!=5*0x120) return;
    unsigned index=0;
    for (U it=begin;it<end;it+=0x120,++index) {
        U box=pointer(it,0x38);
        invoke(0x263880,box,0,index*76,0);
        invoke(0x263a30,box,760,72,0);
        invoke(0x263880,pointer(it,0),142,mode(it) ? 4 : 14,0);
        invoke(0x263a30,pointer(it,0),520,mode(it) ? 38 : 44,0);
        if (pointer(it,0x18))
            invoke(0x263880,pointer(it,0x18),52,6,0);
        if (pointer(it,0x10)) {
            invoke(0x263880,pointer(it,0x10),142,42,0);
            invoke(0x263a30,pointer(it,0x10),520,26,0);
        }
    }
}
__attribute__((visibility("hidden"))) void xreal_ui_call(U *r) {
    osd=r[16];
    U site=r[30]-osd-4;
    if (site==0x540e8) {
        r[0]=invoke(0x26e2a8,r[0],r[1],r[2],r[3]);
        widgets[0x268/8]=pointer(r[19],0x268);
        xreal_clone_row(widgets,r[20],r[21],r[22],r[23],r[24],osd);
        row_ready=1;
    } else if (site==0xf9aa8) {
        U list=r[0], item=r[1];
        if (mode(item)==3 && row_ready) {
            put_pointer(item,0x68,(U)choose);
            title(item,"Real3D One Pro");
            make_item(item);
        }
        r[0]=invoke(0x10c338,list,item,0,0);
        if (mode(item)==3 && item_ready)
            invoke(0x10c338,list,(U)extra,0,0);
        layout(list);
    } else if (site==0xf9bc0) {
        U *old=(U *)r[1], entries[5], n=0;
        if (item_ready && old[1]-old[0]==4*8) {
            for (U p=old[0];p<old[1];p+=8) {
                U it=*(U *)p;
                entries[n++]=it;
                if (mode(it)==3) {
                    entries[n++]=(U)extra;
                    /* Match the native Real3D availability check. */
                    U hidden=invoke(0x262008,pointer(it,0x38),1,0,0);
                    invoke(hidden ? 0x261de8 : 0x261ee8,
                           pointer((U)extra,0x38),1,0,0);
                }
            }
            if (n==5) {
                U vector[3]={(U)entries,(U)(entries+5),(U)(entries+5)};
                r[0]=invoke(0x10cc28,r[0],(U)vector,r[2],r[3]);
                return;
            }
        }
        r[0]=invoke(0x10cc28,r[0],r[1],r[2],r[3]);
    } else if (site==0xf75c8) {
        r[0]=invoke(0xf3070,r[0],(U)"Real3D One Pro",0,0);
    } else {
        /* Only patched native selection/highlight calls reach this branch.
         * Other menu groups and their enums pass through without changes.
         */
        if (item_ready && r[0]==osd+0x238bf50 && r[1]==3)
            r[1]=xreal_compare_algorithm()==1 ? 3 : 4;
        r[0]=invoke(0x10a5f8,r[0],r[1],r[2],r[3]);
    }
}
#endif
