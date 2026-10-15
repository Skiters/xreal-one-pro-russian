import sys,io,struct,json
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(BASE/'work/elfdeps'))
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM
from unicorn.arm64_const import *
report=json.loads((BASE/'work/update-analysis/embedded-font-patch.json').read_text())
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
def call(u,font,cp):
 out=0x20000100;u.mem_write(out,bytes(32));u.reg_write(UC_ARM64_REG_SP,0x200ff000)
 for r,v in [(UC_ARM64_REG_X0,font),(UC_ARM64_REG_X1,out),(UC_ARM64_REG_X2,cp),(UC_ARM64_REG_X3,0),(UC_ARM64_REG_X30,0x30000000)]:u.reg_write(r,v)
 u.emu_start(0x27dd98,0x30000000,count=20000)
 assert u.reg_read(UC_ARM64_REG_PC)==0x30000000,'Function did not return'
 return u.reg_read(UC_ARM64_REG_X0)&255,bytes(u.mem_read(out,24))
old=load(BASE/'work/xreal-language-offline/libnr_osd.so');new=load(BASE/'work/update-analysis/libnr_osd-fontfix.so')
tests=0;repaired=0
for f in report['font_details']:
 a=f['original_address']
 for cp in report['chars']:
  ok,desc=call(new,a,cp);assert ok and not (desc[18]&16),(hex(a),hex(cp),'missing')
  prior,pdesc=call(old,a,cp)
  if prior and not(pdesc[18]&16):assert desc==pdesc,(hex(a),hex(cp),'existing glyph changed')
  else:repaired+=1
  tests+=1
result={'actual_arm64_renderer_emulated':True,'font_character_cases':tests,'missing_glyph_cases_repaired':repaired,'all_required_characters_found':True,'existing_character_metrics_unchanged':True}
(BASE/'work/update-analysis/font-emulation-validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
