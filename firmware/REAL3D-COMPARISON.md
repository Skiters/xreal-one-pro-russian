> Обновление 21 октября 2026: пользователь подтвердил нормальную работу итоговой rc7 на One Pro. Публичная 0.7.0 использует этот BIN без изменений. Ниже сохранён ход исследований и прежние ограничения; он не заменяет [актуальные условия выпуска](../README.md).

# Local Real3D comparison: rc7

This test adds two selectable **mesh/warp implementations** on One Pro
X1112010 / SoC 15.1.03.603_20260914. It does not transplant the complete
Pilot 1.9.1 depth-inference stack. Both choices retain Pilot 2.0's depth
estimator, model, frame scheduler and display routing.

The candidate is intended to display two separate rows in the **3D mode list**:

- **Real3D One Pro** executes the unchanged ARM64 mesh kernel from the
  original One Pro Pilot 1.9.1 `libnr_depth_manager.so`.
- **Real3D Pro+** retains the current custom CPU implementation used by
  our Pilot 2 port. It is not the native Pro+ CEVA DSP implementation.

The ordinary 2D, full SBS and half SBS entries remain available. The list
has five items, including a separate native LVGL row and navigation callback
for each Real3D algorithm. Both open the native Real3D settings.

**Depth/strength remains a separate four-level native setting.** Its original
getter, setter, slider range and translations are unchanged. Selecting a
Real3D algorithm never sets depth to Natural or any other value. Both use the
current depth setting; they do not have separate per-algorithm depth presets.
Algorithm selection is saved atomically in `/usrdata/real3d-algorithm`.
The default without a saved selection is the current Pro+ CPU port.
Since the two mesh algorithms use different geometry and view ordering, equal
depth input does not imply identical perceived depth. rc6 halves the legacy
kernel's per-eye displacement: it shifts both views by opposite amounts,
whereas the CPU port shifts only one view. This avoids the legacy branch's
previous doubled separation; it does not prove that all ghosting is resolved.

rc4 incorrectly reused the strength slider as an algorithm selector. The
user tested it and requested independent mode entries and depth adjustment;
rc5 removed that substitution, but the user reported that the two entries
still appeared in one row, together with displaced ghost images and Movie lag.
Its host test had mocked out the native registration's repositioning step.
rc6 applies layout after that step and after selection refresh. The updated
regression test fails on the shipped rc5 adapter with both Real3D rows at y=94,
and passes on rc6 with five non-overlapping rows inside the 410-pixel viewport.
Final appearance and navigation still require a physical menu check.
rc7 also removes the exact-five-item guard: native navigation can omit hidden
SBS modes depending on the input. The previous guard then skipped layout
entirely, leaving the cloned and original rows at the same position. A second
regression models that three-item list, fails on rc6 and passes on rc7.

For Movie/AI HDR, rc6 retains the full cross-channel LUT. It halves its storage
from 8,912,896 to 4,457,472 bytes using Q2 entries and processes four pixels
at once with vector arithmetic. A 256-entry mapping replaces per-pixel blue
coordinate division. Independent tests of both shipped preset cubes and
random cubes stayed within one RGB8 level of a floating-point trilinear oracle;
identity remains exact. Host timings improved on random frames; gradient and
flat-frame results varied with compiler optimization (some became slower).
This is a candidate optimization, not an on-device FPS claim.
The previously exported device log showed approximately 20–38 ms in the color
conversion stage, with much smaller cache-flush time; sessions do not identify
which preset was selected, so these samples cannot be attributed solely to Movie.

## Vendor evidence

The unmodified One Pro library has SHA256
`ce310b124ab82b909ef4b3a8264a6ba3c0b05a724a21bf6ebd8492f022d99e3d`.

Relevant virtual addresses:

- `0x36d28..0x36d88`: caller supplies source/destination planes, strides,
  dimensions and `DepthFrame`.
- `0x3b870` → `0x3a848`: parallel wrapper, `DepthFrame` starts with a 96-byte
  OpenCV Mat header; a float at `+0x60` bounds the mesh edge region.
- `0x38150`: the original 120-byte lambda/kernel.
- `0x3867c..0x386a0`: the kernel writes one displaced view **into source**
  while constructing the opposite view in destination.
- `0x1ee2c0`: reciprocal lookup table initialized by the original wrapper.

The bridge loads a private copy named `libxreal_onepro_mesh.so`, validates
the entry signature, initializes the same table, and dispatches the original
kernel on four row segments. It never registers the old `DepthManager`
plugin or replaces the Pilot 2 `ImagePostProcessor` interface.

Pilot 2 supplies unsigned Q2/Q3/Q4 displacement. The old kernel consumes
integer per-eye displacement: rc6 divides by two and truncates the fractional part.
It does not multiply disparity or rescale it by each frame's extrema.
Both source and destination planes must be flushed after all workers finish.
The kernel's per-thread scratch vector remains managed by its own TLS code.

The OSD patch is restricted to a hash-pinned input. A small trampoline replaces
the GNU build-ID note; its program-header entry becomes a new RW segment for
extended dynamic metadata. All original LOAD addresses, font mappings, TLS,
RELRO and native function addresses are preserved. No RWX segment is added.
Fifteen UI call sites use a context-preserving adapter dispatcher. A pinned,
straight-line copy of the vendor row constructor creates the additional row
with the same LVGL objects/fonts/styles; its PC-relative instructions and calls
are rebased for ASLR. The mode-list deep-copy and navigation registration stay
native. The extra row has its own persistent callback closure and mode ID (4),
which never reaches the native video-mode API: both Real3D actions use native
mode 3 after selecting their mesh algorithm. Highlight maps mode 3 to the saved
algorithm only for the 3D list; other menus pass through unchanged. Loader or
persistence failure prevents switching the algorithm. No strength call sites
are patched.

## Verification

- `test_onepro_mesh.py`: executes the original vendor instructions; independent
  opposite-eye/chroma oracles, TLS initialization, write bounds, discontinuous
  maps and segmentation equivalence.
- `test_onepro_bridge.py`: executes the compiled bridge and original kernel
  together on a 1920×1080 frame, checking Q3 conversion to half displacement,
  all four jobs, both views and row padding.
- `test_compare_modes.py`: deterministic binary patch, original mappings and
  relocation preservation, dynamic metadata consistency, ARM64 hooks at two
  ASLR bases, unchanged native depth instructions/range/translations.
- `test_compare_ui.py`: executes the compiled ARM64 dispatcher and cloned row;
  verifies the same native LVGL calls/styles, five entries, two callbacks,
  persistent navigation registration and selection highlight. External OSD/LVGL
  services are modeled; this does not replace an on-device menu test.
- `test_lut_arm64.py`: executes the shipped ARM64 table builder and vector
  and scalar loops against a floating-point oracle, including four disjoint
  build partitions, guards, uneven pitches and narrow/odd widths.
- `test_lut.py`: native execution against identity, cross-channel, random and
  packaged Movie/AI HDR cubes, checking error and row padding.
- `test_compare_selection.c`: loader-once behavior, no strength-setter dependency,
  invalid input and five persistence failure cases.
- `test_onepro_rpc.py`: native full adapter and fake HAL; dispatches the old
  path and rejects failures flushing any of its six source/destination planes.
- rc4 also passed 1536 ARM64 geometry cases, 99 invalid descriptors and
  full-frame vendor-kernel tests. rc6 changes only the legacy branch's input
  disparity scaling; the Pro+ CPU warp geometry remains unchanged.
- The installer regression suite is run again for the new package version.

Host tests establish the buffer behavior, not the appearance through the optics.
Physical menu operation, image quality, stereo depth and performance require
comparison on a video source. The full original Pilot 1.9.1 inference/backend
comparison remains unimplemented.

## Reproduce

Use the existing pinned Clang/sysroot and original vendor files:

```powershell
work/installer-venv/Scripts/python.exe firmware/build.py `
  --clang F:/PS4Dev/toolchain/llvm-22.1.3/bin/clang.exe `
  --sysroot work/ai-hdr-lab/device `
  --stock work/pro-plus-ota/pilot-pro-plus.zip `
  --compare-onepro-warp
```

This flag builds only the One Pro package. It cannot be combined with the old
single-view `--capture-real3d` diagnostic, because mesh modifies its source view.
It does not promote the package to a public GitHub release.

## Previous installed candidate and verification evidence

- BIN: `outputs/v0.7.0-rc5/XREAL-one-pro-Pilot2-RU-0.7.0-rc5.bin`
- BIN SHA256: `e5bf66863c172242f56b32bec6700124ac58d874f15ea0b6b6535fdc9e18978c`.
- Exact hashes and build source provenance: `outputs/v0.7.0-rc5/build-report.json`.
- Fresh Pilot backup and USB readback evidence are kept locally under
  `outputs/real3d-dual-rc5/`; this is a Pilot-directory backup, not a partition dump.
  Backup: 98,588,337 bytes, 73 ZIP entries, SHA256
  `90ad0ba8eae32b57633dfdd494358202ede9a46b76522f0ba8422a198c8462ec`.
- Installed once through front USB on One Pro X1112010 on 2026-10-20.
  All 71 files matched on readback. One USB reconnect restarted verification
  without rewriting the package; no rollback. The extra libdl ABI hash matched.
- Physical feedback: overlapping mode rows, displaced ghosting in Real3D,
  and Movie lag. Successful host tests/readback did not establish correct layout.

## rc6 intermediate candidate

- BIN: `outputs/v0.7.0-rc6/XREAL-one-pro-Pilot2-RU-0.7.0-rc6.bin`.
- SHA256: `24c23dc0d85807ca25a5988a906ac68e9c8d8a8c7895c6318bb902f2828ea2ea`.
- Build provenance: `outputs/v0.7.0-rc6/build-report.json`.
- Device logs and installation evidence: `outputs/real3d-rc6/`.
- Physical confirmation of row layout, ghosting and Movie smoothness is pending.

## rc7 candidate

- Includes the rc6 changes and the hidden-SBS layout correction.
- BIN: `outputs/v0.7.0-rc7/XREAL-one-pro-Pilot2-RU-0.7.0-rc7.bin`.
- SHA256: `721e08d0828e116ad18d26168d43a5fc9788ba3ce52e0b34a7c234159b5bde43`.
- Build provenance: `outputs/v0.7.0-rc7/build-report.json`.
- Installation evidence: `outputs/real3d-rc7/`; the pre-session Pilot backup is
  `outputs/real3d-rc6/pilot-before-rc6.zip` (98,590,442 bytes, 73 entries,
  SHA256 `3c01bac48451f9f07ac64907a99a4a04865337f5f7140a93bd8f17aba02624d7`).
- Shipped ARM64 UI, LUT and full-frame legacy bridge tests passed.
- Installed through front USB on One Pro X1112010 on 2026-10-21. All 71 files
  matched on readback (`outputs/real3d-rc7/candidate-install.json`). A USB
  reconnect restarted readback without rewriting the package. rc6 was also
  installed/read back during this session before the hidden-SBS case was fixed;
  the final installed package is rc7. No rollback was performed.
- Physical confirmation is pending.
