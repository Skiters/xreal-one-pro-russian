"""Read-only XREAL One Pro queries using the public XREAL OTA HID protocol."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import time
import zlib

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'work/usbdeps'))
import hid

ALLOWED = {21, 24, 38, 204, 207, 208, 209, 4627}


class Probe:
    def __enter__(self):
        info = next(x for x in hid.enumerate(0x3318, 0x0436)
                    if x['interface_number'] == 0 and x['usage_page'] == 0xff00)
        self.device = hid.device()
        self.device.open_path(info['path'])
        self.device.set_nonblocking(False)
        self.info = {k: v for k, v in info.items() if k not in ('path', 'serial_number')}
        return self

    def __exit__(self, *args):
        self.device.close()

    def query(self, command, payload=b'', timeout=4):
        assert command in ALLOWED
        assert len(payload) <= 490
        frame = bytearray(512)
        frame[0] = 0xfd
        struct.pack_into('<H', frame, 5, 17 + len(payload))
        struct.pack_into('<H', frame, 15, command)
        frame[22:22+len(payload)] = payload
        struct.pack_into('<I', frame, 1, zlib.crc32(frame[5:22+len(payload)]))
        written = self.device.write(b'\x00' + frame)
        # Windows HIDAPI pads to OutputReportByteLength and returns that padded
        # size (1025 on these glasses). Delivery is verified by the reply below.
        if written < len(frame) + 1:
            raise IOError(f'Incomplete HID write: {written}/{len(frame) + 1}')
        deadline = time.monotonic() + timeout
        ignored = 0
        while time.monotonic() < deadline:
            data = bytes(self.device.read(2048, max(1, int((deadline-time.monotonic())*1000))))
            if not data:
                continue
            if data[:2] == b'\x00\xfd':
                data = data[1:]
            if len(data) < 23 or data[0] != 0xfd:
                ignored += 1
                continue
            length = struct.unpack_from('<H', data, 5)[0]
            msg = struct.unpack_from('<H', data, 15)[0]
            if msg != command:
                ignored += 1
                continue
            if length < 18 or length+5 > len(data):
                raise ValueError(f'Invalid response length {length}/{len(data)}')
            crc = struct.unpack_from('<I', data, 1)[0]
            if crc != zlib.crc32(data[5:5+length]):
                raise ValueError('Response CRC32 mismatch')
            return dict(command=command, status=data[22], payload=data[23:5+length],
                        response_crc32_verified=True, ignored_reports=ignored)
        raise TimeoutError(f'No response to command 0x{command:04x}; ignored={ignored}')

    def export(self, remote_path, max_bytes=2*1024*1024):
        start = self.query(207, remote_path.encode(), timeout=180)
        result = dict(path=remote_path, start_status=start['status'],
                      start_payload_hex=start['payload'].hex())
        if start['status'] != 0:
            return result
        chunks = []
        total = 0
        deadline = time.monotonic() + max(45, max_bytes / 20000)
        try:
            if len(start['payload']) >= 8:
                announced = struct.unpack_from('<I', start['payload'], 4)[0]
                result['announced_bytes'] = announced
                if announced > max_bytes:
                    raise ValueError('Export exceeds size limit')
            for _ in range(max_bytes // 400 + 10000):
                if time.monotonic() >= deadline:
                    raise TimeoutError('Export deadline exceeded')
                part = self.query(208, timeout=4)
                if part['status'] not in (0, 250):
                    raise IOError(f"Unexpected export status {part['status']}")
                chunks.append(part['payload'])
                total += len(part['payload'])
                if total > max_bytes:
                    raise ValueError('Export exceeds size limit')
                if part['status'] != 250:
                    break
            else:
                raise ValueError('Export chunk limit reached')
            result['data'] = b''.join(chunks)
            result['bytes'] = total
            result['sha256'] = hashlib.sha256(result['data']).hexdigest()
        finally:
            result['finish_status'] = self.query(209)['status']
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--export', dest='remote_path')
    parser.add_argument('--max-bytes', type=int, default=2*1024*1024)
    args = parser.parse_args()
    output = BASE / 'outputs/xreal-usb-inspection'
    output.mkdir(parents=True, exist_ok=True)
    with Probe() as probe:
        if args.remote_path:
            if not 0 < args.max_bytes <= 32*1024*1024:
                parser.error('--max-bytes must be between 1 and 33554432')
            result = probe.export(args.remote_path, max_bytes=args.max_bytes)
            content = result.pop('data', None)
            stem = args.remote_path.strip('/').replace('/', '_')
            if content is not None:
                filename = stem + ('.zip' if content.startswith(b'PK') else '.bin')
                (output / filename).write_bytes(content)
                result['saved_file'] = filename
            (output / (stem + '.json')).write_text(json.dumps(result, indent=2)+'\n')
        else:
            result = dict(device=probe.info, versions=[])
            for label, command, payload in [('device_id',21,b''),('soc',38,b''),
                                            ('dsp',24,b''),('pilot',4627,b'\x02'),
                                            ('sku',204,b'')]:
                response = probe.query(command, payload)
                raw = response.pop('payload')
                response.update(label=label, payload_hex=raw.hex(),
                                text=raw.rstrip(b'\x00').decode('utf-8', errors='replace'))
                result['versions'].append(response)
            (output / 'device-info.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2))
