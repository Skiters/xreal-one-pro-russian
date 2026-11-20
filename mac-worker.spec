import os
from pathlib import Path

root = Path(SPECPATH)
name = 'XREAL Updater'
arch = os.environ.get('XREAL_MAC_ARCH', 'arm64')
a = Analysis([str(root / 'installer/mac_worker.py')], pathex=[str(root / 'work')])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name,
          console=True, target_arch=arch)
coll = COLLECT(exe, a.binaries, a.datas, name=name)
app = BUNDLE(coll, name=name + '.app', bundle_identifier='local.xreal.russian-updater',
             info_plist={'LSMinimumSystemVersion': '13.0' if arch == 'universal2' else '12.0',
                         'CFBundleShortVersionString': '0.7.2',
                         'CFBundleVersion': '1', 'CFBundleDevelopmentRegion': 'ru',
                         'NSHighResolutionCapable': True})
