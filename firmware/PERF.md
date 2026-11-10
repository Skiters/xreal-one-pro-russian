# One Pro perf candidate (AI HDR and Real3D Pro+)

Experimental build for One Pro (`X1112010` / `X1112011`), Pilot 2.0. It is the
published, user-tested rc7 BIN with **one file replaced**:
`pilot/bin/libxreal_cpu_adapter.so` (plus its manifest and checksums). Menu,
OSD, translations, panel profile, configs and the vendor mesh are byte-identical
to rc7. CI checks this and fails the build otherwise.

## Why it was slow

Device facts (read-only over USB): 4× Cortex-A53 at 1.27 GHz, 512 KiB shared
L2, 4 KiB pages, no transparent huge pages. Adapter logs on an X1112011:

| Mode | CPU work per 1080p frame (median) | Frames/s (median) |
|---|---|---|
| AI HDR (rc7 dense LUT) | convert 26.1 ms | 35.7 |
| Real3D Pro+ (rc7) | warp 23.3 ms | 39.9 |
| Real3D One Pro mesh | warp 10.8 ms + flush 3.9 ms | 48.2 |

The AI HDR kernel reads a 4.25 MiB table at colour-dependent positions — about
8× the L2 — so most pixels miss the cache and the TLB.

## What changed

1. **AI HDR — compact kernel.** Only R is pre-expanded per (B,G) cube node:
   289 KiB, fits in L2. G/B are interpolated with exact 1/255 weights. Max error
   ≤ 1 RGB8 level against a floating trilinear oracle (same bound as rc7),
   including the real `lut_3d_ai_hdr.cube` and `lut_3d_movie.cube`. Identical
   consecutive 4-pixel groups (black bars, flat UI) reuse the previous result.
2. **AI HDR — on-device autotune.** After every LUT init the adapter alternates
   the rc7 and compact kernels for 24 frames each, keeps the faster by median
   time and frees the other table. Result line in `/usrdata/aihdr-cpu.log`:
   `CPU perf: autotune dense_median_us=… compact_median_us=… choice=…`.
   If the compact kernel is not faster on the glasses, rc7 behaviour remains.
3. **Real3D Pro+ — vectorized warp** from upstream commit `2689834`
   (byte-identical output, 41–69 % fewer ARM64 instructions).
4. **Compiler:** `-mcpu=cortex-a53` scheduling for the in-order core, with the
   A53 errata workarounds 835769 and 843419 enabled.

Instruction counts in CI are emulator counts, not A53 cycles or FPS. Real
speedup is only known after installation and reading the logs.

## Install / verify / logs (macOS, Linux, Windows; Python 3.9+)

```sh
python3 -m pip install hidapi==0.15.0
python3 installer/flash_cli.py check   XREAL-one-pro-Pilot2-RU-0.7.0-perfN.bin   # read-only
python3 installer/flash_cli.py install XREAL-one-pro-Pilot2-RU-0.7.0-perfN.bin --yes-write-firmware
python3 installer/flash_cli.py logs    ./device-logs                             # read-only
```

Rollback: install the rc7 BIN (`XREAL-one-pro-Pilot2-RU-0.7.0.bin`) the same
way. There is no automatic rollback. X1112011 on Pilot 2.0 remains an
experimental SKU in the upstream project.
