"""Check every Mach-O slice and both application deployment declarations."""
import plistlib
import re
import subprocess
import sys
from pathlib import Path


def command(*args):
    return subprocess.check_output(args, text=True)


def check(bundle):
    binaries = 0
    for path in bundle.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        if path.name == 'Info.plist' and path.parent.name == 'Contents':
            with path.open('rb') as source:
                if plistlib.load(source).get('LSMinimumSystemVersion') != '13.0':
                    raise ValueError(f'Incorrect minimum macOS in {path}')
        with path.open('rb') as source:
            if source.read(4).hex() not in {'cffaedfe','feedfacf','cefaedfe','cafebabe','cafebabf'}:
                continue
        if set(command('lipo', '-archs', path).split()) != {'arm64', 'x86_64'}:
            raise ValueError(f'Incomplete architecture set: {path}')
        for arch in ('arm64', 'x86_64'):
            headers = command('otool','-arch',arch,'-l',path)
            versions = re.findall(r'cmd LC_(?:BUILD_VERSION|VERSION_MIN_MACOSX)\n\s*cmdsize \d+\n(?:\s*platform \S+\n)?\s*(?:minos|version) ([\d.]+)', headers)
            if len(versions) != 1 or tuple(map(int,versions[0].split('.'))) > (13,0,0):
                raise ValueError(f'Unsupported deployment target {versions} for {arch}: {path}')
        binaries += 1
    if not binaries:
        raise ValueError('Bundle contains no Mach-O files')
    print(f'{binaries} universal binaries checked in {bundle.name}')


if __name__ == '__main__':
    check(Path(sys.argv[1]))
