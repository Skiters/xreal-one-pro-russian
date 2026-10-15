import sys,io,zipfile,csv,json,copy,hashlib,re
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(BASE/'work/fontdeps'))
from fontTools.ttLib import TTFont
from fontTools import subset
from fontTools.pens.recordingPen import DecomposingRecordingPen

out=BASE/'outputs/xreal-russian-firmware';out.mkdir(exist_ok=True)
work=BASE/'work/update-analysis'
rows=list(csv.reader((BASE/'outputs/xreal-russian-translation/osd_language_ru_draft.csv').open(encoding='utf-8-sig',newline='')))
assert rows[1][13]=='Polski' and rows[1][17]=='Русский'
rows[1][13]='Русский'
for r in rows[4:]:r[13]=r[17]
csvout=io.StringIO(newline='');csv.writer(csvout,lineterminator='\n').writerows(rows)
csvbytes=b'\xef\xbb\xbf'+csvout.getvalue().encode('utf8')
original=zipfile.ZipFile(work/'pilot-original.zip')
stock_rows=list(csv.reader(io.StringIO(original.read('pilot/resource/osd_language.csv').decode('utf-8-sig'))))
assert len(rows)==len(stock_rows)
for r,s in zip(rows,stock_rows):
 assert r[4]==s[4] and r[14]==s[14], 'English/Czech must remain stock'
changes={'pilot/resource/osd_language.csv':csvbytes}
required={ord(c) for row in rows[4:] for c in row[17] if not c.isspace()}
required.update(range(0x410,0x450));required.update([0x401,0x451])
font_reports=[]
with zipfile.ZipFile(work/'MiSans_Latin.zip') as donors:
 for weight in ['Medium','Bold']:
  name=f'pilot/resource/font/MiSans_{weight}_Multi.ttf'
  font=TTFont(io.BytesIO(original.read(name)))
  before=TTFont(io.BytesIO(original.read(name)))
  cmap=dict(font.getBestCmap());missing=required-set(cmap)
  donor=TTFont(io.BytesIO(donors.read(f'MiSans Latin/ttf/MiSansLatin-{weight}.ttf')))
  assert font['head'].unitsPerEm==donor['head'].unitsPerEm
  assert not missing-set(donor.getBestCmap()),missing-set(donor.getBestCmap())
  options=subset.Options();options.hinting=False
  sub=subset.Subsetter(options=options);sub.populate(unicodes=missing);sub.subset(donor)
  order=list(font.getGlyphOrder());rename={g:'ru_'+g for g in donor.getGlyphOrder()}
  for g in donor.getGlyphOrder():
   new=rename[g];assert new not in order
   glyph=copy.deepcopy(donor['glyf'][g])
   if glyph.isComposite():
    for component in glyph.components:component.glyphName=rename[component.glyphName]
   font['glyf'][new]=glyph;order.append(new)
   font['hmtx'].metrics[new]=donor['hmtx'].metrics[g]
   if 'vmtx' in font:font['vmtx'].metrics[new]=(font['head'].unitsPerEm,0)
  font.setGlyphOrder(order)
  added={cp:rename[g] for cp,g in donor.getBestCmap().items()}
  for table in font['cmap'].tables:
   if table.isUnicode() and table.format in (4,12):table.cmap.update(added)
  font['OS/2'].recalcUnicodeRanges(font)
  buf=io.BytesIO();font.save(buf);payload=buf.getvalue();after=TTFont(io.BytesIO(payload))
  assert set(cmap)<=set(after.getBestCmap()) and required<=set(after.getBestCmap())
  # Validate every existing glyph's outlines and horizontal metrics after saving.
  oldset=before.getGlyphSet();newset=after.getGlyphSet()
  for g in before.getGlyphOrder():
   p=DecomposingRecordingPen(oldset);oldset[g].draw(p)
   q=DecomposingRecordingPen(newset);newset[g].draw(q)
   assert p.value==q.value,g
   assert before['hmtx'].metrics[g]==after['hmtx'].metrics[g],g
  changes[name]=payload
  font_reports.append({'weight':weight,'added_codepoints':len(added),'original_codepoints_preserved':len(cmap),'existing_glyph_outlines_and_metrics_unchanged':True})

emulation=json.loads((work/'font-emulation-validation.json').read_text())
assert emulation['all_required_characters_found'] and emulation['existing_character_metrics_unchanged']
changes['pilot/bin/libnr_osd.so']=(work/'libnr_osd-fontfix.so').read_bytes()
checks=original.read('pilot/checksums.sha256').decode()
for name,payload in changes.items():
 rel='./'+name.removeprefix('pilot/')
 pattern=r'(?m)^[0-9a-f]{64}(\s+'+re.escape(rel)+r')$'
 checks,n=re.subn(pattern,lambda m:hashlib.sha256(payload).hexdigest()+m.group(1),checks)
 assert n==1,(rel,n)
changes['pilot/checksums.sha256']=checks.encode()
dest=work/'pilot-russian.zip'
with zipfile.ZipFile(dest,'w') as z:
 for info in original.infolist():z.writestr(copy.copy(info),changes.get(info.filename,original.read(info.filename)))
with zipfile.ZipFile(dest) as z:
 assert z.testzip() is None
 assert z.namelist()==original.namelist()
 for n in z.namelist():
  if n not in changes:assert z.read(n)==original.read(n),n
 for line in checks.splitlines():
  if not line.strip():continue
  h,n=line.split(maxsplit=1);assert hashlib.sha256(z.read('pilot/'+n.removeprefix('./'))).hexdigest()==h,n
report={'component':'Pilot','base_version':'1.9.1.20260914175625','language_slot':'polish','menu_name':'Русский','english_preserved':True,'czech_restored_to_stock':True,'changed_files':list(changes),'font_validation':font_reports,'zip_integrity':True,'all_vendor_file_checksums_verified':True,'unmodified_files_byte_identical':True,'device_installation':'not_yet_attempted'}
(out/'build-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
(out/'embedded-font-validation.json').write_text(json.dumps(emulation,indent=2))
print(json.dumps(report,ensure_ascii=True))
