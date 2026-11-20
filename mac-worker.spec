from pathlib import Path

root = Path(SPECPATH)
name = 'XREAL Updater'
a = Analysis([str(root / 'installer/mac_worker.py')], pathex=[str(root / 'work')])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name,
          console=True, target_arch='arm64')
coll = COLLECT(exe, a.binaries, a.datas, name=name)
app = BUNDLE(coll, name=name + '.app', bundle_identifier='local.xreal.russian-updater',
             info_plist={'LSMinimumSystemVersion': '12.0',
                         'CFBundleShortVersionString': '0.7.2',
                         'CFBundleVersion': '1', 'CFBundleDevelopmentRegion': 'ru',
                         'NSHighResolutionCapable': True})
