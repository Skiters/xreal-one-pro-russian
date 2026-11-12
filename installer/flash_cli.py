"""Console front-end for the same updater backend (macOS/Linux/Windows).

  check   BIN   read-only: model, SKU, SoC/Pilot, package and system ABI
  install BIN   writes firmware; requires --yes-write-firmware
  verify  BIN   read-only: compare installed files with the package
  logs    DIR   read-only: export the adapter timing logs

Exactly one supported XREAL device must be connected.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backend  # noqa: E402
from profiles import check_abi  # noqa: E402

LOGS = ('/usrdata/aihdr-cpu.log', '/usrdata/real3d-cpu.log')


def emit(text, percent):
    print(f'[{percent:5.1f}%] {text}', flush=True)


def device():
    found = backend.devices()
    if len(found) != 1:
        raise SystemExit(f'Нужны ровно одни подключённые очки XREAL, найдено: {len(found)}.')
    return found[0]


def check(path):
    firmware, build = backend.identify_package(path, return_manifest=True)
    _, expected = backend.load_package(path, firmware)
    selected = device()
    with backend.SelectedProbe(selected) as probe:
        soc, pilot, sku = backend.identity(probe, firmware)
        abi = check_abi(probe, build, emit) if build else None
    return {'package': firmware.key, 'package_sha256': expected.package_sha256,
            'release': (build or {}).get('release'), 'usb_pid': hex(selected['product_id']),
            'soc': soc, 'pilot': pilot, 'sku': sku, 'abi_ok': abi is not None,
            'firmware_writes': 0}


def export_logs(directory):
    import io
    import zipfile
    directory.mkdir(parents=True, exist_ok=True)
    saved = {}
    with backend.SelectedProbe(device()) as probe:
        for remote in LOGS:
            result = probe.export(remote, max_bytes=32*1024*1024)
            if result.get('start_status') != 0 or 'data' not in result:
                saved[remote] = 'absent'
                continue
            with zipfile.ZipFile(io.BytesIO(result['data'])) as z:
                for info in z.infolist():
                    if not info.is_dir():
                        target = directory/info.filename.rsplit('/', 1)[-1]
                        target.write_bytes(z.read(info).replace(b'\0', b''))
                        saved[remote] = str(target)
    return saved


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=('check', 'install', 'verify', 'logs'))
    p.add_argument('path', type=Path)
    p.add_argument('--yes-write-firmware', action='store_true',
                   help='Required for install. There is no automatic rollback.')
    a = p.parse_args()
    if a.command == 'check':
        result = check(a.path)
    elif a.command == 'logs':
        result = export_logs(a.path)
    elif a.command == 'verify':
        firmware = backend.identify_package(a.path)
        result = backend.verify_only(device(), emit, firmware=firmware, path=a.path)
        result.pop('verified_files', None)
    else:
        if not a.yes_write_firmware:
            raise SystemExit('Запись прошивки требует флага --yes-write-firmware.')
        print(json.dumps(check(a.path), ensure_ascii=False, indent=2))
        firmware = backend.identify_package(a.path)
        result = backend.update(device(), emit, firmware=firmware, path=a.path)
        result.pop('verified_files', None)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
