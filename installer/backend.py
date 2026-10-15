"""Selected-device updater using the validated XREAL website HID protocol."""
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
import sys
import time
import zipfile

import hid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'work'))
from xreal_usb_probe import Probe, ALLOWED

ALLOWED.update({4628, 4629, 4630})
FIRMWARE_SHA = '258ecdb3a9892c7a2bf700c4f0fbccf52ced8915081c0109beb08c59c60418ec'
SOC = '15.1.03.603_20260914'
PILOT = '1.9.1.20260914175625'
PILOT_2 = '2.0.0.20260920102250'
SUPPORTED_SKUS = frozenset({'X1112010', 'X1112011'})
FILES = ['pilot/resource/osd_language.csv', 'pilot/resource/font/MiSans_Medium_Multi.ttf',
         'pilot/resource/font/MiSans_Bold_Multi.ttf', 'pilot/checksums.sha256', 'pilot/bin/libnr_osd.so']


@dataclass(frozen=True)
class Firmware:
    key: str
    filename: str
    sha256: str
    pilot: str
    xor_key: int
    skus: frozenset[str]
    files: tuple[str, ...]


STANDARD = Firmware('standard', 'XREAL_One_Pro_Pilot_RU.bin', FIRMWARE_SHA, PILOT, 0x69,
                    SUPPORTED_SKUS, tuple(FILES))
EXPERIMENTAL = Firmware(
    'experimental', 'XREAL-Pilot2-RU-Hebe-Within-Limit.bin',
    '2230136051f41b5cf7a753ea47b51b502df1fb743f5049a8c9443fb9f99499c3',
    PILOT_2, 0x66, frozenset({'X1112010'}),
    ('pilot/bin/pilot', 'pilot/bin/libnr_image_post_processor.so',
     'pilot/bin/libxreal_cpu_adapter.so', 'pilot/bin/libnr_osd.so',
     'pilot/conf/pilot_default_config.json', 'pilot/conf/pilot_default_config_gina.json',
     'pilot/conf/pilot_default_config_hebe.json', 'pilot/resource/lut_3d_ai_hdr.cube',
     'pilot/checksums.sha256'))
FIRMWARES = {firmware.key: firmware for firmware in (STANDARD, EXPERIMENTAL)}
ACCEPTED_PILOT = frozenset({'1.9.1.20260914175625', PILOT_2})


def devices():
    return [d for d in hid.enumerate(0x3318, 0x0436)
            if d['interface_number'] == 0 and d['usage_page'] == 0xff00]


class SelectedProbe(Probe):
    def __init__(self, selected):
        self.selected = selected

    def __enter__(self):
        # Never silently switch to the first attached device.
        matches = [d for d in devices() if d['path'] == self.selected['path']]
        if len(matches) != 1:
            raise RuntimeError('Выбранные очки отключены. Подключите их и обновите список.')
        serial = self.selected.get('serial_number')
        if serial and matches[0].get('serial_number') != serial:
            raise RuntimeError('Подключено другое устройство. Выберите очки заново.')
        self.device = hid.device()
        try:
            self.device.open_path(matches[0]['path'])
            self.device.set_nonblocking(False)
        except Exception:
            self.device.close()
            raise
        return self


def checked(probe, command, payload=b'', timeout=16):
    result = probe.query(command, payload, timeout=timeout)
    if result['status'] != 0:
        raise RuntimeError(f"Очки отклонили команду {command}: код {result['status']}.")
    return result['payload']


def identity(probe, firmware=STANDARD):
    actual = tuple(checked(probe, cmd, payload).rstrip(b'\0').decode('utf-8')
                   for cmd, payload in [(38, b''), (4627, b'\x02'), (204, b'')])
    supported_current = (actual[1] in ACCEPTED_PILOT and
                         (actual[1] != PILOT_2 or actual[2] == 'X1112010'))
    if actual[0] != SOC or not supported_current or actual[2] not in firmware.skus:
        raise RuntimeError('Эта сборка не подходит к версии или модели очков. '
                           f'Обнаружены: SoC {actual[0]}, Pilot {actual[1]}, SKU {actual[2]}. '
                           f'Нужны: {SOC}, Pilot 1.9.1 или 2.0.0, '
                           f'SKU из списка: {", ".join(sorted(firmware.skus))}. Запись не начата.')
    return actual


def firmware_path(firmware=STANDARD):
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / firmware.filename
    folder = 'xreal-russian-firmware' if firmware is STANDARD else 'xreal-pilot2-hebe'
    return Path(__file__).resolve().parents[1] / 'outputs' / folder / firmware.filename


def load_package(path=None, firmware=STANDARD):
    data = (path or firmware_path(firmware)).read_bytes()
    if hashlib.sha256(data).hexdigest() != firmware.sha256:
        raise RuntimeError('Встроенная прошивка повреждена. Загрузите приложение заново.')
    if (int.from_bytes(data[4:8], 'little') != len(data)-8 or
            int.from_bytes(data[8:12], 'little') != 0x1500 or
            int.from_bytes(data[12:16], 'little') != 4 or
            data[16:36] != firmware.pilot.encode()):
        raise RuntimeError('Неверный заголовок прошивки.')
    with zipfile.ZipFile(io.BytesIO(data[64:].translate(bytes(x ^ firmware.xor_key for x in range(256))))) as z:
        if z.testzip() is not None:
            raise RuntimeError('Архив прошивки повреждён.')
        expected = {name: hashlib.sha256(z.read(name)).hexdigest() for name in firmware.files}
    return data, expected


def transmit(probe, data, emit):
    checked(probe, 4628, data[:64])
    last = 0.0
    for offset in range(64, len(data), 490):
        checked(probe, 4629, data[offset:offset+490])
        if time.monotonic()-last >= .15 or offset+490 >= len(data):
            percent = min(offset+490, len(data)) / len(data) * 85
            emit('Передача прошивки. Не отключайте очки.', percent)
            last = time.monotonic()
    emit('Применение прошивки. Не отключайте очки.', 86)
    # No automatic retries: the protocol has no chunk offset/idempotency guarantee.
    checked(probe, 4630, timeout=55)


def verify_files(probe, expected, emit):
    for index, (name, digest) in enumerate(expected.items()):
        emit(f'Проверка установленных файлов: {index+1} из {len(expected)}',
             90+index*9/max(1, len(expected)-1))
        result = probe.export('/usrdata/'+name, max_bytes=64*1024*1024)
        if result['start_status'] != 0 or result.get('finish_status') != 0:
            raise RuntimeError('Не удалось прочитать установленный файл: '+name)
        with zipfile.ZipFile(io.BytesIO(result['data'])) as z:
            matches = [n for n in z.namelist() if n.rsplit('/', 1)[-1] == name.rsplit('/', 1)[-1]]
            if len(matches) != 1 or hashlib.sha256(z.read(matches[0])).hexdigest() != digest:
                raise RuntimeError('Проверка установленного файла не пройдена: '+name)


def verify_selected(selected, expected, emit, probe_factory=SelectedProbe, sleep=time.sleep,
                    firmware=STANDARD):
    # Only read-back is retryable. Restart the entire export session on a fresh
    # handle, never replay an individual chunk (the protocol has no offsets).
    for attempt in range(3):
        try:
            with probe_factory(selected) as probe:
                actual = identity(probe, firmware)
                if actual[1] != firmware.pilot:
                    raise RuntimeError('На очках другая версия Pilot. Выберите соответствующую прошивку.')
                verify_files(probe, expected, emit)
            return
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            emit('USB-соединение прервалось. Повторная проверка файлов без записи…', 89)
            sleep(5)


def verify_only(selected, emit, firmware=STANDARD):
    emit('Проверка встроенного пакета…', 0)
    _, expected = load_package(firmware=firmware)
    verify_selected(selected, expected, emit, firmware=firmware)
    emit('Готово. Все проверяемые файлы совпадают с прошивкой. Можно отключить очки.', 100)


def update(selected, emit, probe_factory=SelectedProbe, package_loader=None, sleep=time.sleep,
           firmware=STANDARD):
    emit('Проверка встроенной прошивки…', 0)
    data, expected = (package_loader or (lambda: load_package(firmware=firmware)))()
    with probe_factory(selected) as probe:
        emit('Проверка модели и версии очков…', 0)
        identity(probe, firmware)
        emit('Начинается запись. Не отключайте очки.', 1)
        transmit(probe, data, emit)
    emit('Ожидание перезапуска очков…', 89)
    # The old process can still answer immediately after accepting finish.
    sleep(5)
    for attempt in range(30):
        try:
            with probe_factory(selected) as probe:
                actual = identity(probe, firmware)
                if actual[1] != firmware.pilot:
                    raise RuntimeError('Очки ещё не запустили выбранную версию Pilot.')
            break
        except Exception:
            if attempt == 29:
                raise RuntimeError('Пакет передан, но очки не вернулись для проверки. '
                                   'Успех установки не подтверждён. Переподключите очки и проверьте меню.')
            sleep(2)
    try:
        verify_selected(selected, expected, emit, probe_factory=probe_factory, sleep=sleep,
                        firmware=firmware)
    except Exception as e:
        raise RuntimeError('Очки приняли пакет, но проверка файлов не завершена. '
                           'Переподключите очки и нажмите «Проверить без записи». '
                           'Повторная прошивка пока не нужна. Причина: '+str(e)) from e
    emit('Готово. Файлы проверены. Можно отключить очки и проверить русское меню.', 100)
