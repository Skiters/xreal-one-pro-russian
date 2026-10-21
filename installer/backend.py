"""Selected-device updater using the validated XREAL website HID protocol."""
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
import sys
import time
import zipfile

from package_format import container_crc, read_container, bounded_zip, MAX_CONTAINER, MAX_FILE
from profiles import validate_build, check_abi

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


STANDARD = Firmware('standard', 'XREAL-One-RU-Pilot1.9.1.bin', FIRMWARE_SHA, PILOT, 0x69,
                    SUPPORTED_SKUS, tuple(FILES))
EXPERIMENTAL = Firmware(
    'experimental', 'XREAL-Pilot2-RU-Hebe-Real3D-CPU.bin',
    '1b72fc6acd90fc9b8995644cca659907d571292f19a19a33cdbddd59da5decd0',
    PILOT_2, 0x66, SUPPORTED_SKUS,
    ('pilot/bin/pilot', 'pilot/bin/libnr_image_post_processor.so',
     'pilot/bin/libxreal_cpu_adapter.so', 'pilot/bin/libnr_osd.so',
     'pilot/conf/pilot_default_config.json', 'pilot/conf/pilot_default_config_gina.json',
     'pilot/conf/pilot_default_config_hebe.json', 'pilot/resource/lut_3d_ai_hdr.cube',
     'pilot/checksums.sha256'))
ONE_TARGET = {'schema': 1, 'model': 'one', 'usb_pid': 0x0438, 'soc': SOC, 'experimental': True}
ONE_EXPERIMENTAL = Firmware(
    'one-experimental', 'XREAL-One-Pilot2-RU-Real3D-AIHDR-TEST.bin', '',
    PILOT_2, 0x66, frozenset(),
    EXPERIMENTAL.files + ('pilot/conf/pilot_default_config_gf.json', 'pilot/xreal-target.json'))
ONE_S_TARGET = {'schema': 1, 'model': '1s', 'usb_pid': 0x043e, 'soc': SOC, 'experimental': False}
ONE_S_STANDARD = Firmware('1s-standard', 'XREAL-1S-RU-Pilot1.9.1.bin', '', PILOT, 0x69,
                          frozenset(), tuple(FILES) + ('pilot/xreal-target.json',))
FIRMWARES = {firmware.key: firmware for firmware in (STANDARD, EXPERIMENTAL, ONE_EXPERIMENTAL, ONE_S_STANDARD)}
ACCEPTED_PILOT = frozenset({'1.9.1.20260914175625', PILOT_2})


def devices():
    return [d for pid in (0x0436, 0x0438, 0x043e) for d in hid.enumerate(0x3318, pid)
            if d['interface_number'] == 0 and d['usage_page'] == 0xff00]


class SelectedProbe(Probe):
    def __init__(self, selected):
        self.selected = selected

    def __enter__(self):
        # Never silently switch to the first attached device.
        matches = [d for d in devices() if d['path'] == self.selected['path']]
        if len(matches) != 1:
            raise RuntimeError('Выбранные очки отключены. Подключите их и обновите список.')
        if self.selected.get('product_id') != matches[0].get('product_id'):
            raise RuntimeError('Модель USB-устройства изменилась. Выберите очки заново.')
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
    pid = getattr(probe, 'selected', {}).get('product_id', 0x0436)
    one = pid == 0x0438
    supported_current = (actual[1] in ACCEPTED_PILOT and
                         (pid != 0x043e or actual[1] == PILOT) and
                         (actual[1] != PILOT_2 or one or
                          (pid == 0x0436 and actual[2] in SUPPORTED_SKUS)))
    supported_model = ((pid == 0x043e and firmware is ONE_S_STANDARD) or (one and firmware in (STANDARD, ONE_EXPERIMENTAL)) or
                       (pid == 0x0436 and firmware is not ONE_EXPERIMENTAL and actual[2] in firmware.skus))
    if actual[0] != SOC or not supported_current or not supported_model:
        raise RuntimeError('Эта сборка не подходит к версии или модели очков. '
                           f'Обнаружены: USB PID {pid:04x}, SoC {actual[0]}, '
                           f'Pilot {actual[1]}, SKU {actual[2]}. '
                           'Для One нужен стандартный BIN или тестовый BIN именно для One; для One Pro '
                           'требуется поддерживаемый SKU и версия Pilot; для 1S нужен отдельный BIN 1S. Запись не начата.')
    return actual


def firmware_path(firmware=STANDARD):
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / firmware.filename
    if firmware is ONE_S_STANDARD:
        return Path(__file__).resolve().parents[1] / 'outputs/v0.7.0' / firmware.filename
    if firmware is ONE_EXPERIMENTAL:
        return Path(__file__).resolve().parents[1] / 'outputs/xreal-one-real3d-hdr-test' / firmware.filename
    folder = 'xreal-russian-firmware' if firmware is STANDARD else 'xreal-pilot2-real3d-cpu'
    filename = 'XREAL_One_Pro_Pilot_RU.bin' if firmware is STANDARD else firmware.filename
    return Path(__file__).resolve().parents[1] / 'outputs' / folder / filename


def load_package(path=None, firmware=STANDARD):
    try:
        data, expected, target = read_container(path or firmware_path(firmware),
                                                firmware.pilot, firmware.xor_key)
        required_target = ONE_TARGET if firmware is ONE_EXPERIMENTAL else ONE_S_TARGET if firmware is ONE_S_STANDARD else None
        if target != required_target:
            raise RuntimeError('BIN предназначен для другой модели очков.')
        if not set(firmware.files) <= expected.keys():
            raise ValueError('Нет обязательных файлов прошивки.')
        validate_build(expected.build, firmware.key, firmware.pilot)
    except (zipfile.BadZipFile, KeyError, UnicodeError, ValueError) as error:
        raise RuntimeError('Архив BIN не прошёл проверку: '+str(error)) from error
    return data, expected

def identify_package(path, return_manifest=False):
    if Path(path).stat().st_size > MAX_CONTAINER:
        raise RuntimeError('BIN превышает допустимый размер.')
    with Path(path).open('rb') as stream:
        header = stream.read(64)
    firmware = next((item for item in FIRMWARES.values()
                     if header[16:36] == item.pilot.encode()), None)
    if firmware is None:
        raise RuntimeError('Неподдерживаемая версия Pilot в BIN.')
    if firmware in (EXPERIMENTAL, STANDARD):
        try:
            data = Path(path).read_bytes()
            with zipfile.ZipFile(io.BytesIO(data[64:].translate(bytes(x ^ firmware.xor_key for x in range(256))))) as archive:
                bounded_zip(archive)
                if 'pilot/xreal-target.json' in archive.namelist():
                    target = json.loads(archive.read('pilot/xreal-target.json'))
                    if target == ONE_TARGET and firmware is EXPERIMENTAL:
                        firmware = ONE_EXPERIMENTAL
                    elif target == ONE_S_TARGET and firmware is STANDARD:
                        firmware = ONE_S_STANDARD
                    else:
                        raise RuntimeError('BIN предназначен для другой модели очков.')
        except (zipfile.BadZipFile, ValueError) as error:
            raise RuntimeError('Архив BIN повреждён.') from error
    _, expected = load_package(path, firmware)
    return (firmware, expected.build) if return_manifest else firmware


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
        result = probe.export('/usrdata/'+name, max_bytes=MAX_FILE)
        if result['start_status'] != 0 or result.get('finish_status') != 0:
            raise RuntimeError('Не удалось прочитать установленный файл: '+name)
        with zipfile.ZipFile(io.BytesIO(result['data'])) as z:
            matches = [i for i in z.infolist() if not i.is_dir() and i.filename.rsplit('/', 1)[-1] == name.rsplit('/', 1)[-1]]
            if (len(matches) != 1 or matches[0].file_size > MAX_FILE or
                    hashlib.sha256(z.read(matches[0])).hexdigest() != digest):
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


def verify_only(selected, emit, firmware=STANDARD, path=None):
    emit('Проверка выбранного пакета…', 0)
    _, expected = load_package(path, firmware=firmware)
    verify_selected(selected, expected, emit, firmware=firmware)
    emit('Готово. Все проверяемые файлы совпадают с прошивкой. Можно отключить очки.', 100)
    return {'operation': 'verify', 'package_sha256': expected.package_sha256,
            'build': expected.build, 'verified_files': dict(expected), 'firmware_writes': 0}


def update(selected, emit, probe_factory=SelectedProbe, package_loader=None, sleep=time.sleep,
           firmware=STANDARD, path=None):
    emit('Проверка выбранной прошивки…', 0)
    data, expected = (package_loader or (lambda: load_package(path, firmware=firmware)))()
    with probe_factory(selected) as probe:
        emit('Проверка модели и версии очков…', 0)
        before = identity(probe, firmware)
        abi = None
        if getattr(expected, 'build', None):
            abi = check_abi(probe, expected.build, emit)
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
    return {'operation': 'update', 'before': before, 'after': actual,
            'package_sha256': getattr(expected, 'package_sha256', None),
            'build': getattr(expected, 'build', None), 'abi_preflight': abi,
            'verified_files': dict(expected)}
