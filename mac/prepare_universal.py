"""Prepare an isolated universal2 build environment on an Apple Silicon Mac."""
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def prepare(destination):
    if platform.machine() != 'arm64':
        raise SystemExit('Run this preparation step on Apple Silicon.')
    destination.mkdir(parents=True, exist_ok=False)
    packages = destination / 'interpreters'
    release = 'cpython-3.12.15-macos-'
    arm, intel = [packages / (release + cpu + '-none') for cpu in ('aarch64', 'x86_64')]
    run('uv', 'python', 'install', '--install-dir', packages, '--no-bin', arm.name, intel.name)

    # Merge copies of the same CPython release. Keep the two downloads pristine.
    merged = destination / 'python'
    shutil.copytree(arm, merged, symlinks=True)
    for source in arm.rglob('*'):
        if source.is_symlink() or not source.is_file():
            continue
        with source.open('rb') as stream:
            if stream.read(4) != bytes.fromhex('cffaedfe'):
                continue
        relative = source.relative_to(arm)
        run('lipo', '-create', source, intel / relative, '-output', merged / relative)

    env = destination / 'env'
    run(merged / 'bin/python3', '-m', 'venv', env)
    python = env / 'bin/python'
    root = Path(__file__).resolve().parents[1]
    run('uv', 'pip', 'install', '--python', python, '-r', root / 'installer/requirements.txt')
    # PyPI supplies separate wheels for HID. Take both versions from the same release.
    version = subprocess.check_output([python, '-c',
        'from importlib.metadata import version; print(version("hidapi"))'], text=True).strip()
    module = Path(subprocess.check_output([python, '-c', 'import hid;print(hid.__file__)'],
                                         text=True).strip())
    intel_wheel = destination / 'intel-hid'
    run('uv', 'pip', 'install', '--python', python, '--target', intel_wheel,
        '--python-platform', 'x86_64-apple-darwin', '--only-binary=:all:', '--no-deps',
        'hidapi==' + version)
    fat_hid = destination / 'hid-universal.so'
    run('lipo', '-create', module, intel_wheel / module.name, '-output', fat_hid)
    run('codesign', '--force', '-s', '-', fat_hid)
    fat_hid.replace(module)
    print(python)


if __name__ == '__main__':
    prepare(Path(sys.argv[1]).resolve())
