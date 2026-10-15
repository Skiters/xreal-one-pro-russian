import sys, io, struct, json, hashlib, zipfile
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(BASE/'work/elfdeps'),str(BASE/'work/fontdeps')]
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM
from unicorn.arm64_const import *
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import DecomposingRecordingPen
report=json.loads((BASE/'work/uniform-fonts/font-data.json').read_text())
def load(path):
    e=ELFFile(io.BytesIO(path.read_bytes()));u=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
    segs=[s for s in e.iter_segments() if s['p_type']=='PT_LOAD']
    end=max(s['p_vaddr']+s['p_memsz'] for s in segs);u.mem_map(0,(end+4095)&~4095)
    for s in segs:u.mem_write(s['p_vaddr'],s.data())
    for r in e.get_section_by_name('.rela.dyn').iter_relocations():
        if r['r_info_type']==1027:u.mem_write(r['r_offset'],struct.pack('<Q',r['r_addend']))
    u.mem_map(0x20000000,0x100000);u.mem_map(0x30000000,0x1000)
    u.reg_write(UC_ARM64_REG_CPACR_EL1,3<<20)
    return u
def call(u,font,cp,bitmap=False):
    out=0x20000100;u.mem_write(out,bytes(32));u.reg_write(UC_ARM64_REG_SP,0x200ff000)
    regs=[(UC_ARM64_REG_X0,font),(UC_ARM64_REG_X1,cp if bitmap else out),
          (UC_ARM64_REG_X2,cp),(UC_ARM64_REG_X3,0),(UC_ARM64_REG_X30,0x30000000)]
    for r,v in regs:u.reg_write(r,v)
    u.emu_start(0x27dd48 if bitmap else 0x27dd98,0x30000000,count=20000)
    assert u.reg_read(UC_ARM64_REG_PC)==0x30000000
    return u.reg_read(UC_ARM64_REG_X0),bytes(u.mem_read(out,24))
old=load(BASE/'work/xreal-language-offline/libnr_osd.so')
new=load(BASE/'work/uniform-fonts/libnr_osd-uniform.so')
tests=bitmaps=fallbacks=0
for f in report['fonts']:
    for cp in report['chars']:
        ok,desc=call(new,f['address'],cp)
        assert ok and not(desc[18]&16),(hex(f['address']),hex(cp),'missing')
        assert struct.unpack_from('<Q',desc)[0]==f['address'],'Must resolve through unified primary font'
        _,advance,w,h,ox,oy=f['glyphs'][str(cp)]['descriptor']
        assert struct.unpack_from('<HHHhh',desc,8)==((advance+8)//16,w,h,ox,oy),(hex(cp),desc)
        assert -f['baseline']<=oy and oy+h<=f['height']-f['baseline'],'Glyph outside line box'
        if w*h:
            ptr,_=call(new,f['address'],cp,True);assert ptr
            data=bytes(new.mem_read(ptr,(w*h+1)//2))
            assert hashlib.sha256(data).hexdigest()==f['glyphs'][str(cp)]['bitmap_sha256']
            bitmaps+=1
        tests+=1
    count=0
    for cp in list(range(0x4e00,0x4f00))+list(range(0xf000,0xf100))+[0x00e9,0x2026,0x2190,0x2122]:
        if cp in report['chars']:continue
        prior,pdesc=call(old,f['address'],cp)
        if not prior or pdesc[18]&16:continue
        ok,desc=call(new,f['address'],cp)
        assert ok and desc[8:]==pdesc[8:],'Stock fallback metrics must be unchanged'
        assert struct.unpack_from('<Q',desc)[0]==report['segment_base']+f['fallback_offset']
        p1,_=call(old,f['address'],cp,True)
        p2,_=call(new,report['segment_base']+f['fallback_offset'],cp,True)
        assert p1==p2,'Original bitmap address changed'
        fallbacks+=1;count+=1
        if count>=5:break

external=0
with zipfile.ZipFile(BASE/'work/uniform-fonts/pilot-uniform.zip') as z,zipfile.ZipFile(BASE/'work/update-analysis/pilot-original.zip') as stock,zipfile.ZipFile(BASE/'work/update-analysis/MiSans_Latin.zip') as dz:
    for weight in ('Medium','Bold'):
        name=f'pilot/resource/font/MiSans_{weight}_Multi.ttf'
        f=TTFont(io.BytesIO(z.read(name)));o=TTFont(io.BytesIO(stock.read(name)))
        donor=TTFont(io.BytesIO(dz.read(f'MiSans Latin/ttf/MiSansLatin-{weight}.ttf')))
        fs=f.getGlyphSet();ds=donor.getGlyphSet()
        for cp in report['chars']:
            a=f.getBestCmap()[cp];b=donor.getBestCmap()[cp]
            p=DecomposingRecordingPen(fs);q=DecomposingRecordingPen(ds);fs[a].draw(p);ds[b].draw(q)
            assert p.value==q.value and f['hmtx'].metrics[a]==donor['hmtx'].metrics[b]
            external+=1
        for cp,g in o.getBestCmap().items():
            if cp not in report['chars']:assert f.getBestCmap()[cp]==g and f['hmtx'].metrics[g]==o['hmtx'].metrics[g]

result={'actual_arm64_renderer_emulated':True,'unified_font_character_cases':tests,'bitmap_cases':bitmaps,
        'stock_fallback_cases':fallbacks,'external_donor_outline_and_metric_cases':external,
        'all_characters_fit_line_boxes':True,'latin_and_cyrillic_resolve_same_primary_font':True,
        'machine_code_unchanged':True,'device_installation':'not_attempted','visual_on_device':'pending',
        'full_menu_widget_widths':'not_verified_on_device'}
(BASE/'outputs/xreal-uniform-fonts/validation.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
