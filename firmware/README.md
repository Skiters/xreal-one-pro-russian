# Pilot 2 CPU adapter — 0.7.0-rc1

This is a candidate port of the Pilot component, not a new SoC/DSP firmware.
Closed XREAL binaries are pinned inputs. The reproducible part is our adapter
and deterministic repackaging of those inputs.

## What changed

- RGB processing uses the complete 17³ LUT, including interactions between
  channels. R/G interpolation is cached in 17 blue slices (8.5 MiB); each pixel
  requires two lookups. Intermediate Q4 precision differs from the floating
  reference by at most one RGB8 level in the included tests.
- Real3D uses the recovered mode-2 I420 ABI explicitly. A chroma sample moves
  half as far as a luma pixel. Fractional chroma positions are interpolated.
  Row padding is not used to infer side-by-side layout.
- Three persistent workers plus the calling thread replace per-frame thread
  creation. One mutex serializes RPCs, initialization and teardown. No worker
  can outlive its frame's mapped buffers.
- Rejected layouts and overlapping buffers are rejected before processing.
  A rejected result pointer is never written.
- One uses vendor GF panel configuration, with AI HDR panel drive equal to
  that model's SDR drive. One Pro retains the previously tested Hebe/Gina
  profile with `luminance` capped at 500. Neither number is a measured nit value.
- Both packages preserve the baseline's 90 Hz default and 60 Hz synthetic
  stereo setting. They do not advertise 133/240 Hz, change EDID, or enable
  overclocking. These are separate unresolved experiments.

Full LUT fidelity may cost more CPU time than the old diagonal approximation.
Host tests cannot establish performance on the glasses. Stable Real3D 60 fps
is an acceptance target, not a result of this release.

## Pinned inputs

| Input | SHA256 |
|---|---|
| Published v0.5.0 `XREAL-Pilot2-RU-Hebe-Real3D-CPU.bin` | `1b72fc6acd90fc9b8995644cca659907d571292f19a19a33cdbddd59da5decd0` |
| Original vendor `pilot-pro-plus.zip` | `c052575b989c9811c07563bf61ff3360c32a275bc3bb4dd426e211e94525759b` |

The base BIN is retained under `outputs/xreal-pilot2-real3d-cpu/`. Obtain the
exact vendor ZIP from your retained OTA download; a newer ZIP is not an
equivalent input. `build.py` rejects a hash mismatch.

Use Clang/LLD 22.1.3. Supply a directory containing `libhal_sys.so`,
`libpthread.so.0`, and `libc.so.6` exported read-only from the compatible
SoC 15.1.03.603 system. Their target hashes are in `installer/profiles.py`.
No guessed system library, alternate compiler ABI, or host libc is accepted.
The builder does not access USB or write to a device.

`python firmware/import_device_abi.py --output C:/xreal-inputs/sysroot`
captures these libraries and a local ABI report from one connected device,
without firmware-write commands. Keep device captures outside Git.

```powershell
python firmware/build.py --clang C:/toolchain/bin/clang.exe `
  --sysroot C:/xreal-inputs/sysroot --stock C:/xreal-inputs/pilot-pro-plus.zip
powershell -ExecutionPolicy Bypass -File installer/build.ps1
```

The output includes two model-specific BINs, the adapter ELF and a build
report. Every BIN contains source hashes, compiler identity, input hashes,
profile, ABI constraints and build ID. ZIP entries have fixed timestamps
and ordering. Use the same Python/zlib and compiler to reproduce bytes.
EXE assembly uses PyInstaller and is verified separately; byte-for-byte
reproducibility of the Windows PE is not claimed.

## Tests

Python dependencies for ARM64 emulation are pinned in `test-requirements.txt`.

```text
python -m pip install -r firmware/test-requirements.txt
python -m unittest discover -s installer -p test_*.py
python firmware/tests/test_geometry.py
python firmware/tests/test_frame_bounds.py
```

`REAL3D_LIB` may point to another newly built adapter. The default is
`outputs/v0.7.0-rc1/libxreal_cpu_adapter.so`. The tests execute the ARM64
functions in Unicorn and compare them to an independent displacement oracle.

Native Linux/WSL tests of numerical accuracy and worker lifetime:

```sh
gcc -O2 -shared -fPIC firmware/lut_fast.c -o /tmp/xreal-lut.so
python3 firmware/tests/test_lut.py /tmp/xreal-lut.so --bin outputs/xreal-pilot2-real3d-cpu/XREAL-Pilot2-RU-Hebe-Real3D-CPU.bin
gcc -O2 -pthread firmware/runtime.c firmware/tests/runtime_test.c -o /tmp/xreal-runtime
/tmp/xreal-runtime
gcc -O2 -shared -fPIC -pthread -Ifirmware firmware/cpu_adapter.c firmware/lut_fast.c firmware/runtime.c work/3d-lab/real3d_cpu.c firmware/tests/hal_stub.c -o /tmp/xreal-rpc.so
python3 firmware/tests/test_rpc_failures.py /tmp/xreal-rpc.so
```

These host tests do not validate the target's HAL or optics.

## Physical acceptance, separately for each model

1. Installer preflight: PID, SKU, SoC, Pilot, five system library hashes and
   MMZ range match. Read back every packaged file after writing.
2. SDR, AI HDR, Reading and Movie each show correct colors and can return to
   Standard. Repeat mode transitions and initialization/close ten times.
3. Real3D has visible depth on the same scene, correct colored edges and
   no stale frames. Use movement as well as a static scene.
4. Capture logs for at least 60 seconds per mode. `measure.py` reports sampled
   callback throughput and stage times; separately inspect NPU/queue delays,
   display frame pacing, temperature and end-to-end latency. A 60 Hz EDID
   entry does not establish 60 processed frames/s.
5. Promote from candidate only after these results are recorded for the
   relevant model. One Pro results never count as a physical One test.

No automatic firmware rollback is implemented. A failure produces a report;
subsequent fixes require another candidate, not an implicit downgrade.
