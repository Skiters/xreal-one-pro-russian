$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot)
$python = Join-Path (Get-Location) 'work/installer-venv/Scripts/python.exe'
if (-not (Test-Path $python)) {
    python -m venv work/installer-venv
    if ($LASTEXITCODE) { throw 'venv failed' }
}
& $python -m pip install -r installer/requirements.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
& $python -m unittest discover -s installer -p 'test_*.py' -v
if ($LASTEXITCODE) { throw 'Tests failed' }
$firmware = (Resolve-Path 'outputs/xreal-russian-firmware/XREAL_One_Pro_Pilot_RU.bin').Path
$experimental = (Resolve-Path 'outputs/xreal-pilot2-hebe/XREAL-Pilot2-RU-Hebe-Within-Limit.bin').Path
& $python -m PyInstaller --noconfirm --onefile --windowed --noupx --name XREAL-Russian-Updater --paths work --paths installer --add-data "${firmware}:." --add-data "${experimental}:." --distpath outputs/xreal-windows-installer --workpath work/installer-build --specpath work installer/app.py
if ($LASTEXITCODE) { throw 'Build failed' }
