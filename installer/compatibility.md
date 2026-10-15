# One Pro SKU compatibility

The Windows updater allows exactly `X1112010` and `X1112011`, with USB VID/PID `3318:0436`, interface 0, usage page `ff00`, SoC `15.1.03.603_20260914` and Pilot `1.9.1.20260914175625`. Other SKUs and firmware versions remain rejected.

## Evidence reviewed on 2026-10-15

The official XREAL OTA JavaScript saved during this project's original investigation uses these mappings:

- `Support-oYWAiGUb.js`: USB `3318:0436` maps to `one_pro`, kernel partition; the One Pro profile uses `hardwareCode:6`.
- `ota-CrMFRFH8.js`: `Ue()` requests the file list via `Mr(appName, hardwareCode, version)`. That request does not include the SKU read by command 204. In the inspected client, One Pro package selection is by the USB hardware profile, not by `X1112010` versus `X1112011`.
- The reported `X1112011` device passed USB enumeration and returned the same SoC/Pilot versions as the existing supported device; only our single-SKU comparison blocked it before writing.

This justifies extending the installer allowlist to the second observed One Pro SKU without changing the firmware payload. It does not establish physical test coverage: successful installation and visual verification have been reported for `X1112010`; physical installation on `X1112011` is still awaiting user feedback.

Official updater: https://www.xreal.com/ota/

Regression tests cover the second SKU through the simulated update/read-back flow and continue to reject unknown SKUs and wrong firmware versions before any flash command.
