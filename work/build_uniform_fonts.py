"""Local experimental build: unified Latin/Cyrillic primary fonts, stock fallback."""
import csv, copy, hashlib, io, json, re, struct, sys, zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BASE/'work/elfdeps'), str(BASE/'work/fontdeps')]
import lief
from elftools.elf.elffile import ELFFile
from fontTools.ttLib import TTFont
from fontTools import subset
from PIL import Image, ImageFont, ImageDraw

OUT = BASE/'outputs/xreal-uniform-fonts'
OUT.mkdir(exist_ok=True)
SCRATCH = BASE/'work/uniform-fonts'
SCRATCH.mkdir(exist_ok=True)
rows = list(csv.reader((BASE/'outputs/xreal-russian-translation/osd_language_ru_draft.csv').open(encoding='utf-8-sig')))
chars = sorted(set(range(32,127)) | set(range(0x410,0x450)) | {0x401,0x451,0xa0} |
               {ord(c) for row in rows[4:] for c in row[17] if not c.isspace()})
stock = zipfile.ZipFile(BASE/'work/update-analysis/pilot-original.zip')
donorzip = zipfile.ZipFile(BASE/'work/update-analysis/MiSans_Latin.zip')
donors = {w: donorzip.read(f'MiSans Latin/ttf/MiSansLatin-{w}.ttf') for w in ('Medium','Bold')}
rows[1][13]='Русский'
for row in rows[4:]: row[13]=row[17]
csvout=io.StringIO(newline='');csv.writer(csvout,lineterminator='\n').writerows(rows)
changes = {'pilot/resource/osd_language.csv': b'\xef\xbb\xbf'+csvout.getvalue().encode('utf-8')}
for weight, data in donors.items():
    name = f'pilot/resource/font/MiSans_{weight}_Multi.ttf'
    font = TTFont(io.BytesIO(stock.read(name)))
    donor = TTFont(io.BytesIO(data))
    assert set(chars) <= donor.getBestCmap().keys()
    assert font['head'].unitsPerEm == donor['head'].unitsPerEm
    options = subset.Options(); options.hinting = False
    sub = subset.Subsetter(options=options); sub.populate(unicodes=chars); sub.subset(donor)
    order = list(font.getGlyphOrder())
    rename = {g:'uniform_'+g for g in donor.getGlyphOrder()}
    for g in donor.getGlyphOrder():
        new = rename[g]; assert new not in order
        glyph = copy.deepcopy(donor['glyf'][g])
        if glyph.isComposite():
            for component in glyph.components: component.glyphName = rename[component.glyphName]
        font['glyf'][new] = glyph; order.append(new)
        font['hmtx'].metrics[new] = donor['hmtx'].metrics[g]
        if 'vmtx' in font: font['vmtx'].metrics[new] = (font['head'].unitsPerEm,0)
    font.setGlyphOrder(order)
    mapping = {cp:rename[g] for cp,g in donor.getBestCmap().items()}
    for table in font['cmap'].tables:
        if table.isUnicode() and table.format in (4,12): table.cmap.update(mapping)
    font['OS/2'].recalcUnicodeRanges(font)
    buf=io.BytesIO(); font.save(buf); changes[name]=buf.getvalue()
    after=TTFont(io.BytesIO(buf.getvalue()))
    for cp in chars:
        assert after['hmtx'].metrics[after.getBestCmap()[cp]] == donor['hmtx'].metrics[donor.getBestCmap()[cp]]

src = BASE/'work/xreal-language-offline/libnr_osd.so'
original = src.read_bytes(); elf = ELFFile(io.BytesIO(original))
rels = {r['r_offset']:r['r_addend'] for r in elf.get_section_by_name('.rela.dyn').iter_relocations() if r['r_info_type']==1027}
def read(va,n):
    for seg in elf.iter_segments():
        if seg['p_type']=='PT_LOAD' and seg['p_vaddr']<=va<seg['p_vaddr']+seg['p_filesz']:
            p=va-seg['p_vaddr']+seg['p_offset']; return original[p:p+n]
    raise ValueError(hex(va))

blob=bytearray(); fixups=[]; details=[]
def alloc(data):
    while len(blob)%8: blob.append(0)
    p=len(blob); blob.extend(data); return p
def pointer(where,target,local=True): fixups.append((where,target,local))
fontinfo=json.loads((BASE/'work/update-analysis/font-raster-analysis.json').read_text())
for info in fontinfo:
    address=info['address']; assert rels.get(address+40,0)==0
    _,weight,size=info['best'][0]
    height,baseline=struct.unpack('<ii',read(address+16,8))
    assert (height,baseline)==(info['height'],info['baseline'])
    proposed=size
    # Fit all accents/descenders into the existing line box, preserving widget height.
    while True:
        face=ImageFont.truetype(io.BytesIO(donors[weight]),size)
        boxes=[face.getbbox(chr(cp),anchor='ls') for cp in chars]
        shift=max(0,max(b[3] for b in boxes)-baseline)
        if min(b[1] for b in boxes)-shift>=-(height-baseline): break
        size-=1; assert size>=6
    bitmap=bytearray(); descriptors=bytearray(16); glyphs={}
    for cp in chars:
        mask,offset=face.getmask2(chr(cp),mode='L',anchor='ls'); w,h=mask.size
        pixels=list(bytes(mask)); index=len(bitmap)
        quant=[(v*15+127)//255 for v in pixels]
        for k in range(0,len(quant),2): bitmap.append(quant[k]<<4 | (quant[k+1] if k+1<len(quant) else 0))
        advance=round(face.getlength(chr(cp))*16)
        desc=(index,advance,w,h,offset[0],-offset[1]-h+shift)
        descriptors.extend(struct.pack('<IIHHhh',*desc))
        glyphs[str(cp)]={'descriptor':desc,'bitmap_sha256':hashlib.sha256(bytes(bitmap[index:])).hexdigest(),
                         'bytes':(w*h+1)//2}
    bmp=alloc(bitmap); gd=alloc(descriptors)
    uni=alloc(struct.pack('<'+'H'*len(chars),*(c-chars[0] for c in chars)))
    cmap=alloc(struct.pack('<IHHQQHB5x',chars[0],chars[-1]-chars[0]+1,1,0,0,len(chars),3)); pointer(cmap+8,uni)
    dsc=alloc(struct.pack('<QQQQHHIQ',0,0,0,0,0,1|(4<<9),0,0))
    pointer(dsc,bmp); pointer(dsc+8,gd); pointer(dsc+16,cmap)
    # Keep icons, CJK and other untouched scripts reachable via a clone of the
    # stock font. Primary Latin AND Cyrillic now use the same new rasterizer.
    fallback=bytearray(read(address,56)); fallback[:16]=bytes(16); fallback[32:56]=bytes(24)
    fo=alloc(fallback)
    pointer(fo,rels[address],False); pointer(fo+8,rels[address+8],False)
    pointer(fo+32,rels[address+32],False)
    details.append({'address':address,'dsc_offset':dsc,'fallback_offset':fo,'bitmap_offset':bmp,
                    'size':size,'previous_size':proposed,'vertical_shift':shift,'weight':weight,'height':height,'baseline':baseline,'glyphs':glyphs})

b=lief.parse(str(src)); assert b.relocate_phdr_table(lief.ELF.Binary.PHDR_RELOC.FILE_END)
segment=lief.ELF.Segment(); segment.type=lief.ELF.Segment.TYPE.LOAD
segment.flags=lief.ELF.Segment.FLAGS.R|lief.ELF.Segment.FLAGS.W; segment.alignment=0x10000; segment.content=list(blob)
added=b.add(segment); base=added.virtual_address
def reloc(addr,value):
    r=lief.ELF.Relocation(addr,lief.ELF.Relocation.TYPE.AARCH64_RELATIVE,lief.ELF.Relocation.ENCODING.RELA)
    r.addend=value; b.add_dynamic_relocation(r)
for where,target,local in fixups: reloc(base+where,base+target if local else target)
replaced={d['address']+32:base+d['dsc_offset'] for d in details}
for r in b.dynamic_relocations:
    if r.address in replaced: r.addend=replaced[r.address]
for d in details: reloc(d['address']+40,base+d['fallback_offset'])
dest=SCRATCH/'libnr_osd-uniform.so'; b.write(str(dest))
check=ELFFile(io.BytesIO(dest.read_bytes()))
assert check.get_section_by_name('.text').data()==elf.get_section_by_name('.text').data()
assert check.get_section_by_name('.text')['sh_addr']==elf.get_section_by_name('.text')['sh_addr']
newrels={r['r_offset']:r['r_addend'] for r in check.get_section_by_name('.rela.dyn').iter_relocations() if r['r_info_type']==1027}
for addr,value in rels.items(): assert newrels[addr]==replaced.get(addr,value)
changes['pilot/bin/libnr_osd.so']=dest.read_bytes()
report={'chars':chars,'fonts':details,'segment_base':base,'machine_code_unchanged':True,'stock_fallback_preserved':True,
        'primary_dsc_relocations_changed':len(replaced),'external_fonts_same_donor':True}
(SCRATCH/'font-data.json').write_text(json.dumps(report,indent=2))

# Quantized raster preview, same advances and 4bpp glyph pixels as firmware.
sample='Настройки 120 Гц / Settings — Ёё Дд Йй'
preview_width=340+round(max(sum(d['glyphs'][str(ord(c))]['descriptor'][1]/16 for c in sample) for d in details))
canvas=Image.new('RGB',(preview_width,sum(d['height']+36 for d in details)+20),'#11151c')
draw=ImageDraw.Draw(canvas); y=10
for i,d in enumerate(details):
    draw.text((12,y),f"{i+1:02}  {d['weight']} {d['size']} px | line {d['height']}",fill='#94a3b8')
    x=300.; line_y=y
    for c in sample:
        index,adv,w,h,ox,oy=d['glyphs'][str(ord(c))]['descriptor']
        packed=blob[d['bitmap_offset']+index:d['bitmap_offset']+index+(w*h+1)//2]
        pix=bytes(v*17 for byte in packed for v in (byte>>4,byte&15))[:w*h]
        if w*h:
            mask=Image.frombytes('L',(w,h),pix)
            canvas.paste((240,244,250),(round(x)+ox,line_y+d['height']-d['baseline']-h-oy),mask)
        x+=adv/16
    y+=d['height']+36
canvas.save(OUT/'font-preview.png')
checks=stock.read('pilot/checksums.sha256').decode()
for name,payload in changes.items():
    rel='./'+name.removeprefix('pilot/')
    checks,n=re.subn(r'(?m)^[0-9a-f]{64}(\s+'+re.escape(rel)+r')$',lambda m:hashlib.sha256(payload).hexdigest()+m[1],checks)
    assert n==1
changes['pilot/checksums.sha256']=checks.encode()
with zipfile.ZipFile(SCRATCH/'pilot-uniform.zip','w') as z:
    for info in stock.infolist(): z.writestr(copy.copy(info),changes.get(info.filename,stock.read(info.filename)))
with zipfile.ZipFile(SCRATCH/'pilot-uniform.zip') as z:
    assert z.testzip() is None
    for n in z.namelist():
        if n not in changes: assert z.read(n)==stock.read(n)
    for line in checks.splitlines():
        if line.strip():
            h,n=line.split(maxsplit=1); assert hashlib.sha256(z.read('pilot/'+n.removeprefix('./'))).hexdigest()==h
print(json.dumps({'fonts':len(details),'unified_chars_per_font':len(chars),'zip_verified':True,
                  'size_adjustments':[(d['previous_size'],d['size']) for d in details]}))
