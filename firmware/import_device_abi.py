"""Read-only capture of versions/system ABI. Never sends firmware-write commands."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'installer'))
import backend
from profiles import ABI_LIBRARIES, export_file


def capture(output):
    devices=backend.devices()
    if len(devices)!=1:
        raise RuntimeError('Connect exactly one supported XREAL USB device.')
    selected=devices[0]
    firmware=backend.ONE_EXPERIMENTAL if selected['product_id']==0x0438 else backend.EXPERIMENTAL
    output.mkdir(parents=True,exist_ok=True)
    aliases={'libpthread-2.25.so':'libpthread.so.0','libc-2.25.so':'libc.so.6'}
    report={'usb_pid':selected['product_id'],'firmware_writes':0,'files':{}}
    with backend.SelectedProbe(selected) as probe:
        report['identity']=backend.identity(probe,firmware)
        for path,expected in ABI_LIBRARIES.items():
            data=export_file(probe,path)
            digest=hashlib.sha256(data).hexdigest()
            name=path.rsplit('/',1)[-1]
            (output/aliases.get(name,name)).write_bytes(data)
            report['files'][path]={'sha256':digest,'matches_candidate':digest==expected}
        data=export_file(probe,'/proc/media-mem',1024*1024)
        (output/'media-mem.txt').write_bytes(data)
        report['media_mem']=data.decode('ascii',errors='replace')
    (output/'abi-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    capture(parser.parse_args().output)
