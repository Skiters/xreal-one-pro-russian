import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from dump_core import Reader, unpack, capture, safe_name


def archive(name, content=b'payload', mode=None):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        i=zipfile.ZipInfo(name)
        if mode:i.external_attr=mode<<16
        z.writestr(i,content)
    return buf.getvalue()


class Tests(unittest.TestCase):
    def test_write_rejected_before_device_access(self):
        for cmd in (4628,4629,4630,0):
            with self.assertRaises(ValueError):Reader({}).query(cmd)

    def test_traversal_and_links(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ('../escape','/absolute','C:/absolute','a\\b'):
                with self.assertRaises(ValueError):safe_name(name)
            with self.assertRaises(ValueError):unpack(archive('../escape'),Path(d))
            with self.assertRaises(ValueError):unpack(archive('link',mode=0o120777),Path(d))

    def test_capture_manifest_and_partial_report(self):
        class Fake:
            def __init__(self,s):pass
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def query(self,*a,**kw):return {'status':0,'payload':b'test'}
            def export(self,path,**kw):
                if path.endswith('checksums.sha256'):
                    b=archive('checksums.sha256',b'1234  ./bin/model.rknn\n')
                elif path.endswith('model.rknn'):b=archive('model.rknn')
                else:return {'start_status':16}
                return {'start_status':0,'finish_status':0,'data':b}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'capture'
            r=capture({'product_id':123},p,lambda m:None,threading.Event(),Fake)
            self.assertFalse(r['full_dump'])
            self.assertEqual(r['state'],'finished')
            self.assertTrue(any(x['remote'].endswith('model.rknn') and x.get('files') for x in r['requests']))
            self.assertTrue((p/'report.json').exists())


if __name__=='__main__':unittest.main()
