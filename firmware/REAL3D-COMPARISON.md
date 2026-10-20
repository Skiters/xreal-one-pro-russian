# Local Real3D comparison: rc4

This test adds two selectable **mesh/warp implementations** on One Pro
X1112010 / SoC 15.1.03.603_20260914. It does not transplant the complete
Pilot 1.9.1 depth-inference stack. Both choices retain Pilot 2.0's depth
estimator, model, frame scheduler and display routing.

In the glasses, open Real3D and **Алгоритм Real3D**:

- **One Pro (mesh)** executes the unchanged ARM64 mesh kernel from the
  original One Pro Pilot 1.9.1 `libnr_depth_manager.so`.
- **Pro+ (CPU-порт)** retains the current custom CPU implementation used by
  our Pilot 2 port. It is not the native Pro+ CEVA DSP implementation.

For this comparison only, the existing four-level strength slider becomes
this two-position algorithm selector. Choosing either option sets the
vendor depth-strength property to Natural (2). Other state-machine reads
retain the native property; four UI reads use the separate selector.
The selection is saved atomically in `/usrdata/real3d-algorithm`.
The default without a saved selection is the current Pro+ CPU port.
Since the two mesh algorithms use different geometry, equal depth input
does not imply equal perceived strength.

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
Four getter call sites and one setter call site resolve to adapter exports.
The new setter calls the original vendor setter with Natural before saving
the independent algorithm selection. Loader failure prevents selecting mesh.

## Verification

- `test_onepro_mesh.py`: executes the original vendor instructions; independent
  opposite-eye/chroma oracles, TLS initialization, write bounds, discontinuous
  maps and segmentation equivalence.
- `test_onepro_bridge.py`: executes the compiled bridge and original kernel
  together on a 1920×1080 frame, checking Q3 conversion, all four jobs, both
  views and row padding.
- `test_compare_modes.py`: deterministic binary patch, original mappings and
  relocation preservation, dynamic metadata consistency, ARM64 hooks at two
  ASLR bases, two menu choices and Russian/English labels.
- `test_compare_selection.c`: loader-once behavior, common Natural setting,
  invalid input and five persistence failure cases.
- `test_onepro_rpc.py`: native full adapter and fake HAL; dispatches the old
  path and rejects failures flushing any of its six source/destination planes.
- Existing 1536 ARM64 geometry cases, 99 invalid descriptors and 37 installer
  tests pass.

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

## Installed candidate

Installed through the front USB on One Pro X1112010 on 2026-10-20.
All 71 package files were exported back and matched. One readback reconnect
was required after reboot; the package was written only once. No rollback.
An additional preflight read verified `/lib/libdl-2.25.so` against `DL_SHA`.

- BIN: `outputs/v0.7.0-rc4/XREAL-one-pro-Pilot2-RU-0.7.0-rc4.bin`
- SHA256: `9a9dba4165788be531668a19d0730fa16ba79ce43c3e374df6ba5e1d61f08170`
- Before writing, a fresh complete `/usrdata/pilot` backup was checked:
  97,788,865 bytes, 72 ZIP entries, SHA256
  `0a9f92d5a9b22d7c14579e464bcd313b84158dfbb1c12e3bba928d96f1915c1a`.
- Local installation evidence: `outputs/real3d-dual/candidate-install.json`.
- Visual A/B feedback is pending; successful readback is not a stereo test.
