# Local Real3D comparison: rc5

This test adds two selectable **mesh/warp implementations** on One Pro
X1112010 / SoC 15.1.03.603_20260914. It does not transplant the complete
Pilot 1.9.1 depth-inference stack. Both choices retain Pilot 2.0's depth
estimator, model, frame scheduler and display routing.

In the glasses, the **3D mode list** now has two separate entries:

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
Since the two mesh algorithms use different geometry, equal depth input
does not imply equal perceived strength.

rc4 incorrectly reused the strength slider as an algorithm selector. The
user tested it and requested independent mode entries and depth adjustment;
rc5 removes that substitution. This is a UI correction, not a new stereo
quality or performance claim.

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
integer pixel displacement: the bridge truncates the fractional part.
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
  together on a 1920×1080 frame, checking Q3 conversion, all four jobs, both
  views and row padding.
- `test_compare_modes.py`: deterministic binary patch, original mappings and
  relocation preservation, dynamic metadata consistency, ARM64 hooks at two
  ASLR bases, unchanged native depth instructions/range/translations.
- `test_compare_ui.py`: executes the compiled ARM64 dispatcher and cloned row;
  verifies the same native LVGL calls/styles, five entries, two callbacks,
  persistent navigation registration and selection highlight. External OSD/LVGL
  services are modeled; this does not replace an on-device menu test.
- `test_compare_selection.c`: loader-once behavior, no strength-setter dependency,
  invalid input and five persistence failure cases.
- `test_onepro_rpc.py`: native full adapter and fake HAL; dispatches the old
  path and rejects failures flushing any of its six source/destination planes.
- rc4 also passed 1536 ARM64 geometry cases, 99 invalid descriptors and
  full-frame vendor-kernel tests. The warp itself is unchanged in rc5.
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

## Installed candidate and verification evidence

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
- Physical confirmation of two separately selectable modes and usable depth
  control is pending. Successful host tests/readback are not visual confirmation.
