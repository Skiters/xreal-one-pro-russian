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

## perf3

Measured on X1112011 with perf2: autotune dense 33.0 / compact 29.4 /
compact2 34.7 ms, steady convert ~20.8 ms, ~43–44 frames/s. compact2 lost on
the real A53, and the same compact kernel ran slower than in the perf1 run
(14.4 ms in autotune), which points at core contention or throttling.

- **Dynamic row scheduling** (AI HDR and Real3D): 24-row (AI HDR) / 6 depth
  row (Real3D) bands taken from a shared counter instead of fixed quarters,
  so a core busy with Pilot's own threads no longer holds up the frame.
- **AI HDR:** autotune now compares compact vs compact with table prefetch
  (same table, bit-identical output); dense is built only if the compact
  table cannot be, compact2 is no longer used. Log line `CPU perf3: autotune
  …`; every 300 frames the log also records `cpu0 kHz` and `thermal0 mC`.
- **Real3D Pro+ bilinear displacement:** besides the vertical blend, each
  pixel blends with the neighbouring depth column by distance to the cell
  centres, so slanted edges no longer step in 4-pixel cells. Constant depth
  gives byte-identical output; an independent per-pixel oracle covers it.

## perf4

- **AI HDR tetrahedral kernel** as a third autotune candidate: the 17^3 cube
  itself (19.7 KiB, fits each core's L1) instead of the 289 KiB compact table
  in the shared L2; four nodes per pixel, weights from the sorted fractions,
  exact integer arithmetic (one rounding). Not bit-identical to trilinear: on
  the vendor AI HDR cube 75.4% of colours are equal, 24.6% differ by 1 level,
  0.04% by 2, 0.001% by 3 (saturated violet); Movie stays within 1 level.
  Log line `CPU perf4: autotune compact… compact_pf… tetra… choice=…`.

## perf5

Measured with perf4/HDR60 on X1112011: AI HDR on video 18.5 ms, 48.5 fps
(perf2: 20.8 ms, 43–44); autotune on video compact 23.4 / compact_pf 33.7 /
tetra 33.4 ms. Real3D: staircase gone, but warp 20–30 ms, ~39 fps (perf1:
10.7 ms, 54) because most cells took the per-pixel bilinear path.

- **Real3D:** a cell keeps the shared fast path when both neighbours are
  within 1/8 pixel of its displacement (deviation from full bilinear
  <= 0.05 px); other cells use an unchecked per-pixel path, with bounds
  decided once per cell. Oracle mirrors the threshold; ramp case added.
- **AI HDR:** no autotune, always compact (dense only as fallback), so the
  first seconds no longer run the slower kernels.
- **Diagnostic:** a sparse input-frame signature counts repeated source
  frames (`perf5 repeated frames per 300`) and its own cost; this decides
  whether skipping repeated frames is worth implementing.

## perf6

Measured with perf5: the source repeats almost no frames (0 per 300 after
the first seconds), so frame skipping is pointless, and the sparse signature
itself cost 2.5–5 ms per frame (AI HDR 41.5 fps vs 48.5 on perf4). Real3D
recovered to 18.7 ms, ~47 fps (perf4: ~24 ms, 39).

- **AI HDR:** signature removed. New **compact16** kernel: same table and
  lookups, channel extraction by narrowing shifts and 16-bit lerps with
  SQRDMULH over 8 lanes (both B rows at once); within 1 level of the cube,
  identity exact, NEON output equals the portable path. Short autotune
  (10 frames each) compact vs compact16, log `CPU perf6: autotune …`.
- **Real3D:** horizontal (bilinear) blending only on luma; chroma keeps the
  vertical blend and the shared cell path. Luma per-pixel cells gather with
  one 16-byte load + TBL. Destination planes are mapped without the cache
  invalidate (fully overwritten, 64-byte aligned lines, flushed after).

## Variant `-CINEMA30-UI` (opt-in, One Pro, movie-first)

At a 60 Hz source the CPU path processes ~48–50 frames/s, so Pilot skips
~10 source frames per second at irregular points and motion looks uneven.
This variant makes AI HDR and 2D->3D (Real3D, both algorithms) request EDID
`1920_1080_30`; Pilot's `dp_oled_map` shows it on the 90 Hz OLED timing, each
frame exactly 3 times (Reading stays 60 Hz, Movie rc7). With ~18 ms (AI HDR) / ~15 ms (Real3D) per frame and a
33 ms budget no source frame is skipped. 24 fps film then uses the source's
30 Hz cadence (1-1-1-2) instead of an irregular one. Everything else as
`-HDR60-UI`.

The CI build also passes `--hdr-strength 60`: `lut_3d_ai_hdr.cube` is blended
with the identity at 60% (mid grey 128 -> 142 instead of 150, less
saturation), because the full table looked over-bright to the owner. The
adapter logs `LUT fnv1a32` at init to show which cube Pilot loaded.

## Variant `-HDR60-UI` (opt-in, One Pro)

Adds upstream's UI experiment (`firmware/ui_fast.py`): exact rc7 OSD refresh
and animation timers 30 -> 16 ms, OSD action/interface logs off, first boot
wait 1.0 -> 0.25 s, unused ASUS OSD modules removed. The adapter is built
without `XREAL_UI_FAST`, so HDR timing is still logged every 30 frames.

## Variant `-HDR60` (superseded by `-HDR60-UI`)

Same adapter; AI HDR and Reading request **60 Hz** instead of 90 Hz from the
source, because their frames are processed on the CPU. The display-mode menu
is unchanged rc7 («Насыщенный» is back): the 144 Hz game-mode route was
removed because the One Pro OSD hides that mode.

Not possible in this firmware: a "Sound Effects" menu (the OSD has no such
screen; Pilot's audio algorithms are for microphone recording; the speaker
tuning lives in the glasses controller). If the picture is lost, USB
installation still works without video: reinstall the plain perf BIN or rc7.
