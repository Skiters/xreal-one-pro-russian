"""Readable, opt-in support reports. No network, firmware writes or serial numbers."""
import json
import platform
import re
from datetime import datetime, timezone

from version import VERSION


def clean(value):
    text = str(value)
    # Exceptions can contain local paths and USB instance/serial identifiers.
    text = re.sub(r'(?:[A-Za-z]:[\\/]|\\\\)[^\r\n]*', '<local path omitted>', text)
    text = re.sub(r'(?i)(?:hid|usb)#[^\s]+', '<USB path omitted>', text)
    return text[:1200]


def report(operation, stage='start', ok=False, error=None, **fields):
    result = {'format': 'XREAL-DIAG-1', 'app': VERSION,
              'utc': datetime.now(timezone.utc).isoformat(),
              'windows': platform.release(), 'operation': operation,
              'stage': clean(stage), 'ok': ok,
              'code': 'XR-OK' if ok else 'XR-' + operation.upper() + '-' + stage.upper()}
    allowed = {'usb_pid', 'soc', 'pilot', 'sku', 'package_sha256', 'build_id',
               'firmware', 'device_count', 'progress', 'exception', 'checks'}
    for key, value in fields.items():
        if key in allowed:
            result[key] = clean(value) if isinstance(value, str) else value
    if error is not None:
        result['error'] = clean(error)
        if isinstance(error, BaseException):
            result['exception'] = type(error).__name__
    return result


def render(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def collect(selected=None, path=None, *, backend=None):
    """Only enumerate and query versions; works without a BIN or connected glasses."""
    if backend is None:
        import backend
    facts = {}
    stage = 'usb'
    try:
        found = backend.devices()
        facts['device_count'] = len(found)
        if selected is not None:
            facts['usb_pid'] = f"{selected['product_id']:04x}"
            stage = 'identity'
            with backend.SelectedProbe(selected) as probe:
                for key, command, payload in [('soc', 38, b''), ('pilot', 4627, b'\x02'), ('sku', 204, b'')]:
                    facts[key] = backend.checked(probe, command, payload).rstrip(b'\0').decode('utf-8')
        elif not found:
            return report('diagnostic', 'no-device', error=RuntimeError('Очки не найдены по USB.'), **facts)
        else:
            return report('diagnostic', 'select-device', error=RuntimeError('Выберите очки в списке.'), **facts)
        if path:
            stage = 'bin'
            firmware, build = backend.identify_package(path, return_manifest=True)
            _, files = backend.load_package(path, firmware)
            facts.update(firmware=firmware.key, package_sha256=files.package_sha256)
            if build:
                facts['build_id'] = build['build_id']
            stage = 'compatibility'
            with backend.SelectedProbe(selected) as probe:
                backend.identity(probe, firmware)
                if build:
                    stage = 'abi'
                    backend.check_abi(probe, build)
            facts['checks'] = 'BIN, model, versions' + (', ABI and memory' if build else '')
        else:
            facts['checks'] = 'USB and versions only; no BIN selected'
        return report('diagnostic', 'complete', ok=True, **facts)
    except Exception as error:
        return report('diagnostic', stage, error=error, **facts)
