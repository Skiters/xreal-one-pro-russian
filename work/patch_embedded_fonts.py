import sys,io,struct,json,csv,zipfile,hashlib
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(BASE/'work/elfdeps'))
import lief
from elftools.elf.elffile import ELFFile
from PIL import ImageFont

src=BASE/'work/xreal-language-offline/libnr_osd.so'
original=src.read_bytes();elf=ELFFile(io.BytesIO(original))
rels={r['r_offset']:r['r_addend'] for r in elf.get_section_by_name('.rela.dyn').iter_relocations() if r['r_info_type']==1027}
def read(va,n):
 for seg in elf.iter_segments():
  if seg['p_type']=='PT_LOAD' and seg['p_vaddr']<=va<seg['p_vaddr']+seg['p_filesz']:
   p=va-seg['p_vaddr']+seg['p_offset'];return original[p:p+n]
 raise ValueError(hex(va))

fontinfo=json.loads((BASE/'work/update-analysis/font-raster-analysis.json').read_text())
rows=list(csv.reader((BASE/'outputs/xreal-russian-translation/osd_language_ru_draft.csv').open(encoding='utf-8-sig')))
chars=sorted({ord(c) for row in rows[4:] for c in row[17] if not c.isspace()}|set(range(0x410,0x450))|{0x401,0x451,32})
assert max(chars)<65536
blob=bytearray();fixups=[];fontrefs=[]
def alloc(data):
 while len(blob)%8:blob.append(0)
 p=len(blob);blob.extend(data);return p
def pointer(where,target,is_local=True):fixups.append((where,target,is_local))
with zipfile.ZipFile(BASE/'work/update-analysis/pilot-russian.zip') as z:
 for info in fontinfo:
  address=info['address'];assert rels.get(address+40,0)==0
  _,weight,size=info['best'][0]
  face=ImageFont.truetype(io.BytesIO(z.read(f'pilot/resource/font/MiSans_{weight}_Multi.ttf')),size)
  bitmap=bytearray();descriptors=bytearray(16)
  for cp in chars:
   mask,offset=face.getmask2(chr(cp),mode='L',anchor='ls');w,h=mask.size
   pixels=list(bytes(mask));index=len(bitmap)
   for k in range(0,len(pixels),2):bitmap.append(((pixels[k]*15+127)//255)<<4 | ((pixels[k+1]*15+127)//255 if k+1<len(pixels) else 0))
   descriptors.extend(struct.pack('<IIHHhh',index,round(face.getlength(chr(cp))*16),w,h,offset[0],-offset[1]-h))
  bmp=alloc(bitmap);gd=alloc(descriptors);uni=alloc(struct.pack('<'+'H'*len(chars),*(c-chars[0] for c in chars)))
  cmap=alloc(struct.pack('<IHHQQHB5x',chars[0],chars[-1]-chars[0]+1,1,0,0,len(chars),3));pointer(cmap+8,uni)
  dsc=alloc(struct.pack('<QQQQHHIQ',0,0,0,0,0,1|(4<<9),0,0))
  pointer(dsc,bmp);pointer(dsc+8,gd);pointer(dsc+16,cmap)
  # Reuse the exact original metrics and callbacks. Only the fallback data is new.
  font=bytearray(read(address,56));font[:16]=b'\0'*16;font[32:56]=b'\0'*24
  fo=alloc(font);pointer(fo,0x27e340,False);pointer(fo+8,0x27e0e0,False);pointer(fo+32,dsc)
  fontrefs.append({'original_address':address,'fallback_offset':fo,'size':size,'weight':weight,'glyphs':len(chars)})

b=lief.parse(str(src));assert b.relocate_phdr_table(lief.ELF.Binary.PHDR_RELOC.FILE_END)
segment=lief.ELF.Segment();segment.type=lief.ELF.Segment.TYPE.LOAD
segment.flags=lief.ELF.Segment.FLAGS.R|lief.ELF.Segment.FLAGS.W;segment.alignment=0x10000;segment.content=list(blob)
added=b.add(segment);base=added.virtual_address
def reloc(addr,value):
 r=lief.ELF.Relocation(addr,lief.ELF.Relocation.TYPE.AARCH64_RELATIVE,lief.ELF.Relocation.ENCODING.RELA);r.addend=value;b.add_dynamic_relocation(r)
for where,target,local in fixups:reloc(base+where,base+target if local else target)
for info in fontrefs:reloc(info['original_address']+40,base+info['fallback_offset'])
dest=BASE/'work/update-analysis/libnr_osd-fontfix.so';b.write(str(dest))
check=ELFFile(io.BytesIO(dest.read_bytes()))
assert check.get_section_by_name('.text').data()==elf.get_section_by_name('.text').data()
assert check.get_section_by_name('.text')['sh_addr']==elf.get_section_by_name('.text')['sh_addr']
newrels={r['r_offset']:r['r_addend'] for r in check.get_section_by_name('.rela.dyn').iter_relocations() if r['r_info_type']==1027}
for addr,value in rels.items():assert newrels[addr]==value,(hex(addr),hex(value))
for info in fontrefs:assert newrels[info['original_address']+40]==base+info['fallback_offset']
report={'embedded_fonts_extended':len(fontrefs),'fallback_glyphs_per_font':len(chars),'original_machine_code_unchanged':True,'original_relocations_unchanged':True,'font_details':fontrefs,'segment_base':base,'added_data_bytes':len(blob),'sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),'chars':chars}
(BASE/'work/update-analysis/embedded-font-patch.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k not in ['font_details','chars']}))
