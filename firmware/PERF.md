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

## perf2 (after the first on-device measurement)

Measured on X1112011 with perf1/EXP144: AI HDR autotune chose compact
(dense 22.4 ms vs compact 14.4 ms per frame); after tuning median 17.1 ms,
50.6 frames/s (was 26.1 ms, 35.7). Real3D Pro+ warp 10.7 ms, 54 frames/s
(was 23.3 ms, 39.9).

- **AI HDR compact2:** same values and arithmetic as compact (bit-identical),
  table laid out [b][r][g] so G and G+1 neighbours share a cache line
  (2 lines per pixel instead of 4); 8-pixel body issues all table loads
  before arithmetic for the in-order A53. Autotune now picks the fastest of
  dense / compact / compact2 (20 frames each) and logs all three medians.
- **Real3D Pro+ staircase fix:** the 480x270 depth map gave one displacement
  per 4-row band, so slanted object edges showed a staircase (absent in the
  native One Pro mesh). Each output row now blends its displacement with the
  neighbouring depth row by distance to the row centres. Vertically constant
  depth gives byte-identical output to rc7; an independent per-pixel oracle
  covers luma/chroma, Q2–Q4 and worker partitions.
- **144 Hz:** in Pilot 2.0 OSD «Игра — 144 Гц» is the 5th *display mode* item,
  not a quick-button function; the OSD hides it on One Pro without asking the
  controller. The EXP144 hot-key edit is therefore inert.

## Experimental variant `-EXP144` (opt-in)

Same adapter, plus three exact, asserted edits (listed in the BIN manifest):

1. **Display mode «Игровой» instead of «Насыщенный».** One instruction in
   `libnr_osd.so` (0xf2048, `mov w7,#2` → `mov w7,#5`): the Vivid row of the
   display-mode list now selects the vendor's hidden *game* mode, which
   requests EDID `1920_1080_144` and maps it to a 144 Hz OLED timing. Pilot's
   separate overclock function is a stub that always reports "unsupported",
   so this is the only route. Whether the One Pro panel runs 144 Hz is
   **unverified**; the OSD has a vendor fallback screen for the game mode.
2. The row is renamed in both Russian columns of `osd_language.csv`
   («Насыщенный» → «Игровой»), the same technique as the Russian-for-Polish
   swap. Vivid mode is not reachable in this variant; Movie is unchanged.
3. AI HDR and Reading request **60 Hz** instead of 90 Hz from the source,
   because their frames are processed on the CPU.

Not possible in this firmware: a "Sound Effects" menu (the OSD has no such
screen; Pilot's audio algorithms are for microphone recording; the speaker
tuning lives in the glasses controller). If the picture is lost, USB
installation still works without video: reinstall the plain perf BIN or rc7.
