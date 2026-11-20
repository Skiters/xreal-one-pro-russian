# Building XREAL Updater

Windows release: 0.7.2. The published universal Mac DMG is version 0.7.1 (build 2) for macOS 13 and later. Building the current sources produces version 0.7.2.

Portable EXE; separate model-specific BINs. End-user instructions: [project README](../README.md).

## Build

Pinned dependencies: `requirements.txt`. Python 3.13 x64 and PyInstaller 6.22.3 were used. In the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File installer/build.ps1 -FirmwareDirectory outputs/v0.7.0 -FirmwareVersion 0.7.0
```

The directory must contain `XREAL-one-pro-Pilot2-RU-0.7.0.bin`, `XREAL-one-Pilot2-RU-0.7.0.bin`, `XREAL-1S-RU-Pilot1.9.1.bin`, and `build-report.json`. The approved translation-only One / One Pro BIN is also copied from `outputs/xreal-russian-firmware/` as `XREAL-One-OnePro-RU-Pilot1.9.1.bin`. Output: `outputs/release-0.7.2`. The EXE contains no firmware, credentials or network reporting. Release BINs are distributed as assets; vendor inputs and device backups remain local.

```powershell
python -m unittest discover -s installer -p 'test_*.py' -v
XREAL-Russian-Updater-0.7.2.exe --self-test report.json --bin selected.bin
```

`--self-test` validates the package and constructs Tk without firmware writes. `--check-device report.json --bin selected.bin` reads model, versions and ABI. `--verify-device` additionally reads installed files. These switches never flash. CLI failures write their report and return nonzero.

## Diagnostic support

GUI **Диагностика / отчёт об ошибке** shows a readable support report. Users can inspect, copy or save it as TXT. **Проверить сейчас** enumerates devices and issues version queries only; with a BIN it also validates the container, identity and required ABI by reading. It never calls `transmit` or update commands 4628/4629/4630. No serial numbers, USB paths, local file paths or machine/user names are included. No network transmission occurs.

Error reports contain `XR-<operation>-<stage>`, the original sanitized error, updater version, model/Pilot/SoC when available, BIN SHA256, and build ID. Codes identify stages, not uniquely every possible cause; the full report should accompany an issue. The latest operation is preserved when diagnostic checks follow it. Local detailed reports and stack traces remain under `%LOCALAPPDATA%/XREAL-Russian-Updater`; the shareable operation report is `support-report.txt`.

The app disables flashing while diagnostics are running. Choosing a BIN, listing devices, opening a diagnostic dialog, or starting the EXE never starts a firmware update. Device selection is bound to the exact HID path and serial internally; neither is exported in the support report. Unknown firmware targets, unsupported SoC/Pilot/SKU and mismatched CPU ABI are rejected before writing. A positive HID write alone is not success; protocol acknowledgement and full readback are required.

## Device dump

The Windows updater opens the same read-only collector used by the standalone Windows application. The native Mac app exposes this operation through its local worker. See [dump contents and supported interfaces](DUMP-README.md).

## Universal build for macOS 13 and later

On an Apple Silicon build host with Xcode, Python 3 and uv, use a new environment directory and output directory:

```sh
python3 mac/prepare_universal.py work/universal-env
PYTHON="$PWD/work/universal-env/env/bin/python" bash mac/build.sh dist-universal universal2
```

This builds the current branch for Intel and Apple Silicon. The build checks each Mach-O file for both architectures and compatible deployment targets. To create a disk image, add an Applications shortcut and package the output directory:

```sh
ln -s /Applications dist-universal/Applications
hdiutil create -volname "XREAL Updater" -srcfolder dist-universal -format UDZO XREAL-Updater-Universal.dmg
hdiutil verify XREAL-Updater-Universal.dmg
```

## Apple Silicon build for macOS 12 and later

On an Apple Silicon Mac with the macOS SDK, Swift and Python 3.12 with Tk, run from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r installer/requirements.txt
PYTHON="$PWD/.venv/bin/python" bash mac/build.sh dist
```

The resulting application is `dist/XREAL Updater.app`. The macOS GitHub Actions workflow packages it in a DMG, mounts the image, verifies the ad-hoc signature and runs the bundled self-test.
