"""Local request/response protocol for the macOS window."""
import hashlib
import json
import sys
import signal
import threading
from datetime import datetime
from pathlib import Path

import app
import backend
import diagnostics
import dump_core


def send(**event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


def token(device):
    identity = [device['path'].hex(), device['product_id'], device.get('serial_number')]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


class Events:
    def __init__(self):
        self.error = None

    def put(self, item):
        kind, message, value = item
        if kind == 'support':
            send(report=diagnostics.render(message))
        elif kind == 'error':
            self.error = message
        elif kind == 'progress':
            send(status=message, percent=value)


def perform(req):
    op = req['op']
    path = req.get('path') or None
    if op == 'bin':
        fw, build = backend.identify_package(path, return_manifest=True)
        return dict(firmware=fw.key, pilot=fw.pilot, build=build or {})
    found = dump_core.devices()
    if op == 'list':
        return dict(devices=[dict(id=token(d), pid=d['product_id'], name=d.get('product_string') or 'XREAL') for d in found])
    selected = None
    if req.get('id'):
        matches = [d for d in found if token(d) == req['id']]
        if len(matches) != 1:
            raise RuntimeError('Очки отключены или заменены. Обновите список.')
        selected = matches[0]
    if op == 'diag':
        value = diagnostics.collect(selected, path, enumerate_devices=dump_core.devices,
                                    identity_reader=dump_core.Reader)
        return dict(report=diagnostics.render(value), status='Проверка завершена.' if value['ok'] else value.get('error', value['code']))
    if selected is None:
        raise ValueError('Выберите устройство.')
    if op == 'dump':
        dest = Path(req['folder']) / datetime.now().strftime('XREAL-dump-%Y%m%d-%H%M%S-%f')
        stop = threading.Event()
        signal.signal(signal.SIGUSR1, lambda *_: stop.set())
        send(canStop=True)
        report = dump_core.capture(selected, dest, lambda msg: send(status=msg), stop)
        libs = report['profile_evidence']['libraries'].values()
        matches = sum(item['matches'] is True for item in libs)
        changed = sum(item['matches'] is False for item in libs)
        missing = len(libs) - matches - changed
        summary = f'Библиотеки профиля: совпали {matches}, отличаются {changed}, нет данных {missing}.'
        return dict(folder=str(dest), state=report['state'], detail=report.get('error', summary))
    if op == 'identity':
        fields = {}
        with dump_core.Reader(selected) as reader:
            for label, cmd, payload in [('sku',204,b''),('soc',38,b''),('pilot',4627,b'\x02')]:
                raw = backend.checked(reader, cmd, payload, timeout=8)
                fields[label] = backend.sku_text(raw) if label == 'sku' else raw.rstrip(b'\0').decode('utf-8','backslashreplace')
        pid = selected['product_id']
        known = pid in (0x0438, 0x043e) or (pid == 0x0436 and fields['sku'] in backend.SUPPORTED_SKUS)
        pilots = {backend.PILOT} if pid == 0x043e else backend.ACCEPTED_PILOT
        known = known and fields['soc'] == backend.SOC and fields['pilot'] in pilots
        return dict(identity=fields, known=known)
    if op not in ('verify','update'):
        raise ValueError('Неизвестная операция.')
    firmware, build = backend.identify_package(path, return_manifest=True)
    events = Events()
    app.run_operation(selected, firmware, path, op == 'verify', build, events)
    if events.error:
        raise RuntimeError(events.error)
    return dict(status='Готово. Установленные файлы проверены.')


if __name__ == '__main__':
    if sys.argv[1:]:
        # Preserve legacy non-writing CLI switches and diagnostics exit codes.
        import runpy
        runpy.run_module('app', run_name='__main__')
    else:
        request = {}
        try:
            request = json.load(sys.stdin)
            send(done=True, **perform(request))
        except Exception as error:
            send(done=True, error=str(error), report=diagnostics.render(diagnostics.report('bin' if request.get('op') == 'bin' else 'device', 'validation' if request.get('op') == 'bin' else 'request', error=error)))
            sys.exit(1)
