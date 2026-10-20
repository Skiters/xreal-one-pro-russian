"""Pure Pilot container validation. This module never opens a USB device."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import zipfile

MAX_CONTAINER = 160 * 1024 * 1024
MAX_FILE = 128 * 1024 * 1024  # Vendor ASUS OSD libraries are ~74 MB uncompressed.
MAX_UNPACKED = 384 * 1024 * 1024
MAX_ENTRIES = 1024
CHECKSUMS = 'pilot/checksums.sha256'
BUILD_INFO = 'pilot/xreal-build.json'


def container_crc(data):
    table = []
    for index in range(256):
        value = index << 24
        for _ in range(8):
            value = ((value << 1) ^ (0xf4acfb13 if value & 0x80000000 else 0)) & 0xffffffff
        table.append(value)
    value = 0
    for byte in memoryview(data)[8:]:
        value = ((value << 8) ^ table[((value >> 24) ^ byte) & 255]) & 0xffffffff
    return value


def canonical_name(name):
    clean = name[:-1] if name.endswith('/') else name
    if (not clean or '\0' in name or '\\' in name or ':' in name or
            any(ord(c) < 32 for c in name) or
            any(p in ('', '.', '..') for p in clean.split('/')) or
            PurePosixPath(clean).is_absolute() or
            not (clean == 'pilot' or clean.startswith('pilot/'))):
        raise ValueError('Недопустимый путь внутри BIN: ' + repr(name))
    return clean


def bounded_zip(archive):
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_UNPACKED:
        raise ValueError('Слишком большой архив внутри BIN.')
    seen = set()
    files = set()
    directories = set()
    for info in infos:
        name = canonical_name(info.filename)
        if name in seen or info.orig_filename != info.filename:
            raise ValueError('Повторяющийся или обрезанный путь внутри BIN.')
        seen.add(name)
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        allowed = (0, stat.S_IFDIR) if info.is_dir() else (0, stat.S_IFREG)
        if kind not in allowed or info.flag_bits & 1:
            raise ValueError('Ссылки, специальные и зашифрованные файлы в BIN запрещены.')
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError('Неподдерживаемый метод сжатия BIN.')
        if info.file_size > MAX_FILE or (info.is_dir() and info.file_size):
            raise ValueError('Превышен допустимый размер файла внутри BIN.')
        (directories if info.is_dir() else files).add(name)
    for name in files | directories:
        if any(str(parent) in files for parent in PurePosixPath(name).parents):
            raise ValueError('Файл используется как каталог внутри BIN.')


class VerifiedFiles(dict):
    """Hashes to read back, together with immutable-by-convention build provenance."""
    def __init__(self, values, build=None, package_sha256=None):
        super().__init__(values)
        self.build = build
        self.package_sha256 = package_sha256


def validate_elf(data, name):
    """Check target/segment bounds, not the safety or authenticity of machine code."""
    if (len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01' or
            int.from_bytes(data[16:18], 'little') not in (2, 3) or
            int.from_bytes(data[18:20], 'little') != 183):
        raise ValueError('Требуется Linux AArch64 ELF64: ' + name)
    offset = int.from_bytes(data[32:40], 'little')
    size, count = struct.unpack_from('<HH', data, 54)
    if size != 56 or not count or offset < 64 or offset + size*count > len(data):
        raise ValueError('Повреждены заголовки ELF: ' + name)
    loads = 0
    for index in range(count):
        kind, _, start, _, _, filesz, memsz, _ = struct.unpack_from(
            '<IIQQQQQQ', data, offset + index*size)
        if start > len(data) or filesz > len(data)-start or (kind == 1 and filesz > memsz):
            raise ValueError('Сегмент ELF выходит за границы файла: ' + name)
        loads += kind == 1
    if not loads:
        raise ValueError('Нет загружаемых сегментов ELF: ' + name)


def validate_config(data, name):
    # Preserve // inside JSON strings. Only the four active, strict JSON
    # configurations are checked here; some unused vendor models use JSON5.
    text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*',
                  lambda m: m[1] or '', data.decode('utf-8'))
    config = json.loads(text)
    # Model-specific files are overrides; the original Pilot 1.9 GF override
    # has no display section and inherits it from the main configuration.
    if (not isinstance(config, dict) or
            ('display' in config and not isinstance(config['display'], dict)) or
            (name.endswith('/pilot_default_config.json') and 'display' not in config)):
        raise ValueError('Нет секции display в конфигурации: ' + name)


def read_container(path, version, xor_key):
    path = Path(path)
    if path.stat().st_size > MAX_CONTAINER:
        raise ValueError('BIN превышает допустимый размер.')
    with path.open('rb') as handle:
        data = handle.read(MAX_CONTAINER + 1)
    if len(data) > MAX_CONTAINER or len(data) < 64:
        raise ValueError('Неверный размер BIN.')
    if (int.from_bytes(data[4:8], 'little') != len(data)-8 or
            int.from_bytes(data[8:12], 'little') != 0x1500 or
            int.from_bytes(data[12:16], 'little') != 4 or
            data[16:36] != version.encode()):
        raise ValueError('Неверный заголовок прошивки.')
    if container_crc(data) != int.from_bytes(data[:4], 'little'):
        raise ValueError('Неверная контрольная сумма контейнера BIN.')
    archive = zipfile.ZipFile(io.BytesIO(data[64:].translate(bytes(i ^ xor_key for i in range(256)))))
    try:
        bounded_zip(archive)
        # Inspect limits before decompression, including the checksum file.
        expected = {}
        for line in archive.read(CHECKSUMS).decode('ascii').splitlines():
            digest, relative = line.split(None, 1)
            name = 'pilot/' + relative.removeprefix('./')
            canonical_name(name)
            if not re.fullmatch('[0-9a-f]{64}', digest) or name in expected:
                raise ValueError('Неверная строка checksums.sha256.')
            expected[name] = digest
        actual = {i.filename for i in archive.infolist() if not i.is_dir()}
        if set(expected) != actual - {CHECKSUMS}:
            raise ValueError('Не все файлы BIN перечислены в checksums.sha256.')
        for name, digest in expected.items():
            payload = archive.read(name)
            if hashlib.sha256(payload).hexdigest() != digest:
                raise ValueError('Внутренняя контрольная сумма BIN не совпадает: ' + name)
            if name == 'pilot/bin/pilot' or (name.startswith('pilot/bin/') and name.endswith('.so')):
                validate_elf(payload, name)
            if name in {'pilot/conf/pilot_default_config.json',
                        'pilot/conf/pilot_default_config_gf.json',
                        'pilot/conf/pilot_default_config_gina.json',
                        'pilot/conf/pilot_default_config_hebe.json'}:
                validate_config(payload, name)
        expected[CHECKSUMS] = hashlib.sha256(archive.read(CHECKSUMS)).hexdigest()
        build = json.loads(archive.read(BUILD_INFO)) if BUILD_INFO in actual else None
        target = json.loads(archive.read('pilot/xreal-target.json')) if 'pilot/xreal-target.json' in actual else None
        return data, VerifiedFiles(expected, build, hashlib.sha256(data).hexdigest()), target
    finally:
        archive.close()
