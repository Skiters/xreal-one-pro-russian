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

## perf16 (Real3D edge-aware depth)

Where the 480x270 displacement jumps by more than 1/2 pixel around a cell,
each luma pixel mixes the 2x2 nearest cells' raw displacements with a
spatial (bilinear) weight times a range weight from how close its source
luma is to each cell's centre luma (Gaussian, sigma 12 levels): joint
bilateral upsampling, so the depth boundary follows the object contour
instead of a ramp across foreground and background (halo/stretch around
silhouettes). Elsewhere the perf2/perf5 smoothing is unchanged. Chroma keeps
the vertical blend. Oracle in `test_real3d_cubic.py` (incl. a bright object
on a dark background).

## perf15 (Real3D sharpness + joint chroma)

From the external audit (REAL3D-AUDIT-2026-11-12.md). 3D frame rate is
bounded upstream (~48/s, depth/NPU), so warp time is spent on quality:

- **Luma 4-tap Catmull–Rom** for fractional source positions (Q8 weights per
  Q5 phase, rows sum to 256), clamped to the two centre samples (no halo on
  subtitles/hard edges). Integer positions are still copied. Shared-phase
  cells use NEON (8-byte window, 4 widened taps, int32 accumulate). At a
  half-pixel shift linear keeps ~71% of a period-4 pattern's contrast,
  Catmull–Rom ~88% (before the clamp).
- **U and V warped in one pass** sharing the geometry: byte-identical to two
  passes, ~25% fewer geometry-cell iterations.
- `test_real3d_cubic.py`: independent per-pixel oracle for the cubic luma
  warp and byte equality of the joint chroma pass; NEON equals portable.

## perf14

perf13 on the device: AI HDR at the 90 Hz source, unpaced: ~43 frames/s
(p10 38, p90 49), call gaps all 14–30 ms, convert ~21 ms; the owner found it
livelier. perf14 only adds a whole-call stage breakdown every 30 frames
(`CPU perf14 stages_us map= convert= flush= unmap= result= total= gap= idle=`)
because the convert trace hides map + cache invalidate, unmap and the result
write (suggested by the external audit, AI-HDR-AUDIT-2026-11-12.md).

## perf13

perf12 on the device: tolerant repeat detection worked (up to 78% repeats on
a film), but the 6 MB input/output copies made each frame 26–45 ms instead of
~18 on the A53 memory bus. Meanwhile Movie (also our CPU adapter, ~15 ms,
rc7's 90 Hz source, unpaced) looked smooth to the owner, while AI HDR at a
60 Hz source paced to an even 30/s looked stalled: film smoothness depends on
how often a fresh source frame is taken, not on an even cadence.

- AI HDR and Reading keep rc7's 90 Hz source (`--keep90`, variant `-UI90`).
- AI HDR pacing and repeat reuse disabled (code kept); compact16 only.
- Real3D unchanged (unpaced since perf11).

## perf12

perf11 on the device: 0 exact repeats while playing a film, AI HDR paced at
an even ~29.8/s, convert 22–24 ms including the row hashes. macOS temporally
dithers 8-bit external outputs, so repeated frames differ by +-1. perf12
compares each frame with the previous input with a tolerance of 2 levels per
pixel (NEON abs-diff, stops at the first larger difference) instead of exact
hashes; a repeat gets the kept output (within ~2-3 levels of a fresh
conversion). The test now also checks that +-1 noise is served from the copy.

## perf11

- **Repeated-frame reuse (AI HDR):** a 24 fps film in a 60 Hz signal repeats
  frames 3:2 (60% exact repeats). Every frame's rows are hashed (64-bit per
  row and plane, every pixel, parallel); if all equal the previous frame's,
  the kept output copy is written instead of re-converting. Copies are kept
  only while >= 10% of the last 120 frames repeat. Goal: every source frame
  served, so film keeps its 3:2 cadence instead of 2-1-1-1 at an even 30.
- Diagnostics every 300 frames: repeats, served from copy, and a histogram of
  call-to-call gaps (<14, 14-20, 20-30, 30-36, 36-50, >50 ms).
- **Real3D pacing removed:** on the device even 30/s raised Pilot's synth
  latency from ~70 ms to 100–177 ms (frames queue while the call sleeps),
  and head-locked 3D lagged and jumped. 3D returns as soon as the warp ends.
- `test_repeat_reuse.py`: repeated frames byte-identical to a fresh
  conversion; a single changed pixel is never served from the copy.

## perf10

perf9 on the device: compact16 ran 60.0 frames/s at ~11 ms on lighter
scenes; on heavier ones (15–22 ms) pacing switched on but the rate stayed
45–57 because the wait was measured from the previous call's start (only
every other frame slept). Both AI HDR and Real3D now measure from the
current call's start. blocks v2 lost on the device (23.2 vs 13.1 ms); AI HDR
uses compact16 without tuning.

## perf9

- **blocks v2 (NEON):** two 4x2 tiles per step; smoothness via vector
  min/max, the four block means go through one compact16 group, offsets are
  added with saturating vector adds. Byte-identical to v1 (checked against
  the portable build). perf7's scalar v1 lost on the device (39 ms vs 20.9);
  on an M2 v2 is 36% faster than compact16 on smooth content, 11% slower on
  grain. Autotune still decides.
- **AI HDR adaptive pacing:** EMA of the per-frame busy time; above 15.8 ms
  the adapter paces to every second 60 Hz frame (even 30/s), below 13.8 ms it
  returns to every frame. Logged as `perf9 pacing ...`.
- Colour grade 70% (brightness change 35%); 80% looked over-saturated.

## perf7 / perf8

- **AI HDR `blocks` kernel:** compact16 plus smooth 2x2 reuse
  (out = LUT(mean) + pixel offset) on 4x2 tiles where every channel is within
  4 levels; within 2 levels of the float reference (1 on Movie), identity
  exact, NEON equals portable. ~95–100% of tiles are smooth on film-like
  synthetic content, 0% on grain/noise (exact path). Autotune compact16 vs
  blocks; smooth-tile share logged every 300 frames.
- **Flinger `synth_stereo_60fps=false`** (via `conf/sdk_global.json`, which
  Pilot copies to `/tmp`): parsed on device, but 3D stayed ~48 frames/s on the
  90 Hz OLED; only the target offset moved. Builds now ship the default.
- **Real3D frame pacing (perf8):** 48 frames/s on a 90 Hz panel mixes 1 and 2
  refreshes per frame. The Process RPC now returns no earlier than 32.8 ms
  after the previous call started, so 2D->3D locks onto every second 60 Hz
  source frame: 30 frames/s, each shown 3 refreshes. Uses `nanosleep`
  (added to `abi_imports.json`). Logged as `Real3D CPU pace ...`.

## Variant `-CINEMA30-UI` (does NOT work on One Pro)

Tested on X1112011: selecting AI HDR made Pilot request EDID 11
(`1920_1080_30`) and the BSP refused it (`NRDpSetCurrentEdidAndAudioBsp
failed, edid=11`, `SetDisplayDynamicRangeType ret=5001`), so the mode did not
switch. The config entry exists but the One Pro DP receiver lacks the timing.
CI now builds the re-graded cube on top of `-HDR60-UI` instead.

### Original rationale

At a 60 Hz source the CPU path processes ~48–50 frames/s, so Pilot skips
~10 source frames per second at irregular points and motion looks uneven.
This variant makes AI HDR and 2D->3D (Real3D, both algorithms) request EDID
`1920_1080_30`; Pilot's `dp_oled_map` shows it on the 90 Hz OLED timing, each
frame exactly 3 times (Reading stays 60 Hz, Movie rc7). With ~18 ms (AI HDR) / ~15 ms (Real3D) per frame and a
33 ms budget no source frame is skipped. 24 fps film then uses the source's
30 Hz cadence (1-1-1-2) instead of an irregular one. Everything else as
`-HDR60-UI`.

The CI build also re-grades `lut_3d_ai_hdr.cube` (`--hdr-strength 60
--hdr-tone 35`), because the full table looked over-bright to the owner:
each node's vendor change is split into Rec.709 luma and chroma; 35% of the
brightness change and 60% of the colour change are kept, skin-like hues get
half of that colour boost (soft mask), and the violet tint the vendor cube
puts on dark greys (grey 16 -> 10/8/12) is removed. Result: mid grey 128 ->
136 (vendor 150), grey 32 -> 28 (vendor 19, tinted), greys neutral and
monotonic, black 0, no non-monotonic steps, smoother than the vendor cube.
The adapter logs `LUT fnv1a32` at init to show which cube Pilot loaded.

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
