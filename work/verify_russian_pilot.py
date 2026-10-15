import time,json,zipfile,io,hashlib,datetime
from pathlib import Path
from xreal_usb_probe import Probe
BASE=Path(__file__).resolve().parent.parent;OUT=BASE/'outputs/xreal-russian-firmware'
expected=zipfile.ZipFile(BASE/'work/update-analysis/pilot-russian.zip')
names=['pilot/resource/osd_language.csv','pilot/resource/font/MiSans_Medium_Multi.ttf','pilot/resource/font/MiSans_Bold_Multi.ttf','pilot/checksums.sha256','pilot/bin/libnr_osd.so']
result={'verified_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files':[]}
last=None
for attempt in range(30):
 try:
  with Probe() as p:
   r=p.query(4627,b'\x02',timeout=4)
   assert r['status']==0
   result['pilot_version']=r['payload'].rstrip(b'\0').decode()
  break
 except Exception as e:last=e;time.sleep(2)
else:raise RuntimeError(f'Device unavailable after update: {last}')
with Probe() as p:
 for name in names:
  r=p.export('/usrdata/'+name,max_bytes=32*1024*1024)
  assert r['start_status']==0 and r['finish_status']==0,r
  with zipfile.ZipFile(io.BytesIO(r['data'])) as z:
   assert z.testzip() is None
   matches=[n for n in z.namelist() if n.rsplit('/',1)[-1]==name.rsplit('/',1)[-1]]
   assert len(matches)==1
   b=z.read(matches[0]);assert b==expected.read(name),name
  item={'path':'/usrdata/'+name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'matches_build':True}
  result['files'].append(item);print(json.dumps(item),flush=True)
result['all_modified_files_read_back_and_verified']=True
result['visual_on_device_confirmation']='pending_user_check'
(OUT/'installed-verification.json').write_text(json.dumps(result,indent=2))
print('READBACK VERIFIED',flush=True)
