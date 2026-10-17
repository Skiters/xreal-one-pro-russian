$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot)
$python = Join-Path (Get-Location) 'work/installer-venv/Scripts/python.exe'
if (-not (Test-Path $python)) { throw 'First create work/installer-venv and install installer/requirements.txt.' }
& $python work/build_one_features.py
if ($LASTEXITCODE) { throw 'Firmware build failed' }
& $python -m unittest discover -s installer -p 'test_*.py' -v
if ($LASTEXITCODE) { throw 'Tests failed' }
& $python -m PyInstaller --noconfirm --onefile --windowed --noupx --name XREAL-Russian-Updater-v0.6.0-test --paths work --paths installer --distpath outputs/xreal-one-real3d-hdr-test --workpath work/installer-build-one-test --specpath work installer/app.py
if ($LASTEXITCODE) { throw 'EXE build failed' }
Write-Output 'EXE and BIN: outputs/xreal-one-real3d-hdr-test/'
