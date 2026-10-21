# Windows updater 0.7.1

Portable EXE; separate model-specific BINs. End-user instructions: [project README](../README.md).

## Build

Pinned dependencies: `requirements.txt`. Python 3.13 x64 and PyInstaller 6.22.3 were used. In the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File installer/build.ps1 -FirmwareDirectory outputs/v0.7.0 -FirmwareVersion 0.7.0
```

The directory must contain `XREAL-one-pro-Pilot2-RU-0.7.0.bin`, `XREAL-one-Pilot2-RU-0.7.0.bin`, `XREAL-1S-RU-Pilot1.9.1.bin`, and `build-report.json`. The approved translation-only One / One Pro BIN is also copied from `outputs/xreal-russian-firmware/` as `XREAL-One-OnePro-RU-Pilot1.9.1.bin`. Output: `outputs/release-0.7.1`. The EXE contains no firmware, credentials or network reporting. Release BINs are distributed as assets; vendor inputs and device backups remain local.

```powershell
python -m unittest discover -s installer -p 'test_*.py' -v
XREAL-Russian-Updater-0.7.1.exe --self-test report.json --bin selected.bin
```

`--self-test` validates the package and constructs Tk without firmware writes. `--check-device report.json --bin selected.bin` reads model, versions and ABI. `--verify-device` additionally reads installed files. These switches never flash. CLI failures write their report and return nonzero.

## Diagnostic support

GUI **Диагностика / отчёт об ошибке** shows a readable support report. Users can inspect, copy or save it as TXT. **Проверить сейчас** enumerates devices and issues version queries only; with a BIN it also validates the container, identity and required ABI by reading. It never calls `transmit` or update commands 4628/4629/4630. No serial numbers, USB paths, local file paths or machine/user names are included. No network transmission occurs.

Error reports contain `XR-<operation>-<stage>`, the original sanitized error, updater version, model/Pilot/SoC when available, BIN SHA256, and build ID. Codes identify stages, not uniquely every possible cause; the full report should accompany an issue. The latest operation is preserved when diagnostic checks follow it. Local detailed reports and stack traces remain under `%LOCALAPPDATA%/XREAL-Russian-Updater`; the shareable operation report is `support-report.txt`.

The app disables flashing while diagnostics are running. Choosing a BIN, listing devices, opening a diagnostic dialog, or starting the EXE never starts a firmware update. Device selection is bound to the exact HID path and serial internally; neither is exported in the support report. Unknown firmware targets, unsupported SoC/Pilot/SKU and mismatched CPU ABI are rejected before writing. A positive HID write alone is not success; protocol acknowledgement and full readback are required.

0.7.1 expands the Pilot 2.0 One Pro allowlist to X1112011, including post-update verification and return to translation-only Pilot 1.9.1. Firmware payloads are unchanged. SoC/Pilot/PID, ABI and MMZ guards remain enforced. Physical Pilot 2.0 validation on X1112011 is pending. The existing v0.7.0 release hosts the updated EXE alongside the unchanged BINs.
