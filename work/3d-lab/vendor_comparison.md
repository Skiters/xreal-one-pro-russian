# Real3D: One Pro Pilot 1.9.1 vs Pro+ Pilot 2.0

Local comparison of the original One Pro Pilot
`type4_1.9.1.2026091417562520260914175902.bin` and official Pro+ Pilot
`pilot_pilot_2.0.0.20260920102250.bin`. Neither vendor image was modified for
this comparison.

| Component | One Pro Pilot 1.9.1 | Pro+ Pilot 2.0 |
| --- | --- | --- |
| 2D-to-3D processor | `libnr_depth_manager.so` (1,960,288 bytes) | `libnr_image_post_processor.so` (7,278,656 bytes) |
| Depth estimator | `libnr_mono_depth.so` (15,835,832 bytes) | `libnr_mono_depth.so` (11,949,672 bytes) |
| 3D EDID setting | `synth_stereo: 1920_1080_60` | `synth_stereo: 1920_1080_60` |
| Warp path visible in binary | `DoWarpMeshG4SegmentYUV420_Parallel` and a `ThreadPool` | `ArArmWarp2DTo3DInit/Process/Close` through `AR_MPI_DSP_RemoteCall` |
| DSP userspace API on matching system image | One Pro `libmpi_dsp.so` exports `AR_MPI_DSP_LoadBin` but not `LoadMem` | Pro+ `libmpi_dsp.so` exports both `LoadBin` and `LoadMem` |

Both firmware branches use the NPU (`AR_MPI_NPU_Forward`) for monocular depth.
The main difference in the 2D-to-3D image path is the warp: the older One Pro
module advertises a parallel CPU YUV420 mesh warp, while the Pro+ processor
loads a signed embedded 213,408-byte CEVA program and asks the DSP to warp the
frame. Pilot 2.0's `libnr_flinger.so` consumes its `ImagePostProcessor` interface;
the older Pilot 1.9.1 flinger uses `DepthManager`. Exchanging only these
libraries would leave the interface mismatch unresolved.

The One Pro's current SoC image is `15.1.03.603_20260914`; the Pro+ OTA includes
`15.1.03.643_20261010` and DSP `15.A.01.103_20260710`. An earlier `LoadMem` to
`LoadBin` shim did not produce a working warp on One Pro, so this experiment
intercepts only the three Real3D DSP RPCs and computes their result on the CPU.
This does **not** establish that the two devices have the same usable DSP
configuration.

Device logs while the DP input was 1920x1080 at approximately 60 Hz showed:

| Local CPU implementation | Post-processed frames/s | Average synthesis latency |
| --- | ---: | ---: |
| First per-pixel fallback | ~13 | ~275-390 ms |
| Fast depth-cell fallback | ~32 | ~126-138 ms |
| Two luma workers, chroma on caller | ~48 | ~72 ms |
| Four equal YUV420 depth-row segments | ~53-55 | ~65 ms |

Correction, 2026-10-20: the user clarified that actual stereo depth was not
visible in these earlier builds either. The earlier subjective reports below
do not establish stereo output. See `firmware/REAL3D-INVESTIGATION.md` for the
recovered fractional-displacement contract and remaining eye-routing checks.

The active monocular-depth graph uses the **heavy** NPU model
`gina_ar9481npu_depth_heavy` (`depth_model_type:2`). The depth map is 480x270;
observed values after warm-up ranged about 1-157. The four-segment CPU warp
spends about 1.5-2.5 ms mapping buffers, 9-12 ms warping, and 0.5 ms flushing
cache on measured frames. It creates bounded horizontal disparity but is not a
reconstruction of either vendor warp. The user confirmed a slight depth effect
and a small smoothness improvement. Selecting a 60 Hz EDID does not mean the
post-processor delivers 60 distinct frames/s; 60 remains unverified for the
current device and build.
