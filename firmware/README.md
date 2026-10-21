# Pilot adapters and model packages — 0.7.0

One Pro release payload is the exact user-confirmed rc7 BIN. One remains an experimental Pilot port. 1S uses stock Pilot 1.9.1 with language resources only. These are not new SoC/DSP firmwares.
Closed XREAL binaries are pinned inputs. The reproducible part is our adapter
and deterministic repackaging of those inputs.

## What changed

- RGB processing uses the complete 17³ LUT, including interactions between
  channels. R/G interpolation is cached in 17 blue slices (4.25 MiB); each pixel
  requires two lookups. Intermediate Q2 precision differs from the floating
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

Host tests cannot establish performance on the glasses. Stable Real3D 60 fps
is an acceptance target, not a result of this release.

## Pinned inputs

| Input | SHA256 |
|---|---|
| Hash-pinned build input `XREAL-Pilot2-RU-Hebe-Real3D-CPU.bin` | `1b72fc6acd90fc9b8995644cca659907d571292f19a19a33cdbddd59da5decd0` |
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
captures the core libraries and a local ABI report from one connected device,
without firmware-write commands. Keep device captures outside Git.

```powershell
python firmware/build.py --clang C:/toolchain/bin/clang.exe `
  --sysroot C:/xreal-inputs/sysroot --stock C:/xreal-inputs/pilot-pro-plus.zip --model one
```

The command above builds only One. For One Pro, use a separate output directory with `--compare-onepro-warp`; also provide the pinned mesh library through `--onepro-mesh` and libdl in the sysroot. Prepare the 1S package as below, then stage all three model BINs and a combined build-report.json before running `installer/build.ps1` (see [installer build](../installer/README.md)). `--compare-onepro-warp` builds the dual-row One Pro comparison. The output includes model-specific BINs, the adapter ELF and a build
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
`outputs/v<VERSION>/libxreal_cpu_adapter.so` using `installer/version.py`. The tests execute the ARM64
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

1. Installer preflight: PID, SKU, SoC, Pilot, required system library hashes and
   MMZ range match. Read back every packaged file after writing.
2. SDR, AI HDR, Reading and Movie each show correct colors and can return to
   Standard. Repeat mode transitions and initialization/close ten times.
3. Real3D has visible depth on the same scene, correct colored edges and
   no stale frames. Use movement as well as a static scene.
4. Capture logs for at least 60 seconds per mode. `measure.py` reports sampled
   callback throughput and stage times; separately inspect NPU/queue delays,
   display frame pacing, temperature and end-to-end latency. A 60 Hz EDID
   entry does not establish 60 processed frames/s.
5. Record these results for the relevant model. Current One Pro user confirmation
   does not establish physical One compatibility or measured throughput.

No automatic firmware rollback is implemented. A failure produces a report;
subsequent fixes require another candidate, not an implicit downgrade.

## 1S translation only

Run `python firmware/build_1s.py --stock <official-1s.bin> --reference <original-pilot.zip> --translation <approved-russian-1.9.1.bin> --output outputs/v0.7.0/XREAL-1S-RU-Pilot1.9.1.bin`.

All three inputs are hash-pinned. The original 1S Pilot must match every file of the reference stock ZIP. Only the CSV, two TTFs and embedded-font OSD are copied from the approved translation. Checksums and the 1S target descriptor are regenerated; every other vendor file is retained byte-for-byte. The builder never accesses USB. See [compatibility evidence](../installer/compatibility.md).

For the 0.7.0 release the One Pro asset is a copy of rc7, SHA256 `721e08d0828e116ad18d26168d43a5fc9788ba3ce52e0b34a7c234159b5bde43`; internal rc7 provenance is retained. Rebuilding with current version/source provenance produces different bytes and must not be described as the same hardware-tested payload.

## Local UI performance candidate (One Pro only)

Add `--ui-fast --model one-pro --compare-onepro-warp` to the pinned build
command and use a separate output directory. The BIN name ends with
`-UI-FAST-LOCAL`; this does not replace the public firmware asset.

The opt-in changes are:

1. Change the exact rc7 OSD refresh and animation timers from 30 to 16 ms;
   halve screen-load transition durations. Input polling and other LVGL
   timers are unchanged. A 16 ms timer is a scheduling target, not measured FPS.
2. Disable normal action/interface logs and sample HDR processing times every
   1800 instead of 30 frames. Error paths and initialization logs remain.
3. Cache Real3D row geometry across unchanged highlights. List, widget, mode
   or availability changes invalidate it; native registration forces layout.
4. Reduce the first boot wait from 1 to 0.25 seconds. Preserve the separate
   7-second video-readiness timeout, watchdog and onboarding.
5. Omit the two ASUS OSD libraries and `resource_asus/` from this model's BIN.
   Neither library was mapped by the running One Pro Pilot process. OTA may
   retain old unlisted files; package reduction does not prove RAM savings.

`ui_fast.py` checks the entire input OSD hash and each patched instruction.
The candidate retains the current panel, EDID, algorithms and translations.
The 2026-10-21 candidate is 73,347,963 bytes versus 99,742,055 bytes for the
same build without package pruning (about 26.5% less). Expected readback: 54
files. Physical boot time, menu smoothness, CPU cost and every operating mode
still require testing on a video source. The front USB connection alone cannot
validate display performance.

Validation commands (after the normal build inputs are available):

```text
python firmware/tests/test_ui_fast.py <UI-FAST-LOCAL.bin>
python firmware/tests/test_compare_ui.py <libxreal_cpu_adapter.so>
```

The first test uses local baseline evidence at `outputs/ui-performance-audit/`
and `outputs/ui-fast-test/device-before/`; these device artifacts are not
published. It executes the patched ARM64 instructions and verifies config scope,
model rejection, unknown-ELF rejection and the complete package checksums.
The second executes native row construction and tests cache invalidation and
forced geometry repair after navigation registration. Frame-bound regression
checks also passed with `REAL3D_LIB` pointing at the candidate adapter.

Installed on One Pro X1112010 on 2026-10-21. All 54 files were read back and
matched after USB export retries following restart; no second firmware write
was needed. The new Pilot process mapped the adapter and OSD. Build ID:
`ed6357d6311279df4ec5da9510f9a9d24d06712248cfd0afd0b6ef7b1d51ed10`.
BIN SHA256: `81a1e330aa99da478eb995520f6d92e877af22e8271dacfab01eab4b6933324a`.
A full pre-update Pilot directory backup and install/readback reports are kept
locally under `outputs/ui-fast-test/`. This is installation verification;
visual/performance acceptance is still pending. Public release BINs are unchanged.
