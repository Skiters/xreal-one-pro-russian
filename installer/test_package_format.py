import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import backend
import package_format as pf
import profiles
from test_backend import FakeProbe


def elf():
    data=bytearray(120)
    data[:7]=b'\x7fELF\x02\x01\x01'
    struct.pack_into('<HH',data,16,3,183)
    struct.pack_into('<Q',data,32,64)
    struct.pack_into('<HH',data,54,56,1)
    struct.pack_into('<IIQQQQQQ',data,64,1,5,0,0,0,120,120,4096)
    return bytes(data)


def entries(items):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        for name,value in items: archive.writestr(name,value)
    return zipfile.ZipFile(io.BytesIO(stream.getvalue()))


class PackageTests(unittest.TestCase):
    def test_paths_links_duplicates_and_directory_aliases(self):
        for path in ('../evil','pilot/../evil','/pilot/x','pilot//x',
                     'pilot/a:stream','pilot/./x'):
            with self.subTest(path=path), entries([(path,b'')]) as z:
                with self.assertRaises(ValueError): pf.bounded_zip(z)
        for path in ('pilot/a\\x', 'pilot/x\0suffix'):
            with self.assertRaises(ValueError): pf.canonical_name(path)
        # zipfile normalizes backslashes on Windows when writing; inject bytes
        # into both central/local filenames to exercise a hostile incoming ZIP.
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z: z.writestr('pilot/aXx',b'')
        for replacement in (b'pilot/a\\x',b'pilot/a\0x'):
            with zipfile.ZipFile(io.BytesIO(stream.getvalue().replace(b'pilot/aXx',replacement))) as z:
                with self.assertRaises(ValueError): pf.bounded_zip(z)
        for kind in (stat.S_IFLNK,stat.S_IFIFO,stat.S_IFSOCK,stat.S_IFCHR):
            info=zipfile.ZipInfo('pilot/link')
            info.external_attr=(kind|0o777)<<16
            with entries([(info,b'target')]) as z:
                with self.assertRaises(ValueError): pf.bounded_zip(z)
        for items in ([('pilot/a',b'x'),('pilot/a/',b'')],
                      [('pilot/a',b'x'),('pilot/a/b',b'x')]):
            with entries(items) as z:
                with self.assertRaises(ValueError): pf.bounded_zip(z)

    def test_limits_before_decompression(self):
        with entries([('pilot/large',bytes(100))]) as z:
            for constant in ('MAX_FILE','MAX_UNPACKED'):
                with patch.object(pf,constant,99):
                    with self.assertRaises(ValueError): pf.bounded_zip(z)
            with patch.object(pf,'MAX_ENTRIES',0):
                with self.assertRaises(ValueError): pf.bounded_zip(z)

    def test_elf_wrong_arch_and_out_of_bounds(self):
        pf.validate_elf(elf(),'test')
        for offset,value in ((4,1),(18,62),(54,55),(64+8,255)):
            data=bytearray(elf());data[offset]=value
            with self.assertRaises(ValueError): pf.validate_elf(data,'test')

    def test_configuration_preserves_string_urls(self):
        pf.validate_config(b'{"display":{"url":"https://example.test"}} // note','test')
        pf.validate_config(b'{}','pilot/conf/pilot_default_config_gf.json')
        for data in (b'{}',b'[]',b'{"display":1}',b'not json'):
            with self.assertRaises(ValueError): pf.validate_config(data,'pilot/conf/pilot_default_config.json')

    def test_build_profile_and_manifest_digest(self):
        build={'schema':2,'profile':'one-pro-603','soc':profiles.SOC,
               'pilot':backend.PILOT_2,'abi_libraries':profiles.ABI_LIBRARIES,
               'mmz':[profiles.MMZ_START,profiles.MMZ_END]}
        raw=(json.dumps(build,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
        build['build_id']=hashlib.sha256(raw).hexdigest()
        profiles.validate_build(build,'experimental',backend.PILOT_2)
        with self.assertRaises(ValueError):
            profiles.validate_build(build,'one-experimental',backend.PILOT_2)
        for key,value in (('build_id','0'*64),('mmz',[0,0]),
                          ('abi_libraries',{}),('soc','wrong')):
            changed=copy.deepcopy(build);changed[key]=value
            with self.assertRaises(ValueError):
                profiles.validate_build(changed,'experimental',backend.PILOT_2)

    def test_abi_mismatch_never_writes(self):
        expected=pf.VerifiedFiles({},build={'profile':'one-pro-603'})
        probe=FakeProbe(pilot=backend.PILOT_2)
        with self.assertRaisesRegex(RuntimeError,'библиотека отличается'):
            backend.update({'path':b'test'},lambda *_:None,
                           probe_factory=lambda _:probe,
                           package_loader=lambda:(bytes(64),expected),
                           firmware=backend.EXPERIMENTAL,sleep=lambda _:None)
        self.assertFalse(any(c in (4628,4629,4630) for c,_ in probe.calls))

    def test_all_files_including_resources_read_back(self):
        probe=FakeProbe()
        expected={'pilot/resource/font/example.ttf':hashlib.sha256(b'expected').hexdigest(),
                  'pilot/resource/osd_language.csv':hashlib.sha256(b'expected').hexdigest()}
        backend.verify_files(probe,expected,lambda *_:None)
        probe.corrupt=True
        with self.assertRaises(RuntimeError): backend.verify_files(probe,expected,lambda *_:None)
