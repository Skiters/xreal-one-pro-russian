"""Read-only, selected-device capture. No firmware update commands or backend import."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'work'))
from xreal_usb_probe import Probe
import hid
from profiles import ABI_LIBRARIES, SOC

LIMIT = 64 * 1024 * 1024
TOTAL = 1024 * 1024 * 1024
READ_COMMANDS = {38, 24, 204, 4627, 207, 208, 209}
PATHS = [
    '/usrdata/pilot/checksums.sha256', '/usrdata/pilot/pilot_version.txt',
    '/proc/version', '/proc/cpuinfo', '/proc/mounts', '/proc/partitions',
    '/proc/mtd', '/proc/media-mem', '/proc/modules', '/etc/os-release',
    '/system/build.prop', '/vendor/build.prop',
    '/vendor/firmware/', '/lib/firmware/', '/usr/lib/firmware/', '/mnt/',
    '/usrdata/pilot/resource/', '/usr/lib/', '/vendor/lib64/',
] + ['/usr/lib/'+n for n in (
    'libhal_dsp.so', 'libhal_npu.so', 'libmpi_dsp.so', 'libmpi_npu.so',
    'libhal_sys.so', 'libmpp_service.so', 'librknn_runtime.so', 'librknnrt.so'
)] + ['/vendor/lib64/librknn_runtime.so', '/mnt/ceva_ls.bin']


# Include exact file requests so a PID/SKU investigation can compare library hashes.
PATHS.extend(path for path in ABI_LIBRARIES if path not in PATHS)


def evidence(report):
    hashes = {}
    for entry in report['requests']:
        path = entry['remote']
        if path not in ABI_LIBRARIES:
            continue
        files = [f for f in entry.get('files', [])
                 if PurePosixPath(f['name']).name == PurePosixPath(path).name]
        if len(files) == 1:
            hashes[path] = files[0]['sha256']
    soc = report['identity'].get('soc', {})
    return {'reference_soc': SOC,
            'soc_matches': soc.get('value') == SOC if soc.get('status') == 0 else None,
            'libraries': {p: {'expected': h, 'observed': hashes.get(p),
                              'matches': hashes[p] == h if p in hashes else None}
                          for p, h in ABI_LIBRARIES.items()}}


def devices():
    # Enumerate unknown XREAL PIDs as well; compatibility is probed read-only.
    return [d for d in hid.enumerate(0x3318, 0)
            if d.get('interface_number') == 0 and d.get('usage_page') == 0xff00]


class Reader(Probe):
    def __init__(self, selected):
        self.selected = selected

    def __enter__(self):
        matches = [d for d in devices() if d['path'] == self.selected['path']]
        if len(matches) != 1 or any(matches[0].get(k) != self.selected.get(k)
                                  for k in ('product_id', 'serial_number')):
            raise RuntimeError('Выбранные очки отключены или заменены.')
        self.device = hid.device()
        try:
            self.device.open_path(self.selected['path'])
            self.device.set_nonblocking(False)
        except Exception:
            self.device.close()
            raise
        return self

    def query(self, command, payload=b'', timeout=4):
        if command not in READ_COMMANDS:
            raise ValueError('Команда записи запрещена в приложении дампа')
        return super().query(command, payload, timeout)


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or '\\' in name or ':' in name or
            any(p in ('', '.', '..') for p in name.split('/')) or
            any(ord(c) < 32 for c in name)):
        raise ValueError('Недопустимый путь в ответе очков')
    return path


def unpack(blob, folder):
    """Validate whole ZIP before creating files; keep each request isolated."""
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        items = archive.infolist()
        if len(items) > 5000 or sum(i.file_size for i in items) > 256*1024*1024:
            raise ValueError('Распакованный ответ превышает лимит')
        seen = set()
        for i in items:
            name = i.filename.rstrip('/') if i.is_dir() else i.filename
            safe_name(name)
            if name.casefold() in seen or i.orig_filename != i.filename:
                raise ValueError('Повторяющееся имя в архиве')
            seen.add(name.casefold())
            if stat.S_IFMT(i.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError('Ссылки и специальные файлы не извлекаются')
        result = []
        for i in items:
            if i.is_dir():
                continue
            data = archive.read(i)
            target = folder.joinpath(*safe_name(i.filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            result.append({'name': i.filename, 'bytes': len(data),
                           'sha256': hashlib.sha256(data).hexdigest()})
        return result


def capture(selected, destination, emit, stop, factory=Reader):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    report = {'format': 'XREAL-DUMP-1', 'usb_pid': selected['product_id'],
              'full_dump': False, 'firmware_writes': 0, 'identity': {}, 'requests': [],
              'limitations': 'File export only; not a raw flash image. Unsupported paths, '
              'size limits, protected partitions and inaccessible directories prevent completeness.'}
    def save():
        (destination/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    queue = list(PATHS)
    total = 0
    try:
        with factory(selected) as probe:
            for label, cmd, payload in [('soc',38,b''), ('dsp',24,b''),
                                        ('sku',204,b''), ('pilot',4627,b'\x02')]:
                if stop.is_set(): break
                r = probe.query(cmd, payload, timeout=8)
                report['identity'][label] = {'status': r['status'],
                    'raw_hex': r['payload'].hex(),
                    'value': r['payload'].rstrip(b'\0').decode('utf-8', 'replace')}
            for path in queue:
                if stop.is_set() or total >= TOTAL: break
                emit(f'Чтение {path} ({len(report["requests"])+1}/{len(queue)})')
                entry = {'remote': path}
                report['requests'].append(entry)
                try:
                    r = probe.export(path, max_bytes=min(LIMIT, TOTAL-total))
                    entry.update(start_status=r['start_status'], finish_status=r.get('finish_status'))
                    if r['start_status'] != 0 or r.get('finish_status') != 0:
                        continue
                    blob = r['data']; total += len(blob)
                    stem = f'{len(report["requests"]):04d}'
                    (destination/(stem+'.zip')).write_bytes(blob)
                    entry['archive'] = stem+'.zip'
                    entry['files'] = unpack(blob, destination/'files'/stem)
                    if path == '/usrdata/pilot/checksums.sha256':
                        with zipfile.ZipFile(io.BytesIO(blob)) as z:
                            candidates = [n for n in z.namelist() if PurePosixPath(n).name == 'checksums.sha256']
                            if len(candidates) == 1:
                                for line in z.read(candidates[0]).decode('ascii').splitlines():
                                    digest, name = line.split(None,1)
                                    name = name.removeprefix('./')
                                    safe_name(name)
                                    remote = '/usrdata/pilot/'+name
                                    if remote not in queue and len(queue)<1000: queue.append(remote)
                except (OSError, TimeoutError):
                    # Stop on a broken transport, rather than spending minutes on each path.
                    raise
                except Exception as e:
                    entry['error'] = str(e)
                finally:
                    save()
        report['state'] = 'cancelled' if stop.is_set() else 'limit' if total >= TOTAL else 'finished'
    except Exception as e:
        report['state'] = 'interrupted'
        report['error'] = str(e)
    finally:
        report['bytes_received'] = total
        report['profile_evidence'] = evidence(report)
        save()
    return report
