"""Compatibility evidence for the CPU adapter ABI; not a claim of panel equivalence."""
import hashlib
import io
import json
import re
import zipfile

SOC = '15.1.03.603_20260914'
ABI_LIBRARIES = {
    '/usr/lib/libhal_sys.so': 'bd94123361b8941d4b5bc81c78737820c54e0336c8e92be0bdd202b74f13bef0',
    '/lib/libpthread-2.25.so': 'a926697f4380f782a859c670ff5372c490d8ba6c9b67c4f79553f53a71ef7e20',
    '/lib/libc-2.25.so': '9e17e7a1f88f7fd5767606dd9f75dd8e457599b2a02d9195155912ee87970db5',
    '/usr/lib/libmpi_npu.so': '57b9614a98cca727f1ce07b2eff93dde9c6e5793ad0b5e07a6a2e79c3830e19c',
    '/usr/lib/libmpi_dsp.so': 'f216ca134d631d8a62fd0859c8c3a92f379a0f84a2cf107dfafb02a4459d43e5',
}
PROFILES = {
    'one-pro-603': {'model': 'one-pro', 'pid': 0x0436, 'skus': ['X1112010'],
                    'panel_config': 'gina', 'hardware_evidence': 'one-device'},
    'one-603': {'model': 'one', 'pid': 0x0438, 'skus': [],
               'panel_config': 'gf', 'hardware_evidence': 'pending'},
}
MMZ_START, MMZ_END = 0x33000000, 0x40000000


def validate_build(build, firmware_key, pilot):
    if build is None:
        return  # Older custom packages retain their existing model/version checks.
    profile_id = 'one-603' if firmware_key == 'one-experimental' else 'one-pro-603'
    if (not isinstance(build, dict) or build.get('schema') != 2 or
            build.get('profile') != profile_id or build.get('pilot') != pilot or
            build.get('soc') != SOC or firmware_key == 'standard' or
            not isinstance(build.get('build_id'), str) or
            not re.fullmatch('[0-9a-f]{64}', build['build_id']) or
            build.get('abi_libraries') != ABI_LIBRARIES or
            build.get('mmz') != [MMZ_START, MMZ_END]):
        raise ValueError('Неподдерживаемый профиль или ABI в manifest BIN.')
    unsigned = {key: value for key, value in build.items() if key != 'build_id'}
    encoded = (json.dumps(unsigned, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode()
    if hashlib.sha256(encoded).hexdigest() != build['build_id']:
        raise ValueError('Build ID не совпадает с manifest BIN.')


def export_file(probe, path, maximum=8*1024*1024):
    response = probe.export(path, max_bytes=maximum)
    if response['start_status'] != 0 or response.get('finish_status') != 0:
        raise RuntimeError('Не удалось прочитать для проверки совместимости: '+path)
    with zipfile.ZipFile(io.BytesIO(response['data'])) as archive:
        matches = [i for i in archive.infolist()
                   if not i.is_dir() and i.filename.rsplit('/', 1)[-1] == path.rsplit('/', 1)[-1]]
        if len(matches) != 1 or matches[0].file_size > maximum:
            raise RuntimeError('Неожиданный ответ при чтении '+path)
        return archive.read(matches[0])


def check_abi(probe, build, emit=lambda *_: None):
    """Read-only preflight, before the first firmware-writing command."""
    observed = {}
    for index, (path, expected) in enumerate(ABI_LIBRARIES.items(), 1):
        emit(f'Проверка системных библиотек: {index} из {len(ABI_LIBRARIES)}', 0)
        actual = hashlib.sha256(export_file(probe, path)).hexdigest()
        observed[path] = actual
        if actual != expected:
            raise RuntimeError('Системная библиотека отличается от проверенного ABI: ' + path +
                               '. Запись не начата.')
    text = export_file(probe, '/proc/media-mem', 1024*1024).decode('ascii', errors='replace')
    ranges = [(int(a, 16), int(b, 16)+1) for a, b in re.findall(
        r'ZONE:\s*PHYS\((0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+)\)', text)]
    if not any(a == MMZ_START and b == MMZ_END for a, b in ranges):
        raise RuntimeError('Карта видеопамяти отличается от проверенного профиля. Запись не начата.')
    return {'libraries': observed, 'mmz': [MMZ_START, MMZ_END], 'profile': build['profile']}
