import argparse,json,hashlib,time,zipfile,io,datetime
from pathlib import Path
import xreal_usb_probe as transport

BASE=Path(__file__).resolve().parent.parent
OUT=BASE/'outputs/xreal-russian-firmware'
parser=argparse.ArgumentParser();parser.add_argument('--flash',action='store_true');args=parser.parse_args()
manifest=json.loads((OUT/'package-manifest.json').read_text())
data=(OUT/manifest['file']).read_bytes()
assert hashlib.sha256(data).hexdigest()==manifest['sha256']
assert manifest['project_code']==0x1500 and manifest['component_type']==4
assert int.from_bytes(data[4:8],'little')==len(data)-8
assert data[16:36]==b'1.9.1.20260914175625'
with zipfile.ZipFile(io.BytesIO(data[64:].translate(bytes(x^0x69 for x in range(256))))) as z:
 assert z.testzip() is None
print('Package integrity verified. Flash enabled: '+str(args.flash),flush=True)
if not args.flash:raise SystemExit(0)
log={'package_sha256':manifest['sha256'],'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'events':[]}
def event(phase,**kw):
 item={'phase':phase,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),**kw}
 log['events'].append(item);(OUT/'installation-log.json').write_text(json.dumps(log,indent=2))
 print(json.dumps(item),flush=True)
def checked(p,cmd,payload=b'',timeout=16):
 r=p.query(cmd,payload,timeout=timeout)
 if r['status']!=0:raise RuntimeError(f'Command {cmd}: status {r["status"]}, payload {r["payload"].hex()}')
 return r
try:
 with transport.Probe() as p:
  soc=checked(p,38)['payload'].rstrip(b'\0').decode()
  pilot=checked(p,4627,b'\x02')['payload'].rstrip(b'\0').decode()
  sku=checked(p,204)['payload'].rstrip(b'\0').decode()
  assert soc=='15.1.03.603_20260914' and pilot=='1.9.1.20260914175625' and sku=='X1112010',(soc,pilot,sku)
  event('identity_verified',soc=soc,pilot=pilot,sku=sku)
  transport.ALLOWED.update({4628,4629,4630})
  event('start_requested')
  checked(p,4628,data[:64]);event('start_accepted')
  last=time.monotonic()
  for offset in range(64,len(data),490):
   checked(p,4629,data[offset:offset+490])
   if time.monotonic()-last>=15:
    event('transmitting',bytes_acknowledged=min(offset+490,len(data)),total=len(data),percent=round(min(offset+490,len(data))/len(data)*100,1));last=time.monotonic()
  event('all_data_acknowledged',bytes=len(data))
  event('finish_requested')
  checked(p,4630,timeout=55);event('finish_accepted')
except Exception as e:
 event('error',error=repr(e));raise
