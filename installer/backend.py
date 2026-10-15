"""Selected-device updater using the validated XREAL website HID protocol."""
import hashlib
import io
from pathlib import Path
import sys
import time
import zipfile

import hid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'work'))
from xreal_usb_probe import Probe, ALLOWED

ALLOWED.update({4628, 4629, 4630})
FIRMWARE_SHA = '135ff3fee312361323af11e03dac7edf779cf4386cd1a858f767e1320b3e988e'
SOC = '15.1.03.603_20260914'
PILOT = '1.9.1.20260914175625'
SKU = 'X1112010'
FILES = ['pilot/resource/osd_language.csv', 'pilot/resource/font/MiSans_Medium_Multi.ttf',
         'pilot/resource/font/MiSans_Bold_Multi.ttf', 'pilot/checksums.sha256', 'pilot/bin/libnr_osd.so']


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


def identity(probe):
    actual = tuple(checked(probe, cmd, payload).rstrip(b'\0').decode('utf-8')
                   for cmd, payload in [(38, b''), (4627, b'\x02'), (204, b'')])
    if actual != (SOC, PILOT, SKU):
        raise RuntimeError('Эта сборка не подходит к версии или модели очков. '
                           f'Обнаружены: SoC {actual[0]}, Pilot {actual[1]}, SKU {actual[2]}. '
                           f'Нужны: {SOC}, {PILOT}, {SKU}. Запись не начата.')
    return actual


def firmware_path():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'XREAL_One_Pro_Pilot_RU.bin'
    return Path(__file__).resolve().parents[1] / 'outputs/xreal-russian-firmware/XREAL_One_Pro_Pilot_RU.bin'


def load_package(path=None):
    data = (path or firmware_path()).read_bytes()
    if hashlib.sha256(data).hexdigest() != FIRMWARE_SHA:
        raise RuntimeError('Встроенная прошивка повреждена. Загрузите приложение заново.')
    if (int.from_bytes(data[4:8], 'little') != len(data)-8 or
            int.from_bytes(data[8:12], 'little') != 0x1500 or
            int.from_bytes(data[12:16], 'little') != 4 or
            data[16:36] != PILOT.encode()):
        raise RuntimeError('Неверный заголовок прошивки.')
    with zipfile.ZipFile(io.BytesIO(data[64:].translate(bytes(x ^ 0x69 for x in range(256))))) as z:
        if z.testzip() is not None:
            raise RuntimeError('Архив прошивки повреждён.')
        expected = {name: hashlib.sha256(z.read(name)).hexdigest() for name in FILES}
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
    for index, name in enumerate(FILES):
        emit(f'Проверка установленных файлов: {index+1} из {len(FILES)}', 90+index*2)
        result = probe.export('/usrdata/'+name, max_bytes=32*1024*1024)
        if result['start_status'] != 0 or result.get('finish_status') != 0:
            raise RuntimeError('Не удалось прочитать установленный файл: '+name)
        with zipfile.ZipFile(io.BytesIO(result['data'])) as z:
            matches = [n for n in z.namelist() if n.rsplit('/', 1)[-1] == name.rsplit('/', 1)[-1]]
            if len(matches) != 1 or hashlib.sha256(z.read(matches[0])).hexdigest() != expected[name]:
                raise RuntimeError('Проверка установленного файла не пройдена: '+name)


def update(selected, emit, probe_factory=SelectedProbe, package_loader=load_package, sleep=time.sleep):
    emit('Проверка встроенной прошивки…', 0)
    data, expected = package_loader()
    with probe_factory(selected) as probe:
        emit('Проверка модели и версии очков…', 0)
        identity(probe)
        emit('Начинается запись. Не отключайте очки.', 1)
        transmit(probe, data, emit)
    emit('Ожидание перезапуска очков…', 89)
    for attempt in range(30):
        try:
            with probe_factory(selected) as probe:
                identity(probe)
            break
        except Exception:
            if attempt == 29:
                raise RuntimeError('Пакет передан, но очки не вернулись для проверки. '
                                   'Успех установки не подтверждён. Переподключите очки и проверьте меню.')
            sleep(2)
    with probe_factory(selected) as probe:
        verify_files(probe, expected, emit)
    emit('Готово. Файлы проверены. Можно отключить очки и проверить русское меню.', 100)
